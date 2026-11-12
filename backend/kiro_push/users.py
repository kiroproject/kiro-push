"""Core `users` helpers: resolving admin-typed references and the shared segment derivation.

Copied/adapted from kiro-wheel's bulk.py (plugins are separate packages and never import each other).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

_BIGINT_MAX = 2**63 - 1
SEGMENTS = ("none", "trial", "paid")
SEGMENT_LABELS = {"none": "без подписки", "trial": "пробная подписка", "paid": "платная подписка"}

# Segment per user, derived exactly like kiro-wheel's logic.user_segment (latest active subscription:
# no subscription / trial without tariff / paid), for everybody in one statement. NULL is_banned = not banned.
SEGMENT_CTE = """
act as (
    select distinct on (user_id) user_id, provider, status_from_panel, tariff_key, end_date
    from subscriptions
    where is_active and end_date > now()
    order by user_id, end_date desc
),
cand as (
    select u.user_id, u.telegram_id, u.language_code, u.registration_date, a.tariff_key,
        case
            when a.user_id is null then 'none'
            when btrim(coalesce(a.tariff_key, '')) = ''
                 and (lower(btrim(coalesce(a.provider, ''))) = 'trial'
                      or upper(btrim(coalesce(a.status_from_panel, ''))) = 'TRIAL') then 'trial'
            else 'paid'
        end as segment
    from users u
    left join act a on a.user_id = u.user_id
    where u.telegram_id is not null and not coalesce(u.is_banned, false)
)
"""


# Core keeps its own, proactively maintained Telegram notification status (see Minishop "Служебные события Telegram").
# Static fragments (no user input is ever formatted into SQL). NULL / unknown / enabled never exclude anybody.
CORE_BLOCKED_CAND = (
    "not exists (select 1 from users cb where cb.user_id = c.user_id and cb.telegram_notifications_status = 'blocked')"
)
CORE_BLOCKED_USER = "u.telegram_notifications_status = 'blocked'"

_core_blocked_column: bool | None = None


def reset_core_schema_cache() -> None:
    """Forget the detected Core schema (tests swap fake Core tables between runs)."""
    global _core_blocked_column
    _core_blocked_column = None


async def core_blocked_supported(factory: Any) -> bool:
    """True when Core's ``users.telegram_notifications_status`` exists. Detected once per process; errors mean 'no'."""
    global _core_blocked_column
    if _core_blocked_column is None:
        try:
            async with factory() as session:
                found = await session.scalar(
                    text(
                        "select 1 from information_schema.columns where table_name = 'users' "
                        "and column_name = 'telegram_notifications_status' and table_schema = any(current_schemas(false))"
                    )
                )
        except Exception:  # noqa: BLE001 - never block planning because of schema detection
            return False
        _core_blocked_column = bool(found)
    return _core_blocked_column


def label_of(row: Any) -> str:
    username = str(row["username"] or "").strip()
    return f"@{username}" if username else f"id {row['user_id']}"


async def resolve_refs(session: Any, refs: list[str]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """ref -> user row, plus refs that match nobody.

    Forms: ``ms_...`` (minishop id), ``@username`` / ``username``, a number (internal user id or Telegram id;
    the internal id wins when both exist).
    """
    ms: list[str] = []
    names: dict[str, str] = {}
    nums: dict[str, int] = {}
    for ref in refs:
        if ref.startswith("ms_"):
            ms.append(ref)
        elif ref.lstrip("@") and not ref.lstrip("@").lstrip("-").isdigit():
            names[ref] = ref.lstrip("@").lower()
        else:
            try:
                number = int(ref.lstrip("@"))
            except ValueError:
                continue
            if abs(number) <= _BIGINT_MAX:
                nums[ref] = number
    cols = "user_id, telegram_id, username, first_name, minishop_id, is_banned, language_code"
    by_ms: dict[str, Any] = {}
    by_name: dict[str, Any] = {}
    by_uid: dict[int, Any] = {}
    by_tg: dict[int, Any] = {}
    if ms:
        rows = (await session.execute(text(f"select {cols} from users where minishop_id = any(cast(:v as text[]))"), {"v": ms})).mappings().all()
        by_ms = {r["minishop_id"]: r for r in rows}
    if names:
        rows = (
            await session.execute(
                text(f"select {cols} from users where lower(username) = any(cast(:v as text[]))"),
                {"v": sorted(set(names.values()))},
            )
        ).mappings().all()
        by_name = {str(r["username"]).lower(): r for r in rows}
    if nums:
        rows = (
            await session.execute(
                text(
                    f"select {cols} from users where user_id = any(cast(:v as bigint[])) "
                    "or telegram_id = any(cast(:v as bigint[]))"
                ),
                {"v": sorted(set(nums.values()))},
            )
        ).mappings().all()
        for r in rows:
            by_uid[int(r["user_id"])] = r
            if r["telegram_id"] is not None:
                by_tg.setdefault(int(r["telegram_id"]), r)
    found: dict[str, dict[str, Any]] = {}
    unresolved: list[str] = []
    for ref in refs:
        row = None
        if ref in nums:
            row = by_uid.get(nums[ref]) or by_tg.get(nums[ref])
        elif ref in names:
            row = by_name.get(names[ref])
        elif ref.startswith("ms_"):
            row = by_ms.get(ref)
        if row is None:
            unresolved.append(ref)
        else:
            found[ref] = dict(row)
    return found, unresolved


async def resolve_user(session: Any, ref: str) -> dict[str, Any] | None:
    ref = str(ref or "").strip()
    if not ref:
        return None
    found, _ = await resolve_refs(session, [ref])
    return next(iter(found.values()), None)
