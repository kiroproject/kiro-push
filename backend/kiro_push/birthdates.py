"""Birthdates: validation, storage rules (lock semantics), the customer self-entry flow and admin edits.

Rules
- A stored date is LOCKED. Telegram never overwrites it (store_lookup only fills rows without a date), the
  customer can set a date exactly once (only while none is stored) and only an admin can change or delete it.
- Admin delete leaves a tombstone row (status 'cleared'): scans skip it, the customer may enter the date again.
- Sources: 'telegram' (getChat), 'user' (self-entry in the cabinet), 'admin'.
"""

from __future__ import annotations

import calendar
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import text

from .errors import PushError
from .telegram import BirthdateLookup

MONTHS_GEN = (
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
USER_AGE_RANGE = (5, 110)
ADMIN_MIN_YEAR = 1900
MAX_IMPORT = 2000


def valid_calendar_date(day: int, month: int, year: int | None = None) -> bool:
    if not 1 <= month <= 12 or day < 1:
        return False
    if year is None:
        # 29 February is a real birthday; the year is unknown, so the leap check is skipped
        return day <= calendar.monthrange(2000, month)[1]
    if not 1 <= year <= 9999:
        return False
    return day <= calendar.monthrange(year, month)[1]


def _int(value: Any, code: str) -> int:
    if isinstance(value, bool):
        raise PushError(code)
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise PushError(code) from None
    if not number.is_integer():
        raise PushError(code)
    return int(number)


def age_on(day: int, month: int, year: int, today: date) -> int:
    return today.year - year - ((today.month, today.day) < (month, day))


def clean_date(body: dict[str, Any], *, mode: str, today: date | None = None) -> tuple[int, int, int | None]:
    """Validate {day, month, year?}. ``mode`` is 'user' (age 5..110) or 'admin' (1900..today)."""
    today = today or datetime.now(UTC).date()
    day, month = _int(body.get("day"), "invalid_date"), _int(body.get("month"), "invalid_date")
    year_raw = body.get("year")
    year: int | None = None
    if year_raw not in (None, "", 0):
        year = _int(year_raw, "invalid_year")
    if not valid_calendar_date(day, month, year):
        raise PushError("invalid_date")
    if year is not None:
        if year > today.year or date(year, month, day) > today:
            raise PushError("invalid_year")
        if mode == "user":
            age = age_on(day, month, year, today)
            if not USER_AGE_RANGE[0] <= age <= USER_AGE_RANGE[1]:
                raise PushError("invalid_year")
        elif year < ADMIN_MIN_YEAR:
            raise PushError("invalid_year")
    return day, month, year


def format_date(day: int, month: int, year: int | None = None) -> str:
    return f"{day:02d}.{month:02d}.{year}" if year else f"{day:02d}.{month:02d}"


def human_date(day: int, month: int, year: int | None = None) -> str:
    base = f"{day} {MONTHS_GEN[month]}"
    return f"{base} {year}" if year else base


async def get_row(session: Any, user_id: int) -> dict[str, Any] | None:
    row = (
        await session.execute(text("select * from ext_kiro_push_birthdates where user_id = :u"), {"u": user_id})
    ).mappings().first()
    return dict(row) if row else None


async def _audit(
    session: Any, user_id: int, action: str, source: str | None, actor: str, old: str | None, new: str | None
) -> None:
    await session.execute(
        text(
            "insert into ext_kiro_push_birthdate_audit (user_id, action, source, actor, old_value, new_value) "
            "values (:u, :a, :s, :actor, :o, :n)"
        ),
        {"u": user_id, "a": action, "s": source, "actor": actor[:40], "o": old, "n": new},
    )


def _fmt_row(row: dict[str, Any] | None) -> str | None:
    if not row or row.get("day") is None:
        return None
    return format_date(row["day"], row["month"], row["year"])


# ------------------------------------------------------------------ customer


async def customer_state(session: Any, user_id: int, config: dict[str, Any]) -> dict[str, Any]:
    row = await get_row(session, user_id)
    entry = bool(config["enabled"] and config["user_entry_enabled"])
    base = {"enabled": entry, "consent_text": config["consent_text"]}
    if row and row.get("day") is not None:
        out = {
            **base, "state": "set", "day": row["day"], "month": row["month"], "source": row["source"],
            "locked": True, "can_set": False,
        }
        if row["year"]:
            out["year"] = row["year"]
        return out
    return {**base, "state": "none", "locked": False, "can_set": entry}


async def user_set(
    factory: Any, user_id: int, body: dict[str, Any], config: dict[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    """Customer self-entry. The user id comes from the session only; the body can never name another user."""
    if not (config["enabled"] and config["user_entry_enabled"]):
        raise PushError("disabled", 403)
    today = (now or datetime.now(UTC)).date()
    limit = int(config["user_attempts_per_hour"])
    async with factory() as session:
        used = int(
            await session.scalar(
                text(
                    "select count(*) from ext_kiro_push_user_attempts "
                    "where user_id = :u and created_at > now() - interval '1 hour'"
                ),
                {"u": user_id},
            )
            or 0
        )
        if used >= limit:
            raise PushError("rate_limited", 429)
        attempt_id = await session.scalar(
            text("insert into ext_kiro_push_user_attempts (user_id) values (:u) returning id"), {"u": user_id}
        )
        await session.commit()
    if body.get("consent") is not True:
        await _mark_attempt(factory, attempt_id, False, "consent_required")
        raise PushError("consent_required")
    try:
        day, month, year = clean_date(body, mode="user", today=today)
    except PushError as exc:
        await _mark_attempt(factory, attempt_id, False, exc.code)
        raise
    async with factory() as session:
        written = await session.execute(
            text(
                "insert into ext_kiro_push_birthdates (user_id, day, month, year, source, locked, status, fetched_at) "
                "values (:u, :d, :m, :y, 'user', true, 'found', now()) "
                "on conflict (user_id) do update set day = excluded.day, month = excluded.month, year = excluded.year, "
                "source = 'user', locked = true, status = 'found', fetched_at = now(), updated_at = now() "
                "where ext_kiro_push_birthdates.day is null"
            ),
            {"u": user_id, "d": day, "m": month, "y": year},
        )
        if written.rowcount != 1:
            await session.rollback()
            await _mark_attempt(factory, attempt_id, False, "already_set")
            raise PushError("already_set", 409)
        await _audit(session, user_id, "set", "user", f"user:{user_id}", None, format_date(day, month, year))
        await session.commit()
    await _mark_attempt(factory, attempt_id, True, None)
    async with factory() as session:
        return await customer_state(session, user_id, config)


async def _mark_attempt(factory: Any, attempt_id: int, ok: bool, code: str | None) -> None:
    async with factory() as session:
        await session.execute(
            text("update ext_kiro_push_user_attempts set ok = :ok, code = :c where id = :i"),
            {"ok": ok, "c": code, "i": attempt_id},
        )
        await session.commit()


# ------------------------------------------------------------------ telegram scan result


async def store_lookup(session: Any, user_id: int, lookup: BirthdateLookup, *, now: datetime | None = None) -> str:
    """Persist a getChat result. Never touches a row that has a date, is locked or was cleared by an admin.

    Returns found | absent | blocked | error | locked (a date already existed).
    """
    now = now or datetime.now(UTC)
    guard = "where ext_kiro_push_birthdates.day is null and not ext_kiro_push_birthdates.locked and ext_kiro_push_birthdates.status <> 'cleared'"
    if lookup.status == "found":
        written = await session.execute(
            text(
                "insert into ext_kiro_push_birthdates (user_id, day, month, year, source, locked, status, attempts, "
                "last_attempt_at, fetched_at) values (:u, :d, :m, :y, 'telegram', true, 'found', 1, :now, :now) "
                "on conflict (user_id) do update set day = excluded.day, month = excluded.month, year = excluded.year, "
                "source = 'telegram', locked = true, status = 'found', "
                "attempts = ext_kiro_push_birthdates.attempts + 1, last_attempt_at = :now, fetched_at = :now, "
                f"updated_at = now() {guard}"
            ),
            {"u": user_id, "d": lookup.day, "m": lookup.month, "y": lookup.year, "now": now},
        )
        if written.rowcount != 1:
            return "locked"
        await _audit(session, user_id, "fetch", "telegram", "telegram", None, format_date(lookup.day, lookup.month, lookup.year))
        return "found"
    status = {"absent": "absent", "blocked": "blocked"}.get(lookup.status, "error")
    await session.execute(
        text(
            "insert into ext_kiro_push_birthdates (user_id, status, attempts, last_attempt_at) values (:u, :s, 1, :now) "
            "on conflict (user_id) do update set status = excluded.status, "
            "attempts = ext_kiro_push_birthdates.attempts + 1, last_attempt_at = :now, updated_at = now() "
            f"{guard}"
        ),
        {"u": user_id, "s": status, "now": now},
    )
    return status


# ------------------------------------------------------------------ admin


async def admin_set(session: Any, user_id: int, body: dict[str, Any], actor: str, *, today: date | None = None) -> dict[str, Any]:
    day, month, year = clean_date(body, mode="admin", today=today)
    old = _fmt_row(await get_row(session, user_id))
    await session.execute(
        text(
            "insert into ext_kiro_push_birthdates (user_id, day, month, year, source, locked, status, fetched_at) "
            "values (:u, :d, :m, :y, 'admin', true, 'found', now()) "
            "on conflict (user_id) do update set day = excluded.day, month = excluded.month, year = excluded.year, "
            "source = 'admin', locked = true, status = 'found', fetched_at = now(), updated_at = now()"
        ),
        {"u": user_id, "d": day, "m": month, "y": year},
    )
    await _audit(session, user_id, "set", "admin", actor, old, format_date(day, month, year))
    return {"user_id": user_id, "day": day, "month": month, "year": year, "source": "admin", "locked": True}


async def admin_delete(session: Any, user_id: int, actor: str) -> bool:
    """Clear the date. The row stays as a tombstone so scans do not refill it and the customer may enter it again."""
    row = await get_row(session, user_id)
    if row is None or row.get("day") is None:
        return False
    await session.execute(
        text(
            "update ext_kiro_push_birthdates set day = null, month = null, year = null, source = null, "
            "locked = false, status = 'cleared', updated_at = now() where user_id = :u"
        ),
        {"u": user_id},
    )
    await _audit(session, user_id, "delete", row["source"], actor, _fmt_row(row), None)
    return True


async def admin_import(
    session: Any, items: list[Any], actor: str, *, overwrite: bool, resolve: Any, today: date | None = None
) -> dict[str, Any]:
    if len(items) > MAX_IMPORT:
        raise PushError("too_many_items")
    results: list[dict[str, Any]] = []
    counts = {"saved": 0, "skipped": 0, "errors": 0}
    for item in items:
        ref = str(item.get("user") if isinstance(item, dict) else "")
        try:
            if not isinstance(item, dict):
                raise PushError("invalid_item")
            user = await resolve(session, ref)
            if user is None:
                raise PushError("user_not_found")
            existing = await get_row(session, int(user["user_id"]))
            if existing and existing.get("day") is not None and not overwrite:
                counts["skipped"] += 1
                results.append({"user": ref, "status": "skipped", "reason": "already_set"})
                continue
            await admin_set(session, int(user["user_id"]), item, actor, today=today)
            counts["saved"] += 1
            results.append({"user": ref, "status": "saved"})
        except PushError as exc:
            counts["errors"] += 1
            results.append({"user": ref, "status": "error", "error": exc.code})
    return {**counts, "results": results[:500]}


def public_row(row: dict[str, Any]) -> dict[str, Any]:
    """A birthdates row joined with its user, as the admin API returns it."""
    def iso(value: Any) -> Any:
        return value.isoformat() if hasattr(value, "isoformat") else value

    return {
        "user_id": row["user_id"],
        "telegram_id": row.get("telegram_id"),
        "username": row.get("username"),
        "first_name": row.get("first_name"),
        "day": row.get("day"),
        "month": row.get("month"),
        "year": row.get("year"),
        "date": _fmt_row(row),
        "source": row.get("source"),
        "locked": bool(row.get("locked")),
        "status": row.get("status") or "unknown",
        "attempts": row.get("attempts") or 0,
        "last_attempt_at": iso(row.get("last_attempt_at")),
        "fetched_at": iso(row.get("fetched_at")),
    }
