"""KIRO push: automatic and manual Telegram greetings with gifts (birthday, anniversary, holidays, one-offs).

Customer API:  /api/plugins/kiro-push/*   (session user, via the Mini App host SDK): own birthdate, set once
Admin API:     /api/admin/kiro-push/*     (Core admin middleware resolves the role)
A periodic durable job (every 60 s) plans due events, scans Telegram for birthdates and sends greetings.
Birthdates come from Telegram (getChat) or from the user himself, exactly once; a stored date is locked.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from aiohttp import web
from sqlalchemy import text

from bot.app.web.context import get_session_factory
from bot.app.web.session import extract_authenticated_user_id
from bot.infra.events import ACCOUNT_TELEGRAM_LINKED, USER_REGISTERED, subscribe
from bot.plugins.extensions.contracts import ExtensionContributions, JobHandler, OperationContext
from bot.plugins.spec import WEB_SCOPE_WEBAPP, Plugin, PluginContext

from . import backfill, birthdates, diag, eventdefs, gifts, media, scheduler, stats, templates
from .errors import PushError
from .storage import (
    MIGRATIONS, PLUGIN_ID, clean_config, get_meta, jload, load_config, save_config, set_meta,
)
from .telegram import BotGateway, Gateway, SendResult
from .users import SEGMENT_LABELS, SEGMENTS, resolve_user

logger = logging.getLogger(__name__)

VERSION = "0.1.0"
USER = f"/api/plugins/{PLUGIN_ID}"
ADMIN = f"/api/admin/{PLUGIN_ID}"
RUNTIME: dict[str, Any] = {}
_TASKS: set[asyncio.Task] = set()
EVENT_SCAN_DELAY = 4.0  # seconds to let Core commit the new user before looking its birthdate up
EMOJI_API = {
    "library": "/api/admin/telegram-emoji/library",
    "catalog": "/api/admin/telegram-emoji/catalog",
    "previews": "/api/admin/telegram-emoji/previews",
    "media": "/api/admin/telegram-emoji/media/{emoji_id}",
}


def _ok(payload: dict[str, Any] | None = None, status: int = 200) -> web.Response:
    return web.json_response(
        {"ok": True, **(payload or {})}, status=status, headers={"Cache-Control": "no-store"},
        dumps=lambda v: json.dumps(v, ensure_ascii=False, default=str),
    )


def _err(code: str, status: int = 400, **extra: Any) -> web.Response:
    return web.json_response(
        {"ok": False, "error": code, **extra}, status=status, dumps=lambda v: json.dumps(v, ensure_ascii=False, default=str)
    )


async def _json_body(request: web.Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise PushError("invalid_json") from None
    if not isinstance(body, dict):
        raise PushError("invalid_json")
    return body


def _safe_user(request: web.Request) -> int | None:
    try:
        return int(extract_authenticated_user_id(request) or 0) or None
    except Exception:  # noqa: BLE001
        return None


async def _log(request: web.Request, level: str, event: str, user_id: int | None = None, status: int | None = None, **detail: Any) -> None:
    await diag.record(
        get_session_factory(request), level, event,
        user_id=user_id if user_id is not None else _safe_user(request),
        path=f"{request.method} {request.path}", status=status, detail=detail,
    )


def _guard(handler, *, admin: bool = True):
    async def wrapped(request: web.Request) -> web.Response:
        try:
            if admin and not request.get("admin_authorized", False):
                raise PushError("forbidden", 403)
            return await handler(request)
        except PushError as exc:
            level = "error" if exc.status >= 500 else "warn" if exc.status in (401, 403, 429) else "info"
            await _log(request, level, "request_failed", status=exc.status, error_code=exc.code)
            return _err(exc.code, exc.status, **exc.extra)
        except web.HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("kiro-push: %s %s failed", request.method, request.path)
            await _log(request, "error", "internal_error", status=500, error=repr(exc)[:300], trace=traceback.format_exc()[-3000:])
            return _err("internal_error", 500)

    return wrapped


def _ctx() -> PluginContext:
    ctx = RUNTIME.get("ctx")
    if ctx is None:
        raise PushError("not_ready", 503)
    return ctx


def make_gateway(ctx: Any) -> Gateway | None:
    """The Telegram gateway; tests install their own through RUNTIME['gateway']."""
    if RUNTIME.get("gateway") is not None:
        return RUNTIME["gateway"]
    bot = getattr(ctx, "bot", None)
    if bot is None:
        return None
    return BotGateway(bot, ctx.settings, getattr(ctx, "i18n", None))


def _currency(settings: Any) -> str:
    return (str(getattr(settings, "USER_BALANCE_CURRENCY", "") or getattr(settings, "DEFAULT_CURRENCY_SYMBOL", "") or "RUB")).upper()


def _tariff_titles(settings: Any) -> dict[str, str]:
    try:
        out = {}
        for tariff in getattr(settings.tariffs_config, "tariffs", []):
            names = getattr(tariff, "names", None) or {}
            out[str(tariff.key)] = str(names.get("ru") or names.get("en") or next(iter(names.values()), "") or tariff.key)
        return out
    except Exception:  # noqa: BLE001
        return {}


def _spawn(coro: Any) -> asyncio.Task:
    task = asyncio.ensure_future(coro)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


def _actor(request: web.Request) -> str:
    tg = request.get("admin_telegram_id")
    return f"admin:{tg}" if tg else "admin"


# ---------------------------------------------------------------- customer


def _user_id(request: web.Request) -> int:
    user_id = extract_authenticated_user_id(request)
    if not user_id:
        raise PushError("unauthorized", 401)
    return int(user_id)


async def user_birthdate(request: web.Request) -> web.Response:
    user_id = _user_id(request)
    async with get_session_factory(request)() as session:
        config = await load_config(session)
        state = await birthdates.customer_state(session, user_id, config)
    return _ok(state)


async def user_birthdate_set(request: web.Request) -> web.Response:
    user_id = _user_id(request)  # the only identity ever written: the session's own
    body = await _json_body(request)
    factory = get_session_factory(request)
    async with factory() as session:
        config = await load_config(session)
    state = await birthdates.user_set(factory, user_id, body, config)
    await _log(request, "info", "birthdate_set_by_user", user_id, 200, has_year=bool(state.get("year")))
    return _ok(state)


# ---------------------------------------------------------------- admin: overview / config


def _capabilities(wheel: bool) -> dict[str, Any]:
    return {
        "event_kinds": [{"id": k, "label": eventdefs.KIND_LABELS[k]} for k in eventdefs.KINDS],
        "gift_kinds": [
            {"id": "none", "label": "Без подарка", "available": True},
            {"id": "traffic", "label": "Трафик (ГБ)", "available": True, "needs_subscription": True},
            {"id": "days", "label": "Дни подписки", "available": True, "needs_subscription": True},
            {"id": "balance", "label": "Баланс", "available": True},
            {"id": "promo_discount", "label": "Персональный промокод со скидкой", "available": True},
            {"id": "wheel_spins", "label": "Билеты колеса удачи", "available": wheel},
        ],
        "gift_fallbacks": [
            {"id": "send_without_gift", "label": "Отправить поздравление без подарка"},
            {"id": "skip", "label": "Не отправлять"},
        ],
        "feb29_rules": [{"id": "feb28", "label": "28 февраля"}, {"id": "mar1", "label": "1 марта"}],
        "segments": [{"id": s, "label": SEGMENT_LABELS[s]} for s in SEGMENTS],
        "languages": ["ru", "en"],
        "media_kinds": list(templates.MEDIA_KINDS),
        "button_kinds": [
            {"id": "url", "label": "Ссылка"}, {"id": "web_app", "label": "Mini App (https)"},
            {"id": "section", "label": "Раздел Mini App магазина"},
        ],
        "button_styles": list(templates.BUTTON_STYLES),
        "sections": list(templates.SECTIONS),
        "placeholders": [{"name": n, "description": d, "sample": s} for n, (d, s) in templates.PLACEHOLDERS.items()],
        "limits": {
            "text": templates.MAX_TEXT, "caption": templates.MAX_CAPTION, "media": templates.MAX_MEDIA,
            "button_rows": templates.MAX_BUTTON_ROWS, "buttons_per_row": templates.MAX_BUTTONS_PER_ROW,
            "button_label": templates.MAX_LABEL, "media_bytes": media.MEDIA_LIMITS, "media_chunk": media.MAX_CHUNK,
        },
        "allowed_html_tags": sorted(templates.ALLOWED_TAGS),
        "optional_block_syntax": "[[ текст с {age} ]]",
        "emoji_library": EMOJI_API,
    }


async def admin_overview(request: web.Request) -> web.Response:
    ctx = RUNTIME.get("ctx")
    async with get_session_factory(request)() as session:
        config = await load_config(session)
        wheel = await gifts.wheel_installed(session)
        today = datetime.now(UTC).astimezone(ZoneInfo(config["timezone"])).date()
        payload = {
            "version": VERSION,
            "config": config,
            "wheel_installed": wheel,
            "bot_available": bool(ctx is not None and (RUNTIME.get("gateway") or getattr(ctx, "bot", None))),
            "birthdates": await stats.birthdate_stats(session, today),
            "upcoming": await stats.upcoming_birthdays(session, today),
            "deliveries": await stats.delivery_stats(session),
            "backfill": await backfill.status(session),
            "events_total": int(await session.scalar(text("select count(*) from ext_kiro_push_events where deleted_at is null")) or 0),
            "currency": _currency(ctx.settings if ctx is not None else None),  # for the gift editor (amount in major units)
            "capabilities": _capabilities(wheel),
        }
    return _ok(payload)


async def admin_save_config(request: web.Request) -> web.Response:
    body = await _json_body(request)
    async with get_session_factory(request)() as session:
        before = await load_config(session)
        data = clean_config(body, before)
        await save_config(session, data)
        await session.commit()
    diag.set_debug(bool(data.get("debug_log")))
    changed = sorted(k for k in data if data[k] != before.get(k))
    await _log(request, "info", "admin_config_saved", status=200, changed=changed)
    return _ok({"config": data})


# ---------------------------------------------------------------- admin: events


async def _event_row(session: Any, event_id: int) -> dict[str, Any]:
    row = (
        await session.execute(text("select * from ext_kiro_push_events where id = :i and deleted_at is null"), {"i": event_id})
    ).mappings().first()
    if row is None:
        raise PushError("event_not_found", 404)
    return dict(row)


def _event_id(request: web.Request) -> int:
    try:
        return int(request.match_info["event_id"])
    except (KeyError, ValueError):
        raise PushError("event_not_found", 404) from None


async def admin_events(request: web.Request) -> web.Response:
    async with get_session_factory(request)() as session:
        rows = (await session.execute(text("select * from ext_kiro_push_events where deleted_at is null order by id"))).mappings().all()
        delivery = await stats.delivery_stats(session)
    events = []
    for r in rows:
        item = eventdefs.public_event(dict(r))
        item["stats"] = delivery["events"].get(str(r["id"]))
        events.append(item)
    return _ok({"events": events})


async def admin_event_get(request: web.Request) -> web.Response:
    async with get_session_factory(request)() as session:
        row = await _event_row(session, _event_id(request))
    return _ok({"event": eventdefs.public_event(row)})


def _event_params(clean: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": clean["name"], "kind": clean["kind"], "enabled": clean["enabled"],
        "params": json.dumps(clean["params"]), "audience": json.dumps(clean["audience"]),
        "send_time": clean["send_time"], "feb29": clean["feb29"], "gift": json.dumps(clean["gift"], ensure_ascii=False),
        "fallback": clean["gift_fallback"], "templates": json.dumps(clean["templates"], ensure_ascii=False),
    }


async def admin_event_create(request: web.Request) -> web.Response:
    body = await _json_body(request)
    async with get_session_factory(request)() as session:
        clean = eventdefs.clean_event(body, wheel_installed=await gifts.wheel_installed(session))
        event_id = await session.scalar(
            text(
                "insert into ext_kiro_push_events (name, kind, enabled, params, audience, send_time, feb29, gift, "
                "gift_fallback, templates) values (:name, :kind, :enabled, cast(:params as jsonb), cast(:audience as jsonb), "
                ":send_time, :feb29, cast(:gift as jsonb), :fallback, cast(:templates as jsonb)) returning id"
            ),
            _event_params(clean),
        )
        await session.commit()
    await _log(request, "info", "admin_event_created", status=200, event_id=event_id, kind=clean["kind"])
    return _ok({"id": event_id, "warnings": clean["warnings"]}, 201)


async def admin_event_update(request: web.Request) -> web.Response:
    event_id = _event_id(request)
    body = await _json_body(request)
    async with get_session_factory(request)() as session:
        await _event_row(session, event_id)
        clean = eventdefs.clean_event(body, wheel_installed=await gifts.wheel_installed(session))
        await session.execute(
            text(
                "update ext_kiro_push_events set name = :name, kind = :kind, enabled = :enabled, params = cast(:params as jsonb), "
                "audience = cast(:audience as jsonb), send_time = :send_time, feb29 = :feb29, gift = cast(:gift as jsonb), "
                "gift_fallback = :fallback, templates = cast(:templates as jsonb), updated_at = now() where id = :id"
            ),
            {**_event_params(clean), "id": event_id},
        )
        await session.commit()
    await _log(request, "info", "admin_event_updated", status=200, event_id=event_id, enabled=clean["enabled"])
    return _ok({"id": event_id, "warnings": clean["warnings"]})


async def admin_event_delete(request: web.Request) -> web.Response:
    event_id = _event_id(request)
    async with get_session_factory(request)() as session:
        await _event_row(session, event_id)
        await session.execute(
            text("update ext_kiro_push_events set deleted_at = now(), enabled = false where id = :i"), {"i": event_id}
        )
        await session.commit()
    await _log(request, "info", "admin_event_deleted", status=200, event_id=event_id)
    return _ok()


SAMPLE_PROMO = "PUSH-AB12CD"


async def _preview_values(
    session: Any, config: dict[str, Any], event: dict[str, Any], sample: Any, currency: str, gift_available: bool
) -> dict[str, str | None]:
    values: dict[str, str | None] = {n: s for n, (_d, s) in templates.PLACEHOLDERS.items()}
    gift = event["gift"]
    values["gift"] = gifts.gift_text(gift, currency) or values["gift"]
    values["promo_discount"] = gifts.discount_text(gift) or values["promo_discount"]
    if isinstance(sample, str) and sample.strip():
        user = await resolve_user(session, sample)
        if user is None:
            raise PushError("user_not_found", 404)
        values = await scheduler.build_values(
            session, user, {"base_year": datetime.now(UTC).year}, {**event, "gift": gift}, gift_available=gift_available,
            promo_code=SAMPLE_PROMO, config=config, currency=currency, tariff_titles={},
            today=datetime.now(UTC).astimezone(ZoneInfo(config["timezone"])).date(),
        )
    elif isinstance(sample, dict):
        for name in templates.PLACEHOLDERS:
            if name in sample:
                values[name] = "" if sample[name] is None else str(sample[name])
    return values


async def _render_preview(
    session: Any, config: dict[str, Any], event: dict[str, Any], body: dict[str, Any], currency: str
) -> dict[str, Any]:
    language = str(body.get("language") or "ru").lower()
    if language not in ("ru", "en"):
        raise PushError("invalid_language")
    tpl = event["templates"].get(language)
    if tpl is None or templates.is_empty(tpl):
        raise PushError("template_missing", 404)
    gift_available = bool(body.get("with_gift", True)) and event["gift"]["kind"] != "none"
    values = await _preview_values(session, config, event, body.get("sample_user"), currency, gift_available)
    rendered = templates.render_message(tpl, values, gift_available=gift_available, language=language)
    errors, warnings = templates.validate_template(tpl, gift_kind=event["gift"]["kind"])
    warnings += templates.preview_warnings(tpl, event)
    return {"rendered": rendered.to_dict(), "errors": errors, "warnings": warnings, "gift_text": gifts.gift_text(event["gift"], currency)}


def _event_for_render(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "gift": jload(row["gift"]) or {"kind": "none"}, "templates": jload(row["templates"]) or {},
            "params": jload(row["params"]) or {}}


async def admin_event_preview(request: web.Request) -> web.Response:
    body = await _json_body(request)
    async with get_session_factory(request)() as session:
        config = await load_config(session)
        event = _event_for_render(await _event_row(session, _event_id(request)))
        data = await _render_preview(session, config, event, body, _currency(_ctx().settings if RUNTIME.get("ctx") else None))
    return _ok(data)


async def admin_preview_draft(request: web.Request) -> web.Response:
    """Preview an unsaved event: the body carries the event (same shape as POST /events) plus language / sample_user."""
    body = await _json_body(request)
    draft = body.get("event")
    if not isinstance(draft, dict):
        raise PushError("event_required")
    async with get_session_factory(request)() as session:
        config = await load_config(session)
        draft = {**draft, "name": draft.get("name") or "draft", "kind": draft.get("kind") or "manual"}
        try:
            clean = eventdefs.clean_event(draft, wheel_installed=await gifts.wheel_installed(session))
        except PushError as exc:
            if exc.code != "invalid_event":
                raise
            return _ok({"rendered": None, "errors": exc.extra.get("issues", []), "warnings": exc.extra.get("warnings", [])})
        event = {**clean, "id": 0}
        data = await _render_preview(session, config, event, body, _currency(RUNTIME["ctx"].settings if RUNTIME.get("ctx") else None))
    return _ok(data)


async def admin_event_test(request: web.Request) -> web.Response:
    """Send the template to the requesting admin only. The gift is NOT granted; its text is just shown."""
    admin_tg = request.get("admin_telegram_id")
    if not admin_tg:
        raise PushError("admin_telegram_unavailable", 403)
    body = await _json_body(request)
    ctx = _ctx()
    gateway = make_gateway(ctx)
    if gateway is None:
        raise PushError("bot_unavailable", 503)
    async with get_session_factory(request)() as session:
        config = await load_config(session)
        event = _event_for_render(await _event_row(session, _event_id(request)))
        admin_user = await resolve_user(session, str(admin_tg))
        sample = str(admin_user["user_id"]) if admin_user else body.get("sample_user")
        data = await _render_preview(session, config, event, {**body, "sample_user": sample}, _currency(ctx.settings))
        language = data["rendered"]["language"]
        rendered = templates.render_message(
            event["templates"][language], await _preview_values(session, config, event, sample, _currency(ctx.settings), bool(body.get("with_gift", True)) and event["gift"]["kind"] != "none"),
            gift_available=bool(body.get("with_gift", True)) and event["gift"]["kind"] != "none", language=language,
        )
        result = await gateway.send(session, chat_id=int(admin_tg), user_id=int(admin_user["user_id"]) if admin_user else int(admin_tg), message=rendered)
        await session.commit()
    if result.status != "sent":
        return _err("test_failed", 502, detail=result.error)
    await _log(request, "info", "admin_event_test", status=200, event_id=event["id"], language=language)
    return _ok({"message_id": result.message_id})


async def admin_event_run(request: web.Request) -> web.Response:
    event_id = _event_id(request)
    body = await _json_body(request)
    factory = get_session_factory(request)
    async with factory() as session:
        config = await load_config(session)
        event = await _event_row(session, event_id)
        if not jload(event["templates"]):
            raise PushError("templates_required")
    result = await scheduler.run_manual(factory, event, config, body)
    if not result["dry_run"]:
        await _log(request, "info", "admin_event_run", status=200, event_id=event_id, batch_id=result["batch_id"], matched=result["matched"], queued=result["queued"])
    return _ok(result)


# ---------------------------------------------------------------- admin: media


async def admin_media_init(request: web.Request) -> web.Response:
    body = await _json_body(request)
    async with get_session_factory(request)() as session:
        data = await media.init(session, body)
        await session.commit()
    return _ok(data)


async def admin_media_chunk(request: web.Request) -> web.Response:
    media_id = request.match_info["media_id"]
    try:
        offset = int(request.headers.get("X-Offset", "-1"))
    except ValueError:
        raise PushError("invalid_chunk") from None
    data = await request.read()
    async with get_session_factory(request)() as session:
        result = await media.chunk(session, media_id, offset, data)
        await session.commit()
    return _ok(result)


async def admin_media_finish(request: web.Request) -> web.Response:
    async with get_session_factory(request)() as session:
        result = await media.finish(session, request.match_info["media_id"])
        await session.commit()
    return _ok(result)


async def admin_media_list(request: web.Request) -> web.Response:
    async with get_session_factory(request)() as session:
        return _ok({"media": await media.listing(session)})


async def admin_media_get(request: web.Request) -> web.Response:
    media_id = request.match_info["media_id"]
    if not media_id.isalnum():
        raise PushError("not_found", 404)
    async with get_session_factory(request)() as session:
        row = (await session.execute(text("select content_type, body from ext_kiro_push_media where id = :i and complete"), {"i": media_id})).first()
    if row is None:
        raise PushError("not_found", 404)
    return web.Response(body=bytes(row[1]), content_type=row[0] or "application/octet-stream", headers={"Cache-Control": "private, max-age=3600"})


async def admin_media_delete(request: web.Request) -> web.Response:
    media_id = request.match_info["media_id"]
    async with get_session_factory(request)() as session:
        if await media.referenced(session, media_id):
            raise PushError("media_in_use", 409)
        await session.execute(text("delete from ext_kiro_push_media where id = :i"), {"i": media_id})
        await session.commit()
    return _ok()


# ---------------------------------------------------------------- admin: birthdates


def _page(request: web.Request) -> tuple[int, int, int]:
    try:
        page = max(1, int(request.query.get("page") or 1))
        per = max(1, min(200, int(request.query.get("per_page") or 50)))
    except ValueError:
        raise PushError("invalid_page") from None
    return page, per, (page - 1) * per


async def admin_birthdates(request: web.Request) -> web.Response:
    page, per, offset = _page(request)
    q = (request.query.get("q") or "").strip()
    status = request.query.get("status") or ("all" if q else "set")
    source = request.query.get("source") or ""
    month = request.query.get("month") or ""
    where = ["1 = 1"]
    params: dict[str, Any] = {}
    if q:
        stripped = q.lstrip("@")
        if stripped.lstrip("-").isdigit() and abs(int(stripped)) < 2**63:
            where.append("(u.user_id = :n or u.telegram_id = :n)")
            params["n"] = int(stripped)
        else:
            where.append("(lower(u.username) like :like or lower(u.first_name) like :like)")
            params["like"] = f"%{stripped.lower()}%"
    if status == "set":
        where.append("b.day is not null")
    elif status == "unknown":
        where.append("b.user_id is null")
    elif status in ("absent", "blocked", "error", "cleared"):
        where.append("b.status = :st")
        params["st"] = status
    elif status != "all":
        raise PushError("invalid_status")
    if source in ("telegram", "user", "admin"):
        where.append("b.source = :src")
        params["src"] = source
    if month:
        try:
            params["month"] = int(month)
        except ValueError:
            raise PushError("invalid_month") from None
        where.append("b.month = :month")
    sql_from = "from users u left join ext_kiro_push_birthdates b on b.user_id = u.user_id where u.telegram_id is not null and " + " and ".join(where)
    async with get_session_factory(request)() as session:
        total = int(await session.scalar(text(f"select count(*) {sql_from}"), params) or 0)
        rows = (
            await session.execute(
                text(
                    "select u.user_id, u.telegram_id, u.username, u.first_name, b.day, b.month, b.year, b.source, b.locked, "
                    f"b.status, b.attempts, b.last_attempt_at, b.fetched_at {sql_from} order by u.user_id desc limit :lim offset :off"
                ),
                {**params, "lim": per, "off": offset},
            )
        ).mappings().all()
    return _ok({"total": total, "page": page, "per_page": per, "items": [birthdates.public_row(dict(r)) for r in rows]})


async def _target_user(session: Any, ref: str) -> dict[str, Any]:
    user = await resolve_user(session, ref)
    if user is None:
        raise PushError("user_not_found", 404)
    return user


async def admin_birthdate_put(request: web.Request) -> web.Response:
    body = await _json_body(request)
    async with get_session_factory(request)() as session:
        user = await _target_user(session, request.match_info["user_ref"])
        saved = await birthdates.admin_set(session, int(user["user_id"]), body, _actor(request))
        await session.commit()
    await _log(request, "info", "admin_birthdate_set", user_id=int(user["user_id"]), status=200)
    return _ok(saved)


async def admin_birthdate_delete(request: web.Request) -> web.Response:
    async with get_session_factory(request)() as session:
        user = await _target_user(session, request.match_info["user_ref"])
        removed = await birthdates.admin_delete(session, int(user["user_id"]), _actor(request))
        await session.commit()
    if not removed:
        raise PushError("birthdate_not_found", 404)
    await _log(request, "info", "admin_birthdate_deleted", user_id=int(user["user_id"]), status=200)
    return _ok()


async def admin_birthdates_import(request: web.Request) -> web.Response:
    body = await _json_body(request)
    items = body.get("items")
    if not isinstance(items, list) or not items:
        raise PushError("no_items")
    async with get_session_factory(request)() as session:
        result = await birthdates.admin_import(session, items, _actor(request), overwrite=bool(body.get("overwrite")), resolve=resolve_user)
        await session.commit()
    await _log(request, "info", "admin_birthdates_imported", status=200, saved=result["saved"], skipped=result["skipped"], errors=result["errors"])
    return _ok(result)


# ---------------------------------------------------------------- admin: backfill


async def admin_backfill_start(request: web.Request) -> web.Response:
    body = await _json_body(request)
    async with get_session_factory(request)() as session:
        config = await load_config(session)
        rate = backfill.clean_rate(body.get("rate"), int(config["scan_rate_per_second"]))
        state = await backfill.start(session, dry_run=bool(body.get("dry_run")), rate=rate, resume=bool(body.get("resume")))
        await session.commit()
    await _log(request, "info", "backfill_started", status=200, dry_run=bool(body.get("dry_run")), rate=rate, resume=bool(body.get("resume")), total=state["total"])
    return _ok({"backfill": state})


async def admin_backfill_stop(request: web.Request) -> web.Response:
    async with get_session_factory(request)() as session:
        state = await backfill.stop(session)
        await session.commit()
    await _log(request, "info", "backfill_stopped", status=200, scanned=state["scanned"])
    return _ok({"backfill": state})


async def admin_backfill_status(request: web.Request) -> web.Response:
    async with get_session_factory(request)() as session:
        return _ok({"backfill": await backfill.status(session)})


# ---------------------------------------------------------------- admin: deliveries, logs, stats


async def _delivery_rows(session: Any, request: web.Request, limit: int, offset: int) -> tuple[int, list[dict[str, Any]]]:
    where = ["1 = 1"]
    params: dict[str, Any] = {}
    q = request.query
    if q.get("event_id"):
        where.append("d.event_id = :event_id")
        params["event_id"] = int(q["event_id"])
    if q.get("status"):
        where.append("d.status = :status")
        params["status"] = q["status"]
    if q.get("period"):
        where.append("d.period_key = :period")
        params["period"] = q["period"]
    if q.get("user"):
        ref = q["user"].strip().lstrip("@")
        if ref.lstrip("-").isdigit():
            where.append("(d.user_id = :u or u.telegram_id = :u)")
            params["u"] = int(ref)
        else:
            where.append("lower(u.username) = :uname")
            params["uname"] = ref.lower()
    clause = " and ".join(where)
    base = f"from ext_kiro_push_deliveries d left join users u on u.user_id = d.user_id left join ext_kiro_push_events e on e.id = d.event_id where {clause}"
    total = int(await session.scalar(text(f"select count(*) {base}"), params) or 0)
    rows = (
        await session.execute(
            text(
                "select d.id, d.event_id, e.name as event_name, d.user_id, u.username, u.first_name, d.period_key, d.status, d.reason, "
                "d.gift_kind, d.gift_status, d.gift_note, d.promo_code, d.promo_code_id, d.message_id, d.attempts, d.language, "
                f"d.scheduled_for, d.sent_at, d.created_at, d.updated_at {base} order by d.id desc limit :lim offset :off"
            ),
            {**params, "lim": limit, "off": offset},
        )
    ).mappings().all()
    out = []
    for r in rows:
        item = dict(r)
        for key in ("scheduled_for", "sent_at", "created_at", "updated_at"):
            item[key] = item[key].isoformat() if item[key] else None
        out.append(item)
    return total, out


async def admin_deliveries(request: web.Request) -> web.Response:
    page, per, offset = _page(request)
    try:
        async with get_session_factory(request)() as session:
            total, items = await _delivery_rows(session, request, per, offset)
    except ValueError:
        raise PushError("invalid_filter") from None
    return _ok({"total": total, "page": page, "per_page": per, "items": items})


async def admin_deliveries_download(request: web.Request) -> web.Response:
    """Recent deliveries as a JSON file; user ids and promo codes are masked unless ?mask=0."""
    mask = request.query.get("mask", "1") != "0"
    try:
        limit = max(1, min(5000, int(request.query.get("limit") or 1000)))
    except ValueError:
        limit = 1000
    async with get_session_factory(request)() as session:
        _total, items = await _delivery_rows(session, request, limit, 0)
    masker = diag.Masker(mask)
    for item in items:
        item["user_id"] = masker.user(item["user_id"])
        item["username"] = masker.user(item["username"]) if item["username"] else None
        item["first_name"] = None if mask else item["first_name"]
        item["promo_code"] = masker.secret(item["promo_code"]) if item["promo_code"] else None
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    await _log(request, "info", "admin_deliveries_exported", status=200, rows=len(items), masked=mask)
    return web.Response(
        text=json.dumps({"generated_at": datetime.now(UTC).isoformat(), "masked": mask, "items": items}, ensure_ascii=False, indent=1),
        content_type="application/json", charset="utf-8",
        headers={"Content-Disposition": f'attachment; filename="kiro-push-deliveries-{stamp}.json"', "Cache-Control": "no-store"},
    )


async def admin_stats(request: web.Request) -> web.Response:
    async with get_session_factory(request)() as session:
        config = await load_config(session)
        today = datetime.now(UTC).astimezone(ZoneInfo(config["timezone"])).date()
        return _ok(
            {
                "birthdates": await stats.birthdate_stats(session, today),
                "upcoming": await stats.upcoming_birthdays(session, today),
                "deliveries": await stats.delivery_stats(session),
            }
        )


async def admin_logs(request: web.Request) -> web.Response:
    level = request.query.get("level") or None
    try:
        limit = int(request.query.get("limit") or 100)
    except ValueError:
        limit = 100
    async with get_session_factory(request)() as session:
        config = await load_config(session)
        rows = await diag.fetch(session, limit=limit, min_level=level)
        counts = await diag.counts(session)
    return _ok(
        {
            "debug": bool(config.get("debug_log")), "counts": counts, "max_rows": diag.MAX_ROWS,
            "rows": [
                {"id": r["id"], "at": r["created_at"].isoformat(), "level": r["level"], "event": r["event"],
                 "user_id": r["user_id"], "path": r["path"], "status": r["status"], "detail": r["detail"]}
                for r in rows
            ],
        }
    )


async def admin_logs_download(request: web.Request) -> web.Response:
    mask = request.query.get("mask", "1") != "0"
    try:
        limit = int(request.query.get("limit") or diag.MAX_ROWS)
    except ValueError:
        limit = diag.MAX_ROWS
    async with get_session_factory(request)() as session:
        config = await load_config(session)
        header = await diag.collect_header(session, VERSION, config)
        rows = await diag.fetch(session, limit=limit)
    report = diag.format_report(header, rows, mask=mask)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    await _log(request, "info", "admin_log_exported", status=200, rows=len(rows), masked=mask)
    return web.Response(
        text=report, content_type="text/plain", charset="utf-8",
        headers={"Content-Disposition": f'attachment; filename="kiro-push-log-{stamp}.txt"', "Cache-Control": "no-store"},
    )


async def admin_logs_clear(request: web.Request) -> web.Response:
    async with get_session_factory(request)() as session:
        removed = await diag.clear(session)
        await session.commit()
    await _log(request, "info", "admin_log_cleared", status=200, removed=removed)
    return _ok({"removed": removed})


# ---------------------------------------------------------------- background work


async def run_tick(
    factory: Any, gateway: Gateway, *, currency: str, tariff_titles: dict[str, str], clock: Any = None,
    sleep: Any = asyncio.sleep, monotonic: Any = time.monotonic, budget: float = 50.0,
) -> dict[str, Any]:
    """One pass of the periodic job (separate from the Core wrapper so tests drive it directly)."""
    clock = clock or (lambda: datetime.now(UTC))
    started = monotonic()
    async with factory() as session:
        config = await load_config(session)
    if not config["enabled"]:
        return {"skipped": "disabled"}
    result: dict[str, Any] = {}
    result["plan"] = await scheduler.plan_events(factory, config, now=clock())
    # birthdate passes (small, they share Telegram's quota with sending)
    if config["scan_new_users"]:
        result["new_users"] = await backfill.scan_new_users(factory, gateway, config, limit=30, sleep=sleep, clock=clock)
    result["rescan"] = await backfill.rescan_missing(factory, gateway, config, limit=30, sleep=sleep, clock=clock)
    if config["auto_backfill"]:
        async with factory() as session:
            if not await get_meta(session, backfill.AUTO_BACKFILL_FLAG):
                await set_meta(session, backfill.AUTO_BACKFILL_FLAG, True)
                try:
                    await backfill.start(session, dry_run=False, rate=int(config["scan_rate_per_second"]))
                except PushError:
                    pass
                await session.commit()
    send_budget = max(5.0, min(22.0, budget - (monotonic() - started) - 10))
    result["send"] = await scheduler.process_batch(
        factory, gateway, config, currency=currency, tariff_titles=tariff_titles, clock=clock, sleep=sleep,
        monotonic=monotonic, budget_seconds=send_budget,
    )
    left = budget - (monotonic() - started)
    if left > 2:
        result["backfill"] = await backfill.run_chunk(factory, gateway, budget_seconds=left, clock=clock, sleep=sleep, monotonic=monotonic)
    return result


async def _tick(op: OperationContext, payload: dict[str, Any]) -> dict[str, Any]:
    ctx = op.runtime
    RUNTIME["ctx"] = ctx
    op.assert_current()
    gateway = make_gateway(ctx)
    if gateway is None:
        return {"skipped": "bot_unavailable"}
    result = await run_tick(
        ctx.require_session_factory(), gateway, currency=_currency(ctx.settings), tariff_titles=_tariff_titles(ctx.settings)
    )
    async with ctx.require_session_factory()() as session:  # abandoned uploads
        await session.execute(text("delete from ext_kiro_push_media where not complete and created_at < now() - interval '1 day'"))
        await session.commit()
    return {"at": datetime.now(UTC).isoformat(), **result}


def _scan_on_event(ctx: PluginContext):
    """user.registered / account.telegram_linked: look the birthdate up shortly after the event.

    Core emits events before its transaction commits, hence the short delay; the periodic new-user pass is the
    safety net when this process misses the event or the bot is not available here.
    """

    async def handler(event_name: str, payload: dict[str, Any]) -> None:
        try:
            user_id = int(payload.get("user_id") or 0)
        except (TypeError, ValueError):
            return
        if not user_id:
            return

        async def work() -> None:
            await asyncio.sleep(EVENT_SCAN_DELAY)
            try:
                gateway = make_gateway(ctx)
                factory = ctx.require_session_factory()
                if gateway is None:
                    return
                async with factory() as session:
                    config = await load_config(session)
                if not config["enabled"] or not config["scan_new_users"]:
                    return
                outcome = await backfill.scan_one(factory, gateway, user_id)
                if outcome:
                    await diag.record(factory, "info", "birthdate_scanned_on_event", user_id=user_id, detail={"event": event_name, "outcome": outcome})
            except Exception:  # noqa: BLE001 - best effort, the periodic pass catches up
                logger.warning("kiro-push: event scan failed", exc_info=True)

        _spawn(work())

    return handler


class KiroPushPlugin(Plugin):
    name = PLUGIN_ID
    version = VERSION
    plugin_api_min_version = 1
    plugin_api_max_version = 1

    def migrations(self):
        return MIGRATIONS

    def setup(self, ctx: PluginContext) -> None:
        RUNTIME["ctx"] = ctx
        handler = _scan_on_event(ctx)
        subscribe(USER_REGISTERED, handler)
        subscribe(ACCOUNT_TELEGRAM_LINKED, handler)

    def extensions(self, ctx: PluginContext) -> ExtensionContributions:
        return ExtensionContributions(
            jobs=(JobHandler(id="tick", run=_tick, timeout_seconds=55, max_attempts=1, interval_seconds=60),),
            permissions=frozenset({"rewards.days", "rewards.traffic", "rewards.balance"}),
        )

    def setup_web(self, ctx: PluginContext, app: web.Application, *, scope: str) -> None:
        if scope != WEB_SCOPE_WEBAPP:
            return
        RUNTIME["ctx"] = ctx
        r = app.router
        r.add_get(f"{USER}/birthdate", _guard(user_birthdate, admin=False))
        r.add_post(f"{USER}/birthdate", _guard(user_birthdate_set, admin=False))
        r.add_get(f"{ADMIN}/overview", _guard(admin_overview))
        r.add_put(f"{ADMIN}/config", _guard(admin_save_config))
        r.add_get(f"{ADMIN}/events", _guard(admin_events))
        r.add_post(f"{ADMIN}/events", _guard(admin_event_create))
        r.add_post(f"{ADMIN}/preview", _guard(admin_preview_draft))
        r.add_get(ADMIN + "/events/{event_id:\\d+}", _guard(admin_event_get))
        r.add_put(ADMIN + "/events/{event_id:\\d+}", _guard(admin_event_update))
        r.add_delete(ADMIN + "/events/{event_id:\\d+}", _guard(admin_event_delete))
        r.add_post(ADMIN + "/events/{event_id:\\d+}/preview", _guard(admin_event_preview))
        r.add_post(ADMIN + "/events/{event_id:\\d+}/test", _guard(admin_event_test))
        r.add_post(ADMIN + "/events/{event_id:\\d+}/run", _guard(admin_event_run))
        r.add_get(f"{ADMIN}/media", _guard(admin_media_list))
        r.add_post(f"{ADMIN}/media", _guard(admin_media_init))
        r.add_put(ADMIN + "/media/{media_id}/chunk", _guard(admin_media_chunk))
        r.add_post(ADMIN + "/media/{media_id}/finish", _guard(admin_media_finish))
        r.add_get(ADMIN + "/media/{media_id}", _guard(admin_media_get))
        r.add_delete(ADMIN + "/media/{media_id}", _guard(admin_media_delete))
        r.add_get(f"{ADMIN}/birthdates", _guard(admin_birthdates))
        r.add_post(f"{ADMIN}/birthdates/import", _guard(admin_birthdates_import))
        r.add_put(ADMIN + "/birthdates/{user_ref}", _guard(admin_birthdate_put))
        r.add_delete(ADMIN + "/birthdates/{user_ref}", _guard(admin_birthdate_delete))
        r.add_post(f"{ADMIN}/backfill/start", _guard(admin_backfill_start))
        r.add_post(f"{ADMIN}/backfill/stop", _guard(admin_backfill_stop))
        r.add_get(f"{ADMIN}/backfill/status", _guard(admin_backfill_status))
        r.add_get(f"{ADMIN}/deliveries", _guard(admin_deliveries))
        r.add_get(f"{ADMIN}/deliveries/download", _guard(admin_deliveries_download))
        r.add_get(f"{ADMIN}/stats", _guard(admin_stats))
        r.add_get(f"{ADMIN}/logs", _guard(admin_logs))
        r.add_get(f"{ADMIN}/logs/download", _guard(admin_logs_download))
        r.add_post(f"{ADMIN}/logs/clear", _guard(admin_logs_clear))

    def locales_dir(self) -> Path | None:
        path = Path(__file__).resolve().parents[2] / "locales"
        return path if path.is_dir() else None


plugin = KiroPushPlugin()

__all__ = ["SendResult", "plugin", "run_tick"]
