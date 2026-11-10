"""Planning (who gets which event today) and delivery (gift, render, send) with crash-safe state.

Planning is one idempotent INSERT ... SELECT per (event, due day): the unique constraint
(event_id, user_id, period_key) guarantees one delivery per person and period no matter how often it runs.
Delivery claims rows with FOR UPDATE SKIP LOCKED, grants the gift in its own committed transaction, records
``send_started_at`` before talking to Telegram and never re-sends a row whose send may have happened.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text

from . import diag, gifts
from .birthdates import age_on
from .errors import PushError
from .eventdefs import DueSlot, clean_audience, clean_batch_id, due_slots, month_day_keys
from .storage import jload
from .telegram import Gateway, visible_length
from .templates import pick_template, render_message
from .users import SEGMENT_CTE, label_of

logger = logging.getLogger(__name__)

CLAIM_CHUNK = 20
REPLAN_MINUTES = 10
STALE_MINUTES = 10
MAX_MANUAL_RECIPIENTS = 100_000
MAX_RETRY_WAIT = 120

Clock = Callable[[], datetime]
Sleep = Callable[[float], Awaitable[None]]


def _now() -> datetime:
    return datetime.now(UTC)


# ------------------------------------------------------------------ audience / SQL


def audience_conditions(aud: dict[str, Any], params: dict[str, Any], default_language: str) -> list[str]:
    where = ["c.segment = any(cast(:segs as text[]))"]
    params["segs"] = list(aud["segments"])
    if aud["language"] in ("ru", "en"):
        where.append("lower(split_part(coalesce(nullif(c.language_code, ''), :dl), '-', 1)) = :lang")
        params["lang"] = aud["language"]
        params["dl"] = default_language
    if aud["tariff_keys"]:
        where.append("(c.segment <> 'paid' or c.tariff_key = any(cast(:keys as text[])))")
        params["keys"] = list(aud["tariff_keys"])
    where.append(
        "not exists (select 1 from ext_kiro_push_blocked k where k.user_id = c.user_id "
        "and k.created_at > now() - make_interval(days => :bdays))"
    )
    return where


def _plan_statement(event: dict[str, Any], slot: DueSlot, config: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    kind = event["kind"]
    aud = clean_audience(jload(event["audience"]))
    params: dict[str, Any] = {
        "eid": event["id"], "pk": slot.period_key, "by": slot.base_date.year, "sched": slot.scheduled_for,
        "gk": (jload(event["gift"]) or {}).get("kind", "none"), "bdays": int(config["blocked_retry_days"]),
        "tz": config["timezone"],
    }
    joins = ""
    where = audience_conditions(aud, params, config["default_language"])
    ev_params = jload(event["params"]) or {}
    if kind == "birthday":
        joins = "join ext_kiro_push_birthdates b on b.user_id = c.user_id and b.day is not null"
        where.append("(b.month * 100 + b.day) = any(cast(:md as int[]))")
        params["md"] = month_day_keys(slot.base_date, event["feb29"])
    sql = (
        f"with {SEGMENT_CTE} "
        "insert into ext_kiro_push_deliveries (event_id, user_id, period_key, base_year, status, language, gift_kind, "
        "gift_status, scheduled_for, next_try_at) "
        "select :eid, c.user_id, :pk, :by, 'pending', c.language_code, cast(:gk as varchar(20)), "
        "case when cast(:gk as varchar(20)) = 'none' then null else 'pending' end, :sched, :sched "
        f"from cand c {joins} where {' and '.join(where)} "
        "on conflict (event_id, user_id, period_key) do nothing"
    )
    return sql, params


async def _plan_anniversary(session: Any, event: dict[str, Any], slot: DueSlot, config: dict[str, Any]) -> int:
    """Registration anniversaries: the local calendar date comes from Python's zoneinfo, not from the database.

    SQL narrows by UTC month/day (one day either side), Python decides exactly in the configured timezone
    (so the result never depends on the database's own time zone data), then the ids are inserted idempotently.
    """
    tz = ZoneInfo(config["timezone"])
    min_years = int((jload(event["params"]) or {}).get("min_years", 1))
    aud = clean_audience(jload(event["audience"]))
    params: dict[str, Any] = {"bdays": int(config["blocked_retry_days"])}
    where = audience_conditions(aud, params, config["default_language"])
    wanted = set(month_day_keys(slot.base_date, event["feb29"]))
    coarse: set[int] = set()
    for shift in (-1, 0, 1):
        coarse.update(month_day_keys(slot.base_date + timedelta(days=shift), event["feb29"]))
    utc_local = "(c.registration_date at time zone 'UTC')"
    where.append(f"(extract(month from {utc_local}) * 100 + extract(day from {utc_local})) = any(cast(:coarse as int[]))")
    params["coarse"] = sorted(coarse)
    rows = (
        await session.execute(
            text(f"with {SEGMENT_CTE} select c.user_id, c.registration_date from cand c where {' and '.join(where)}"), params
        )
    ).all()
    ids = []
    for user_id, registered in rows:
        local = registered.astimezone(tz).date()
        if local.month * 100 + local.day in wanted and local.year <= slot.base_date.year - min_years:
            ids.append(int(user_id))
    if not ids:
        return 0
    gift_kind = (jload(event["gift"]) or {}).get("kind", "none")
    inserted = 0
    for start in range(0, len(ids), 2000):
        result = await session.execute(
            text(
                "insert into ext_kiro_push_deliveries (event_id, user_id, period_key, base_year, status, language, gift_kind, "
                "gift_status, scheduled_for, next_try_at) "
                "select :eid, u.user_id, :pk, :by, 'pending', u.language_code, cast(:gk as varchar(20)), "
                "case when cast(:gk as varchar(20)) = 'none' then null else 'pending' end, :sched, :sched "
                "from users u where u.user_id = any(cast(:ids as bigint[])) "
                "on conflict (event_id, user_id, period_key) do nothing"
            ),
            {"eid": event["id"], "pk": slot.period_key, "by": slot.base_date.year, "gk": gift_kind, "sched": slot.scheduled_for,
             "ids": ids[start : start + 2000]},
        )
        inserted += int(result.rowcount or 0)
    return inserted


async def plan_events(factory: Any, config: dict[str, Any], *, now: datetime | None = None) -> dict[str, int]:
    """Create pending deliveries for every automatic event that is due. Safe to run any number of times."""
    now = now or _now()
    tz = ZoneInfo(config["timezone"])
    created = planned = 0
    async with factory() as session:
        events = (
            await session.execute(
                text("select * from ext_kiro_push_events where enabled and deleted_at is null and kind <> 'manual' order by id")
            )
        ).mappings().all()
    for event in events:
        event = dict(event)
        for slot in due_slots(event, now, tz, int(config["catchup_hours"])):
            async with factory() as session:
                recent = await session.scalar(
                    text("select planned_at from ext_kiro_push_plans where event_id = :e and plan_key = :k"),
                    {"e": event["id"], "k": slot.plan_key},
                )
                if recent is not None and now - recent < timedelta(minutes=REPLAN_MINUTES):
                    continue
                if event["kind"] == "registration_anniversary":
                    inserted = await _plan_anniversary(session, event, slot, config)
                else:
                    sql, params = _plan_statement(event, slot, config)
                    result = await session.execute(text(sql), params)
                    inserted = int(result.rowcount or 0)
                await session.execute(
                    text(
                        "insert into ext_kiro_push_plans (event_id, plan_key, planned_at, matched) values (:e, :k, :now, :m) "
                        "on conflict (event_id, plan_key) do update set planned_at = :now, "
                        "matched = ext_kiro_push_plans.matched + :m"
                    ),
                    {"e": event["id"], "k": slot.plan_key, "now": now, "m": inserted},
                )
                await session.commit()
                created += inserted
                planned += 1
    return {"events": len(events), "slots": planned, "created": created}


async def run_manual(
    factory: Any, event: dict[str, Any], config: dict[str, Any], body: dict[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    """Manual / on-demand run of an event over its audience. ``dry_run`` only counts and shows a sample."""
    if event["kind"] in ("birthday", "registration_anniversary"):
        raise PushError("run_not_supported")
    aud = clean_audience(body["audience"]) if isinstance(body.get("audience"), dict) else clean_audience(jload(event["audience"]))
    params: dict[str, Any] = {"bdays": int(config["blocked_retry_days"])}
    where = " and ".join(audience_conditions(aud, params, config["default_language"]))
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    f"with {SEGMENT_CTE} select c.user_id, u.username from cand c join users u on u.user_id = c.user_id "
                    f"where {where} order by c.user_id"
                ),
                params,
            )
        ).mappings().all()
    if len(rows) > MAX_MANUAL_RECIPIENTS:
        raise PushError("too_many_recipients")
    summary = {"matched": len(rows), "sample": [label_of(r) for r in rows[:10]]}
    if bool(body.get("dry_run")):
        return {"dry_run": True, **summary}
    if not rows:
        raise PushError("no_recipients")
    now = now or _now()
    batch_id = clean_batch_id(body.get("batch_id")) or uuid.uuid4().hex[:16]
    gift_kind = (jload(event["gift"]) or {}).get("kind", "none")
    queued = 0
    async with factory() as session:
        await session.execute(
            text(
                "insert into ext_kiro_push_batches (batch_id, event_id, matched) values (:b, :e, :m) "
                "on conflict (batch_id) do nothing"
            ),
            {"b": batch_id, "e": event["id"], "m": len(rows)},
        )
        ids = [int(r["user_id"]) for r in rows]
        for start in range(0, len(ids), 2000):
            result = await session.execute(
                text(
                    "insert into ext_kiro_push_deliveries (event_id, user_id, period_key, base_year, status, language, "
                    "gift_kind, gift_status, batch_id, scheduled_for, next_try_at) "
                    "select :e, u.user_id, :pk, :by, 'pending', u.language_code, cast(:gk as varchar(20)), "
                    "case when cast(:gk as varchar(20)) = 'none' then null else 'pending' end, cast(:b as varchar(32)), :now, :now "
                    "from users u where u.user_id = any(cast(:ids as bigint[])) "
                    "on conflict (event_id, user_id, period_key) do nothing"
                ),
                {"e": event["id"], "pk": f"m:{batch_id}", "by": now.year, "gk": gift_kind, "b": batch_id, "now": now,
                 "ids": ids[start : start + 2000]},
            )
            queued += int(result.rowcount or 0)
        await session.execute(text("update ext_kiro_push_batches set queued = :q where batch_id = :b"), {"q": queued, "b": batch_id})
        await session.commit()
    return {"dry_run": False, "batch_id": batch_id, "queued": queued, "already": len(rows) - queued, **summary}


# ------------------------------------------------------------------ delivery


async def balance_minor(session: Any, user_id: int, currency: str) -> int | None:
    try:
        from db.dal import user_balance_dal

        return int(await user_balance_dal.balance_minor(session, user_id, currency))
    except Exception:  # noqa: BLE001 - a placeholder must never block a greeting
        return None


def format_money(minor: int, currency: str) -> str:
    amount = f"{minor / 100:,.2f}".replace(",", " ").replace(".", ",")
    if amount.endswith(",00"):
        amount = amount[:-3]
    return f"{amount} {gifts.CURRENCY_SYMBOLS.get(currency.upper(), currency.upper())}"


async def build_values(
    session: Any, user: dict[str, Any], delivery: dict[str, Any], event: dict[str, Any], *, gift_available: bool,
    promo_code: str | None, config: dict[str, Any], currency: str, tariff_titles: dict[str, str], today: date,
) -> dict[str, str | None]:
    """Placeholder values for one recipient. Missing facts are empty strings (optional blocks then vanish)."""
    uid = int(user["user_id"])
    first = str(user.get("first_name") or "").strip()
    username = str(user.get("username") or "").strip()
    lang = str(user.get("language_code") or config["default_language"]).lower()
    fallback_name = "friend" if lang.startswith("en") else "друг"
    values: dict[str, str | None] = {
        "first_name": first,
        "name": first or (f"@{username}" if username else fallback_name),
    }
    bd = (
        await session.execute(text("select day, month, year from ext_kiro_push_birthdates where user_id = :u and day is not null"), {"u": uid})
    ).mappings().first()
    values["age"] = ""
    if bd and bd["year"]:
        if event["kind"] == "birthday" and delivery.get("base_year"):
            values["age"] = str(int(delivery["base_year"]) - int(bd["year"]))
        else:
            values["age"] = str(age_on(int(bd["day"]), int(bd["month"]), int(bd["year"]), today))
    values["years"] = ""
    reg = user.get("registration_date")
    if reg is not None:
        reg_local = reg.astimezone(ZoneInfo(config["timezone"])).date() if reg.tzinfo else reg.date()
        if event["kind"] == "registration_anniversary" and delivery.get("base_year"):
            years = int(delivery["base_year"]) - reg_local.year
        else:
            years = today.year - reg_local.year - ((today.month, today.day) < (reg_local.month, reg_local.day))
        values["years"] = str(max(years, 0))
    sub = (
        await session.execute(
            text(
                "select end_date, tariff_key from subscriptions where user_id = :u and is_active and end_date > now() "
                "order by end_date desc limit 1"
            ),
            {"u": uid},
        )
    ).mappings().first()
    if sub:
        values["days_left"] = str(max((sub["end_date"] - _now()).days, 0))
        key = str(sub["tariff_key"] or "")
        values["tariff"] = tariff_titles.get(key, key)
    else:
        values["days_left"] = "0"
        values["tariff"] = ""
    minor = await balance_minor(session, uid, currency)
    values["balance"] = format_money(minor, currency) if minor is not None else ""
    spins = await gifts.wheel_spin_balance(session, uid) if await gifts.wheel_installed(session) else None
    values["spins"] = str(spins) if spins is not None else ""
    gift = jload(event["gift"]) or {"kind": "none"}
    values["gift"] = gifts.gift_text(gift, currency) if gift_available else ""
    values["promo_code"] = promo_code or "" if gift_available else ""
    values["promo_discount"] = gifts.discount_text(gift) if gift_available else ""
    return values


async def _set(factory: Any, delivery_id: int, **fields: Any) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    async with factory() as session:
        await session.execute(text(f"update ext_kiro_push_deliveries set {sets}, updated_at = now() where id = :_id"), {**fields, "_id": delivery_id})
        await session.commit()


async def _finish(factory: Any, d: dict[str, Any], status: str, reason: str | None, **extra: Any) -> str:
    await _set(factory, d["id"], status=status, reason=(reason or None) and reason[:80], claimed_at=None, **extra)
    return status


async def _retry(factory: Any, d: dict[str, Any], config: dict[str, Any], reason: str, now: datetime, *, wait: int | None = None,
                 count_attempt: bool = True) -> str:
    attempts = int(d["attempts"]) - (0 if count_attempt else 1)
    if count_attempt and attempts >= int(config["max_send_attempts"]):
        return await _finish(factory, d, "failed", reason)
    delay = timedelta(seconds=wait) if wait is not None else timedelta(minutes=int(config["retry_minutes"]))
    await _set(
        factory, d["id"], status="pending", reason=reason[:80], next_try_at=now + delay, claimed_at=None,
        send_started_at=None, attempts=max(attempts, 0),
    )
    return "retry"


async def _claim(factory: Any, now: datetime, limit: int) -> list[dict[str, Any]]:
    stale = now - timedelta(minutes=STALE_MINUTES)
    async with factory() as session:
        # A row that was mid-send when its worker died may or may not have reached Telegram: never repeat it.
        await session.execute(
            text(
                "update ext_kiro_push_deliveries set status = 'failed', reason = 'interrupted_during_send', "
                "updated_at = now() where status = 'sending' and claimed_at < :stale and send_started_at is not null"
            ),
            {"stale": stale},
        )
        rows = (
            await session.execute(
                text(
                    "update ext_kiro_push_deliveries d set status = 'sending', claimed_at = :now, attempts = d.attempts + 1, "
                    "updated_at = now() where d.id in ("
                    "select id from ext_kiro_push_deliveries where (status = 'pending' and next_try_at <= :now) "
                    "or (status = 'sending' and claimed_at < :stale) order by id limit :n for update skip locked) "
                    "returning d.*"
                ),
                {"now": now, "stale": stale, "n": limit},
            )
        ).mappings().all()
        await session.commit()
    return [dict(r) for r in rows]


async def _load_event(factory: Any, cache: dict[int, dict[str, Any] | None], event_id: int) -> dict[str, Any] | None:
    if event_id not in cache:
        async with factory() as session:
            row = (await session.execute(text("select * from ext_kiro_push_events where id = :i"), {"i": event_id})).mappings().first()
        cache[event_id] = dict(row) if row else None
    return cache[event_id]


async def process_delivery(
    factory: Any, gateway: Gateway, d: dict[str, Any], config: dict[str, Any], events: dict[int, dict[str, Any] | None], *,
    currency: str, tariff_titles: dict[str, str], clock: Clock,
) -> tuple[str, int]:
    """Handle one claimed delivery. Returns (outcome, retry_after_seconds)."""
    now = clock()
    event = await _load_event(factory, events, int(d["event_id"]))
    if event is None or event["deleted_at"] is not None:
        return await _finish(factory, d, "skipped", "event_deleted"), 0
    if not event["enabled"] and not d["batch_id"]:
        return await _finish(factory, d, "skipped", "event_disabled"), 0
    async with factory() as session:
        user = (
            await session.execute(
                text(
                    "select user_id, telegram_id, username, first_name, language_code, is_banned, registration_date "
                    "from users where user_id = :u"
                ),
                {"u": d["user_id"]},
            )
        ).mappings().first()
        blocked = await session.scalar(text("select 1 from ext_kiro_push_blocked where user_id = :u"), {"u": d["user_id"]})
    if user is None:
        return await _finish(factory, d, "skipped", "user_missing"), 0
    if user["is_banned"]:
        return await _finish(factory, d, "skipped", "user_banned"), 0
    if not user["telegram_id"]:
        return await _finish(factory, d, "skipped", "no_telegram"), 0
    if blocked:
        return await _finish(factory, d, "skipped", "user_blocked"), 0
    templates = jload(event["templates"]) or {}
    picked = pick_template(templates, user["language_code"], config["default_language"])
    if picked is None:
        return await _finish(factory, d, "skipped", "no_template"), 0
    language, tpl = picked

    gift = jload(event["gift"]) or {"kind": "none"}
    gift_available = False
    promo_code = d["promo_code"]
    gift_status = d["gift_status"]
    if gift["kind"] != "none":
        if gift_status != "granted":
            try:
                outcome = await gifts.apply_gift(factory, d, gift, currency=currency, now=now)
            except Exception as exc:  # noqa: BLE001 - transient: try the whole delivery again later
                logger.warning("kiro-push: gift for delivery %s failed: %r", d["id"], exc)
                return await _retry(factory, d, config, f"gift_error:{type(exc).__name__}", now), 0
            gift_status, promo_code = outcome.status, outcome.promo_code
            if outcome.status != "granted":
                await diag.record(factory, "info", "gift_not_given", user_id=int(d["user_id"]),
                                  detail={"event_id": d["event_id"], "gift": gift["kind"], "status": outcome.status, "note": outcome.note})
        gift_available = gift_status == "granted"
        if not gift_available and event["gift_fallback"] == "skip":
            return await _finish(factory, d, "skipped", f"gift_{gift_status or 'unavailable'}"), 0

    async with factory() as session:
        local_today = now.astimezone(ZoneInfo(config["timezone"])).date()
        values = await build_values(
            session, dict(user), d, event, gift_available=gift_available, promo_code=promo_code, config=config,
            currency=currency, tariff_titles=tariff_titles, today=local_today,
        )
    rendered = render_message(tpl, values, gift_available=gift_available, language=language)
    length = visible_length(rendered.text)
    if length > 4096 or (not rendered.text and not rendered.media):
        return await _finish(factory, d, "failed", "message_too_long" if length > 4096 else "empty_message"), 0

    await _set(factory, d["id"], send_started_at=now)
    async with factory() as session:
        result = await gateway.send(session, chat_id=int(user["telegram_id"]), user_id=int(user["user_id"]), message=rendered)
        await session.commit()
    if result.status == "sent":
        await _finish(factory, d, "sent", None, message_id=result.message_id, sent_at=clock())
        return "sent", 0
    if result.status == "blocked":
        async with factory() as session:
            await session.execute(
                text(
                    "insert into ext_kiro_push_blocked (user_id, reason) values (:u, :r) "
                    "on conflict (user_id) do update set reason = excluded.reason, created_at = now()"
                ),
                {"u": user["user_id"], "r": result.error[:120]},
            )
            await session.commit()
        await _finish(factory, d, "blocked", result.error)
        return "blocked", 0
    if result.status == "retry_after":
        wait = min(max(result.retry_after, 1), MAX_RETRY_WAIT)
        await _retry(factory, d, config, "rate_limited", now, wait=wait + 1, count_attempt=False)
        return "rate_limited", wait
    outcome = await _retry(factory, d, config, f"send_error:{result.error}"[:80], now)
    return outcome, 0


async def process_batch(
    factory: Any,
    gateway: Gateway,
    config: dict[str, Any],
    *,
    currency: str = "RUB",
    tariff_titles: dict[str, str] | None = None,
    clock: Clock = _now,
    sleep: Sleep = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    budget_seconds: float = 30.0,
) -> dict[str, int]:
    """Send as many due deliveries as the time budget and the rate limit allow."""
    started = monotonic()
    delay = 1.0 / int(config["send_rate_per_second"])
    remaining = int(config["batch_size"])
    counts = {"sent": 0, "skipped": 0, "failed": 0, "blocked": 0, "retry": 0, "rate_limited": 0, "claimed": 0}
    events: dict[int, dict[str, Any] | None] = {}
    titles = tariff_titles or {}
    while remaining > 0 and monotonic() - started < budget_seconds:
        claimed = await _claim(factory, clock(), min(CLAIM_CHUNK, remaining))
        if not claimed:
            break
        counts["claimed"] += len(claimed)
        remaining -= len(claimed)
        for index, d in enumerate(claimed):
            if monotonic() - started >= budget_seconds:
                await _release(factory, claimed[index:], clock())
                return counts
            try:
                outcome, wait = await process_delivery(
                    factory, gateway, d, config, events, currency=currency, tariff_titles=titles, clock=clock
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("kiro-push: delivery %s crashed", d["id"])
                await diag.record(factory, "error", "delivery_crashed", user_id=int(d["user_id"]), detail={"id": d["id"], "error": repr(exc)[:200]})
                outcome, wait = await _retry(factory, d, config, f"crash:{type(exc).__name__}", clock()), 0
            counts[outcome if outcome in counts else "failed"] += 1
            if wait:
                # Telegram asked to slow down: hand the rest of this chunk back and pause.
                await _release(factory, claimed[index + 1 :], clock())
                if monotonic() - started + wait >= budget_seconds:
                    return counts
                await sleep(wait + 0.5)
                break
            await sleep(delay)
    return counts


async def _release(factory: Any, rows: list[dict[str, Any]], now: datetime) -> None:
    """Return claimed-but-untouched rows to the queue without spending an attempt."""
    if not rows:
        return
    async with factory() as session:
        await session.execute(
            text(
                "update ext_kiro_push_deliveries set status = 'pending', claimed_at = null, "
                "attempts = greatest(attempts - 1, 0), updated_at = now() where id = any(cast(:ids as bigint[])) and status = 'sending'"
            ),
            {"ids": [int(r["id"]) for r in rows]},
        )
        await session.commit()
