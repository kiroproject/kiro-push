"""Gifts attached to an event: validation, human text and idempotent granting.

One gift kind per event. The grant happens in its own transaction BEFORE the greeting is sent and records
``gift_status = 'granted'`` on the delivery row in the same commit, so a retry of a failed message never grants
twice (and Core / the wheel ledger are idempotent by key on top of that).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from bot.plugins.extensions.contracts import ExtensionError
from bot.plugins.extensions.rewards import Reward, grant
from bot.services.promo_code_service import PromoCodeService
from bot.services.promo_effects import PromoEffects
from db.dal import user_dal

from .errors import PushError
from .storage import PLUGIN_ID, jload

logger = logging.getLogger(__name__)

GIFT_KINDS = ("none", "traffic", "days", "balance", "promo_discount", "wheel_spins")
GIB = 1024**3
NEEDS_SUBSCRIPTION = ("days", "traffic")
UNCAPPED = 1_000_000
CURRENCY_SYMBOLS = {"RUB": "₽", "USD": "$", "EUR": "€"}


def _num(value: Any, lo: float, hi: float, code: str, *, integer: bool = False) -> float | int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise PushError(code) from None
    if not lo <= number <= hi or number != number:
        raise PushError(code)
    if integer:
        if not number.is_integer():
            raise PushError(code)
        return int(number)
    return int(number) if number.is_integer() else round(number, 3)


def clean_gift(raw: Any, *, wheel_installed: bool) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    kind = str(raw.get("kind") or "none")
    if kind not in GIFT_KINDS:
        raise PushError("invalid_gift_kind")
    if kind == "none":
        return {"kind": "none"}
    if kind == "traffic":
        return {"kind": kind, "gb": _num(raw.get("gb"), 0.1, 10000, "invalid_gift_gb")}
    if kind == "days":
        return {"kind": kind, "days": _num(raw.get("days"), 1, 3650, "invalid_gift_days", integer=True)}
    if kind == "balance":
        return {"kind": kind, "amount_minor": _num(raw.get("amount_minor"), 1, 100_000_000, "invalid_gift_amount", integer=True)}
    if kind == "promo_discount":
        applies = str(raw.get("applies_to") or "subscription")
        if applies not in ("subscription", "all"):
            raise PushError("invalid_gift_applies_to")
        return {
            "kind": kind,
            "percent": _num(raw.get("percent"), 1, 100, "invalid_gift_percent"),
            "valid_days": _num(raw.get("valid_days", 14), 1, 365, "invalid_gift_valid_days", integer=True),
            "applies_to": applies,
        }
    if not wheel_installed:
        raise PushError("wheel_not_installed", 409)
    return {
        "kind": kind,
        "spins": _num(raw.get("spins"), 1, 100, "invalid_gift_spins", integer=True),
        "ignore_cap": bool(raw.get("ignore_cap")),
    }


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _trim(number: float) -> str:
    return str(int(number)) if float(number).is_integer() else f"{number:g}"


def gift_text(gift: dict[str, Any], currency: str = "RUB") -> str:
    """Human description for the {gift} placeholder."""
    kind = gift.get("kind", "none")
    if kind == "days":
        n = int(gift["days"])
        return f"+{n} {_plural(n, 'день', 'дня', 'дней')}"
    if kind == "traffic":
        return f"+{_trim(gift['gb'])} ГБ трафика"
    if kind == "balance":
        amount = gift["amount_minor"] / 100
        shown = f"{amount:,.2f}".replace(",", " ").replace(".", ",")
        if shown.endswith(",00"):
            shown = shown[:-3]
        return f"+{shown} {CURRENCY_SYMBOLS.get(currency.upper(), currency.upper())} на баланс"
    if kind == "promo_discount":
        return f"скидка {_trim(gift['percent'])}% по промокоду"
    if kind == "wheel_spins":
        n = int(gift["spins"])
        return f"+{n} {_plural(n, 'билет', 'билета', 'билетов')} колеса удачи"
    return ""


def discount_text(gift: dict[str, Any]) -> str:
    return f"{_trim(gift['percent'])}%" if gift.get("kind") == "promo_discount" else ""


async def wheel_installed(session: Any) -> bool:
    """kiro-wheel is installed when both of its ticket tables exist (it owns them)."""
    found = await session.scalar(
        text(
            "select count(*) from (values ('ext_kiro_wheel_credits'), ('ext_kiro_wheel_grants')) t(name) "
            "where to_regclass(t.name) is not null"
        )
    )
    return int(found or 0) == 2


async def wheel_spin_balance(session: Any, user_id: int) -> int | None:
    try:
        return int(await session.scalar(text("select coalesce(spins, 0) from ext_kiro_wheel_credits where user_id = :u"), {"u": user_id}) or 0)
    except Exception:  # noqa: BLE001
        return None


@dataclass
class GiftOutcome:
    status: str  # granted | not_eligible | unavailable | failed
    note: str = ""
    promo_code: str | None = None
    promo_code_id: int | None = None


async def _has_active_subscription(session: Any, user_id: int) -> bool:
    return bool(
        await session.scalar(
            text("select 1 from subscriptions where user_id = :u and is_active and end_date > now() limit 1"), {"u": user_id}
        )
    )


async def apply_gift(
    factory: Any,
    delivery: dict[str, Any],
    gift: dict[str, Any],
    *,
    currency: str,
    now: datetime | None = None,
) -> GiftOutcome:
    """Grant the gift once for this delivery and mark it ``granted`` in the same commit.

    A transient error (database, panel) propagates so the delivery is retried; permanent ones come back as an
    outcome (``not_eligible`` / ``unavailable`` / ``failed``) and are stored on the delivery.
    """
    now = now or datetime.now(UTC)
    kind = gift["kind"]
    user_id = int(delivery["user_id"])
    key = f"push:{delivery['event_id']}:{user_id}:{delivery['period_key']}"[:128]
    async with factory() as session:
        row = (
            await session.execute(
                text(
                    "select gift_status, promo_code, promo_code_id, gift_note from ext_kiro_push_deliveries "
                    "where id = :i for update"
                ),
                {"i": delivery["id"]},
            )
        ).mappings().first()
        if row is None:
            return GiftOutcome("failed", "delivery_missing")
        if row["gift_status"] == "granted":
            return GiftOutcome("granted", row["gift_note"] or "", row["promo_code"], row["promo_code_id"])
        await user_dal.lock_user_by_id(session, user_id)  # Core lock order: the user row first
        outcome = await _grant(session, kind, gift, user_id, key, delivery, currency, now)
        await session.execute(
            text(
                "update ext_kiro_push_deliveries set gift_status = :s, gift_note = :n, promo_code = :c, "
                "promo_code_id = :ci, updated_at = now() where id = :i"
            ),
            {"s": outcome.status, "n": outcome.note[:120] or None, "c": outcome.promo_code, "ci": outcome.promo_code_id, "i": delivery["id"]},
        )
        await session.commit()
        return outcome


async def _grant(
    session: Any, kind: str, gift: dict[str, Any], user_id: int, key: str, delivery: dict[str, Any], currency: str, now: datetime
) -> GiftOutcome:
    if kind in NEEDS_SUBSCRIPTION and not await _has_active_subscription(session, user_id):
        return GiftOutcome("not_eligible", "subscription_required")
    try:
        if kind == "days":
            await grant(session, owner=PLUGIN_ID, user_id=user_id, idempotency_key=key, reward=Reward(kind="days", amount=int(gift["days"])))
        elif kind == "traffic":
            await grant(
                session, owner=PLUGIN_ID, user_id=user_id, idempotency_key=key,
                reward=Reward(kind="traffic", amount=int(float(gift["gb"]) * GIB)),
            )
        elif kind == "balance":
            await grant(
                session, owner=PLUGIN_ID, user_id=user_id, idempotency_key=key,
                reward=Reward(kind="balance", amount=int(gift["amount_minor"]), currency=currency.upper()),
            )
        elif kind == "promo_discount":
            created = await PromoCodeService.issue_code(
                session,
                effects=PromoEffects(discount_percent=float(gift["percent"]), applies_to=gift.get("applies_to", "subscription")),
                code=None,
                max_activations=1,
                valid_until=now + timedelta(days=int(gift.get("valid_days", 14))),
                origin=PLUGIN_ID,
                created_by_admin_id=None,
                user_id=user_id,
            )
            code = str(created.code)
            code_id = getattr(created, "promo_code_id", None)
            if code_id is None:
                code_id = await session.scalar(text("select promo_code_id from promo_codes where code = :c"), {"c": code})
            return GiftOutcome("granted", "", code, int(code_id) if code_id is not None else None)
        elif kind == "wheel_spins":
            return await _grant_wheel(session, gift, user_id, key)
        else:
            return GiftOutcome("failed", "unknown_gift")
    except ExtensionError as exc:
        code = str(getattr(exc, "code", "") or exc)
        if "requires_subscription" in code:
            return GiftOutcome("not_eligible", "subscription_required")
        return GiftOutcome("failed", code[:100])
    return GiftOutcome("granted")


async def _grant_wheel(session: Any, gift: dict[str, Any], user_id: int, key: str) -> GiftOutcome:
    """kiro-wheel's add_bonus_spins semantics: ledger row (idempotent by key) + capped credits upsert."""
    if not await wheel_installed(session):
        return GiftOutcome("unavailable", "wheel_not_installed")
    spins = int(gift["spins"])
    cap = UNCAPPED
    if not gift.get("ignore_cap"):
        raw = jload(await session.scalar(text("select data from ext_kiro_wheel_config where id = 1")))
        try:
            cap = int((raw or {}).get("max_bonus_spins", 20))
        except (TypeError, ValueError, AttributeError):
            cap = 20
    inserted = await session.execute(
        text(
            "insert into ext_kiro_wheel_grants (key, user_id, spins, reason) values (:k, :u, :s, 'push') "
            "on conflict (key) do nothing"
        ),
        {"k": key, "u": user_id, "s": spins},
    )
    if inserted.rowcount == 1:
        await session.execute(
            text(
                "insert into ext_kiro_wheel_credits (user_id, spins) values (:u, least(cast(:s as integer), cast(:cap as integer))) "
                "on conflict (user_id) do update set spins = least(ext_kiro_wheel_credits.spins + cast(:s as integer), "
                "cast(:cap as integer)), updated_at = now()"
            ),
            {"u": user_id, "s": spins, "cap": max(cap, 0)},
        )
    return GiftOutcome("granted", "" if inserted.rowcount == 1 else "already")


def gift_json(gift: dict[str, Any]) -> str:
    return json.dumps(gift, ensure_ascii=False)
