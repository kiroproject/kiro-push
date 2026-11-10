"""Telegram birthdate scanning: the one-time resumable backfill, the new-user pass and the periodic re-scan.

Common rules
- Users: Core ``users`` with a telegram_id, not banned (NULL is_banned = not banned).
- ``scan_user`` calls ``Gateway.lookup_birthdate`` (getChat); 429 ``retry_after`` is honoured by the caller.
- A dry run counts only: it stores NOTHING in the birthdates table (progress lives in the backfill row).
- The backfill state row keeps a cursor (last user_id), so a stopped or interrupted run resumes where it ended.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from . import birthdates
from .errors import PushError
from .storage import get_meta, set_meta
from .telegram import BirthdateLookup, Gateway

logger = logging.getLogger(__name__)

CHUNK = 50
STOP_CHECK = 5  # users between looks at the stop flag
MAX_RETRY_WAIT = 300
RATE_RANGE = (1, 28)
NEW_USER_CURSOR = "new_user_cursor"
AUTO_BACKFILL_FLAG = "auto_backfill_started"

Clock = Callable[[], datetime]
Sleep = Callable[[float], Awaitable[None]]

_ELIGIBLE = "u.telegram_id is not null and not coalesce(u.is_banned, false)"
_NO_ROW = "not exists (select 1 from ext_kiro_push_birthdates b where b.user_id = u.user_id)"


def _now() -> datetime:
    return datetime.now(UTC)


# ------------------------------------------------------------------ state


async def status(session: Any) -> dict[str, Any]:
    row = (await session.execute(text("select * from ext_kiro_push_backfill where id = 1"))).mappings().first()
    if row is None:
        return {
            "status": "idle", "dry_run": False, "rate": 20, "total": 0, "scanned": 0, "found": 0, "absent": 0,
            "blocked": 0, "errors": 0, "rate_limited": 0, "progress": 0.0, "hit_rate": None,
            "started_at": None, "finished_at": None, "updated_at": None, "paused_until": None, "last_error": None,
            "can_resume": False,
        }
    data = dict(row)
    scanned = int(data["scanned"])
    total = int(data["total"])

    def iso(value: Any) -> Any:
        return value.isoformat() if value else None

    return {
        "status": data["status"],
        "dry_run": bool(data["dry_run"]),
        "rate": int(data["rate"]),
        "total": total,
        "scanned": scanned,
        "found": int(data["found"]),
        "absent": int(data["absent"]),
        "blocked": int(data["blocked"]),
        "errors": int(data["errors"]),
        "rate_limited": int(data["rate_limited"]),
        "progress": round(scanned / total, 4) if total else (1.0 if data["status"] == "done" else 0.0),
        "hit_rate": round(int(data["found"]) / scanned, 4) if scanned else None,
        "cursor": data["cursor"],
        "started_at": iso(data["started_at"]),
        "finished_at": iso(data["finished_at"]),
        "updated_at": iso(data["updated_at"]),
        "paused_until": iso(data["paused_until"]),
        "last_error": data["last_error"],
        "can_resume": data["status"] == "stopped" and data["cursor"] is not None,
    }


def clean_rate(raw: Any, default: int) -> int:
    if raw in (None, ""):
        return default
    try:
        rate = int(float(raw))
    except (TypeError, ValueError):
        raise PushError("invalid_rate") from None
    if not RATE_RANGE[0] <= rate <= RATE_RANGE[1]:
        raise PushError("invalid_rate")
    return rate


async def count_candidates(session: Any, after: int | None = None) -> int:
    return int(
        await session.scalar(
            text(f"select count(*) from users u where {_ELIGIBLE} and {_NO_ROW} and u.user_id > :c"),
            {"c": after if after is not None else -(2**62)},
        )
        or 0
    )


async def start(
    session: Any, *, dry_run: bool, rate: int, resume: bool = False, now: datetime | None = None
) -> dict[str, Any]:
    """Begin (or resume) a backfill. The worker job performs it; this only records the intent."""
    now = now or _now()
    row = (await session.execute(text("select status, cursor, dry_run from ext_kiro_push_backfill where id = 1 for update"))).first()
    if row is not None and row[0] == "running":
        raise PushError("already_running", 409)
    if resume:
        if row is None or row[0] != "stopped" or row[1] is None:
            raise PushError("nothing_to_resume", 409)
        await session.execute(
            text(
                "update ext_kiro_push_backfill set status = 'running', rate = :r, paused_until = null, "
                "finished_at = null, updated_at = :now where id = 1"
            ),
            {"r": rate, "now": now},
        )
        return await status(session)
    total = await count_candidates(session)
    await session.execute(
        text(
            "insert into ext_kiro_push_backfill (id, status, dry_run, rate, cursor, total, scanned, found, absent, blocked, "
            "errors, rate_limited, paused_until, last_error, started_at, finished_at, updated_at) "
            "values (1, 'running', :dry, :r, null, :t, 0, 0, 0, 0, 0, 0, null, null, :now, null, :now) "
            "on conflict (id) do update set status = 'running', dry_run = :dry, rate = :r, cursor = null, total = :t, "
            "scanned = 0, found = 0, absent = 0, blocked = 0, errors = 0, rate_limited = 0, paused_until = null, "
            "last_error = null, started_at = :now, finished_at = null, updated_at = :now"
        ),
        {"dry": dry_run, "r": rate, "t": total, "now": now},
    )
    return await status(session)


async def stop(session: Any) -> dict[str, Any]:
    await session.execute(
        text("update ext_kiro_push_backfill set status = 'stopped', updated_at = now() where id = 1 and status = 'running'")
    )
    return await status(session)


# ------------------------------------------------------------------ scanning


async def scan_user(
    factory: Any,
    gateway: Gateway,
    user_id: int,
    telegram_id: int,
    *,
    dry_run: bool = False,
    sleep: Sleep = asyncio.sleep,
    on_rate_limit: Callable[[int], Awaitable[None]] | None = None,
    max_retries: int = 3,
    now: Clock = _now,
) -> str:
    """Look one user up and store the outcome (unless dry_run). Returns found | absent | blocked | error | locked.

    A 429 answer is waited out (``retry_after`` seconds, capped) and the same user is asked again.
    """
    lookup = BirthdateLookup("error", error="no_attempt")
    for _ in range(max_retries + 1):
        lookup = await gateway.lookup_birthdate(telegram_id)
        if lookup.status != "retry_after":
            break
        wait = min(max(lookup.retry_after, 1), MAX_RETRY_WAIT)
        if on_rate_limit is not None:
            await on_rate_limit(wait)
        await sleep(wait + 0.5)
    else:
        lookup = BirthdateLookup("error", error="rate_limited")
    if lookup.status == "retry_after":
        lookup = BirthdateLookup("error", error="rate_limited")
    if dry_run:
        return lookup.status if lookup.status in ("found", "absent", "blocked") else "error"
    async with factory() as session:
        outcome = await birthdates.store_lookup(session, user_id, lookup, now=now())
        await session.commit()
    return outcome


_COUNTER = {"found": "found", "locked": "found", "absent": "absent", "blocked": "blocked", "error": "errors"}


async def run_chunk(
    factory: Any,
    gateway: Gateway,
    *,
    budget_seconds: float = 40.0,
    clock: Clock = _now,
    sleep: Sleep = asyncio.sleep,
    monotonic: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """Advance a running backfill for at most ``budget_seconds``; persists the cursor and counters per chunk."""
    import time

    mono = monotonic or time.monotonic
    started = mono()
    async with factory() as session:
        state = (await session.execute(text("select * from ext_kiro_push_backfill where id = 1"))).mappings().first()
    if state is None or state["status"] != "running":
        return {"ran": False}
    if state["paused_until"] is not None and state["paused_until"] > clock():
        return {"ran": False, "paused": True}
    dry_run, rate = bool(state["dry_run"]), int(state["rate"])
    cursor = state["cursor"] if state["cursor"] is not None else -(2**62)
    delay = 1.0 / rate
    processed = 0
    finished = False
    while mono() - started < budget_seconds:
        async with factory() as session:
            live = await session.scalar(text("select status from ext_kiro_push_backfill where id = 1"))
            if live != "running":
                break
            rows = (
                await session.execute(
                    text(
                        f"select u.user_id, u.telegram_id from users u where {_ELIGIBLE} and {_NO_ROW} "
                        "and u.user_id > :c order by u.user_id limit :n"
                    ),
                    {"c": cursor, "n": CHUNK},
                )
            ).all()
        if not rows:
            finished = True
            break
        delta = {"scanned": 0, "found": 0, "absent": 0, "blocked": 0, "errors": 0, "rate_limited": 0}
        paused_for = 0

        async def limited(wait: int) -> None:
            nonlocal paused_for
            delta["rate_limited"] += 1
            paused_for = wait

        last_id = cursor
        for user_id, telegram_id in rows:
            if mono() - started >= budget_seconds:
                break
            if delta["scanned"] and delta["scanned"] % STOP_CHECK == 0:
                async with factory() as session:
                    if await session.scalar(text("select status from ext_kiro_push_backfill where id = 1")) != "running":
                        break
            outcome = await scan_user(
                factory, gateway, int(user_id), int(telegram_id), dry_run=dry_run, sleep=sleep, on_rate_limit=limited, now=clock,
            )
            delta["scanned"] += 1
            delta[_COUNTER.get(outcome, "errors")] += 1
            last_id = int(user_id)
            processed += 1
            await sleep(delay)
        async with factory() as session:
            await session.execute(
                text(
                    "update ext_kiro_push_backfill set cursor = :c, scanned = scanned + :s, found = found + :f, "
                    "absent = absent + :a, blocked = blocked + :b, errors = errors + :e, "
                    "rate_limited = rate_limited + :rl, updated_at = now() "
                    "where id = 1"
                ),
                {"c": last_id, "s": delta["scanned"], "f": delta["found"], "a": delta["absent"], "b": delta["blocked"],
                 "e": delta["errors"], "rl": delta["rate_limited"]},
            )
            await session.commit()
        cursor = last_id
        if paused_for:
            logger.info("kiro-push: backfill rate-limited by Telegram, waited %ss", paused_for)
    if finished:
        async with factory() as session:
            await session.execute(
                text(
                    "update ext_kiro_push_backfill set status = 'done', finished_at = now(), updated_at = now() "
                    "where id = 1 and status = 'running'"
                )
            )
            await session.commit()
    return {"ran": True, "processed": processed, "finished": finished}


# ------------------------------------------------------------------ new users / re-scan


async def scan_new_users(
    factory: Any, gateway: Gateway, config: dict[str, Any], *, limit: int = 50, sleep: Sleep = asyncio.sleep, clock: Clock = _now
) -> dict[str, int]:
    """Users registered after the plugin started: look their birthdate up once (the backfill covers older ones)."""
    out = {"scanned": 0, "found": 0}
    async with factory() as session:
        cursor = await get_meta(session, NEW_USER_CURSOR)
        if not isinstance(cursor, int):
            top = await session.scalar(text("select coalesce(max(user_id), 0) from users"))
            await set_meta(session, NEW_USER_CURSOR, int(top or 0))
            await session.commit()
            return out
        window = [
            int(r[0])
            for r in (
                await session.execute(
                    text("select user_id from users where user_id > :c order by user_id limit :n"), {"c": cursor, "n": limit}
                )
            ).all()
        ]
        if not window:
            return out
        rows = (
            await session.execute(
                text(
                    f"select u.user_id, u.telegram_id from users u where u.user_id = any(cast(:ids as bigint[])) "
                    f"and {_ELIGIBLE} and {_NO_ROW} order by u.user_id"
                ),
                {"ids": window},
            )
        ).all()
    # users that cannot be looked up yet (no telegram_id) are passed over: account.telegram_linked covers them
    delay = 1.0 / int(config["scan_rate_per_second"])
    for user_id, telegram_id in rows:
        outcome = await scan_user(factory, gateway, int(user_id), int(telegram_id), sleep=sleep, now=clock)
        out["scanned"] += 1
        out["found"] += outcome == "found"
        await sleep(delay)
    async with factory() as session:
        await set_meta(session, NEW_USER_CURSOR, window[-1])
        await session.commit()
    return out


async def rescan_missing(
    factory: Any, gateway: Gateway, config: dict[str, Any], *, limit: int = 50, sleep: Sleep = asyncio.sleep, clock: Clock = _now
) -> dict[str, int]:
    """Users without a visible date are asked again every ``rescan_missing_days`` (at most ``rescan_max_attempts`` times)."""
    out = {"scanned": 0, "found": 0}
    cutoff = clock() - timedelta(days=int(config["rescan_missing_days"]))
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "select u.user_id, u.telegram_id from ext_kiro_push_birthdates b join users u on u.user_id = b.user_id "
                    f"where b.day is null and b.status in ('absent', 'error') and not b.locked and b.attempts < :max "
                    f"and b.last_attempt_at < :cut and {_ELIGIBLE} order by b.last_attempt_at limit :n"
                ),
                {"max": int(config["rescan_max_attempts"]), "cut": cutoff, "n": limit},
            )
        ).all()
    delay = 1.0 / int(config["scan_rate_per_second"])
    for user_id, telegram_id in rows:
        outcome = await scan_user(factory, gateway, int(user_id), int(telegram_id), sleep=sleep, now=clock)
        out["scanned"] += 1
        out["found"] += outcome == "found"
        await sleep(delay)
    return out


async def scan_one(factory: Any, gateway: Gateway, user_id: int, *, sleep: Sleep = asyncio.sleep, clock: Clock = _now) -> str | None:
    """Event path (user.registered / account.telegram_linked): look one user up unless a row exists."""
    async with factory() as session:
        row = (
            await session.execute(
                text(
                    f"select u.user_id, u.telegram_id from users u where u.user_id = :u and {_ELIGIBLE} and {_NO_ROW}"
                ),
                {"u": user_id},
            )
        ).first()
    if row is None:
        return None
    return await scan_user(factory, gateway, int(row[0]), int(row[1]), sleep=sleep, now=clock)
