"""Birthdates: parsing/validation, lock semantics, customer self-entry, backfill (dry run, resume, 429, blocked), re-scan.

Run:  python tests/run_birthdates_tests.py     (needs: pgserver sqlalchemy[asyncio] asyncpg)
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness as h  # noqa: E402

storage, errors, tg, bd, bf, users = h.load("storage", "errors", "telegram", "birthdates", "backfill", "users")
PushError = errors.PushError
Lookup = tg.BirthdateLookup
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
TODAY = NOW.date()
CFG = {**storage.DEFAULT_CONFIG, "enabled": True}


def pure_tests() -> None:
    print("parsing / validation")
    h.ok(tg.parse_birthdate({"day": 12, "month": 8, "year": 1990}, now=NOW) == (12, 8, 1990), "telegram date with year")
    h.ok(tg.parse_birthdate({"day": 12, "month": 8}, now=NOW) == (12, 8, None), "telegram date without year")
    h.ok(tg.parse_birthdate({"day": 29, "month": 2}, now=NOW) == (29, 2, None), "29 Feb without year is valid")
    h.ok(tg.parse_birthdate({"day": 29, "month": 2, "year": 2001}, now=NOW) == (29, 2, None), "29 Feb with a common year keeps the date only")
    h.ok(tg.parse_birthdate({"day": 29, "month": 2, "year": 2000}, now=NOW) == (29, 2, 2000), "29 Feb in a leap year")
    h.ok(tg.parse_birthdate({"day": 31, "month": 4}, now=NOW) is None, "31 April rejected")
    h.ok(tg.parse_birthdate({"day": 1, "month": 13}, now=NOW) is None, "month 13 rejected")
    h.ok(tg.parse_birthdate({"day": 5, "month": 5, "year": 1800}, now=NOW) == (5, 5, None), "implausible year dropped")
    h.ok(tg.parse_birthdate({"day": 5, "month": 5, "year": 2999}, now=NOW) == (5, 5, None), "future year dropped")
    h.ok(tg.parse_birthdate(None) is None, "absent birthdate -> None")
    h.ok(tg.parse_birthdate(type("B", (), {"day": 3, "month": 4, "year": None})(), now=NOW) == (3, 4, None), "object form")

    c = bd.clean_date
    h.ok(c({"day": 12, "month": 8}, mode="user", today=TODAY) == (12, 8, None), "user: year optional")
    h.ok(c({"day": "12", "month": "8", "year": "1990"}, mode="user", today=TODAY) == (12, 8, 1990), "user: numeric strings")
    h.ok(c({"day": 29, "month": 2}, mode="user", today=TODAY) == (29, 2, None), "user: 29 Feb accepted")
    h.ok(c({"day": 29, "month": 2, "year": 2000}, mode="user", today=TODAY) == (29, 2, 2000), "user: 29 Feb 2000 accepted")
    for body, code, label in (
        ({"day": 29, "month": 2, "year": 2001}, "invalid_date", "29 Feb 2001"),
        ({"day": 31, "month": 6}, "invalid_date", "31 June"),
        ({"day": 0, "month": 6}, "invalid_date", "day 0"),
        ({"day": 1, "month": 6, "year": 2030}, "invalid_year", "future year"),
        ({"day": 1, "month": 6, "year": 2024}, "invalid_year", "age under 5"),
        ({"day": 1, "month": 6, "year": 1900}, "invalid_year", "age over 110"),
        ({"day": 1.5, "month": 6}, "invalid_date", "fractional day"),
        ({"day": True, "month": 6}, "invalid_date", "boolean day"),
        ({"day": "x", "month": 6}, "invalid_date", "garbage day"),
        ({"month": 6}, "invalid_date", "missing day"),
    ):
        try:
            c(body, mode="user", today=TODAY)
        except PushError as exc:
            h.ok(exc.code == code, f"user rejects {label} -> {code}")
        else:
            raise AssertionError(f"user accepted {label}")
    h.ok(bd.age_on(10, 11, 2016, TODAY) == 10, "age on the birthday itself")
    h.ok(bd.age_on(11, 11, 2016, TODAY) == 9, "age the day before the birthday")
    h.ok(c({"day": 10, "month": 11, "year": 2021}, mode="user", today=TODAY)[2] == 2021, "exactly 5 years old is accepted")
    h.ok(c({"day": 1, "month": 1, "year": 1910}, mode="admin", today=TODAY)[2] == 1910, "admin: wider range")
    h.ok(tg.classify_error(type("TelegramRetryAfter", (Exception,), {"retry_after": 7})("x"))[0::2] == ("retry_after", 7), "429 classified")
    h.ok(tg.classify_error(Exception("Bad Request: chat not found"))[0] == "blocked", "chat not found -> blocked")
    h.ok(tg.classify_error(Exception("Forbidden: bot was blocked by the user"))[0] == "blocked", "bot blocked")
    h.ok(tg.classify_error(Exception("Network is down"))[0] == "failed", "network -> failed")
    h.ok(tg.unicode_html('a <tg-emoji emoji-id="5">🎁</tg-emoji> b <b>x</b>') == "a 🎁 b <b>x</b>", "custom emoji -> plain emoji")


async def lock_tests(db: h.Db) -> None:
    print("lock semantics")
    await db.reset()
    for uid in (1, 2, 3, 4):
        await db.add_user(uid)
    async with db.factory() as s:
        out = await bd.store_lookup(s, 1, Lookup("found", 12, 8, 1990), now=NOW)
        await s.commit()
    h.ok(out == "found", "telegram date stored")
    row = await db.one("select * from ext_kiro_push_birthdates where user_id = 1")
    h.ok((row["day"], row["month"], row["year"], row["source"], row["locked"], row["status"]) == (12, 8, 1990, "telegram", True, "found"), "row is locked, source telegram")
    async with db.factory() as s:
        out = await bd.store_lookup(s, 1, Lookup("found", 1, 1, 2000), now=NOW)
        await s.commit()
    row = await db.one("select * from ext_kiro_push_birthdates where user_id = 1")
    h.ok(out == "locked" and (row["day"], row["month"]) == (12, 8), "a stored date is never refreshed from Telegram")
    async with db.factory() as s:
        await bd.store_lookup(s, 1, Lookup("absent"), now=NOW)
        await s.commit()
    h.ok((await db.one("select day, status from ext_kiro_push_birthdates where user_id = 1"))["status"] == "found", "'absent' result does not touch a stored date")
    # absent -> later found
    async with db.factory() as s:
        await bd.store_lookup(s, 2, Lookup("absent"), now=NOW)
        await s.commit()
    row = await db.one("select * from ext_kiro_push_birthdates where user_id = 2")
    h.ok(row["status"] == "absent" and row["attempts"] == 1 and row["day"] is None, "hidden date recorded as absent")
    async with db.factory() as s:
        out = await bd.store_lookup(s, 2, Lookup("found", 5, 5, None), now=NOW)
        await s.commit()
    row = await db.one("select * from ext_kiro_push_birthdates where user_id = 2")
    h.ok(out == "found" and row["attempts"] == 2 and row["year"] is None, "date opened later is picked up (attempts counted)")
    # admin override and delete
    async with db.factory() as s:
        saved = await bd.admin_set(s, 1, {"day": 3, "month": 3, "year": 1991}, "admin:1", today=TODAY)
        await s.commit()
    row = await db.one("select * from ext_kiro_push_birthdates where user_id = 1")
    h.ok(saved["source"] == "admin" and (row["day"], row["month"], row["year"], row["source"], row["locked"]) == (3, 3, 1991, "admin", True), "admin overrides a telegram date")
    async with db.factory() as s:
        out = await bd.store_lookup(s, 1, Lookup("found", 9, 9, None), now=NOW)
        await s.commit()
    h.ok(out == "locked", "telegram cannot override an admin date")
    async with db.factory() as s:
        removed = await bd.admin_delete(s, 1, "admin:1")
        again = await bd.admin_delete(s, 1, "admin:1")
        await s.commit()
    row = await db.one("select * from ext_kiro_push_birthdates where user_id = 1")
    h.ok(removed and not again and row["day"] is None and row["status"] == "cleared", "admin delete leaves a 'cleared' tombstone")
    async with db.factory() as s:
        out = await bd.store_lookup(s, 1, Lookup("found", 9, 9, None), now=NOW)
        await s.commit()
    h.ok(out == "locked" and (await db.one("select day from ext_kiro_push_birthdates where user_id = 1"))["day"] is None, "telegram does not refill a cleared row")
    h.ok(await db.count("ext_kiro_push_birthdate_audit", "user_id = 1") == 3, "audit trail: fetch, set, delete (refused refreshes are not logged)")
    # bad admin input
    async with db.factory() as s:
        await h.raises(bd.admin_set(s, 3, {"day": 30, "month": 2}, "admin"), "invalid_date", "admin: 30 Feb")
        await h.raises(bd.admin_set(s, 3, {"day": 1, "month": 1, "year": 1800}, "admin", today=TODAY), "invalid_year", "admin: year 1800")
        res = await bd.admin_import(s, [{"user": "3", "day": 1, "month": 2}, {"user": "nobody", "day": 1, "month": 2}, {"user": "2", "day": 9, "month": 9}],
                                    "admin", overwrite=False, resolve=users.resolve_user, today=TODAY)
        await s.commit()
    h.ok((res["saved"], res["skipped"], res["errors"]) == (1, 1, 1), "import: saved / skipped existing / unknown user")
    async with db.factory() as s:
        res = await bd.admin_import(s, [{"user": "2", "day": 9, "month": 9}], "admin", overwrite=True, resolve=users.resolve_user, today=TODAY)
        await s.commit()
    h.ok(res["saved"] == 1 and (await db.one("select day from ext_kiro_push_birthdates where user_id = 2"))["day"] == 9, "import with overwrite")


async def customer_tests(db: h.Db) -> None:
    print("customer self-entry")
    await db.reset()
    for uid in (10, 11, 12):
        await db.add_user(uid)
    f = db.factory
    async with f() as s:
        state = await bd.customer_state(s, 10, CFG)
    h.ok(state["state"] == "none" and state["can_set"] and state["enabled"] and "consent_text" in state, "state none, can_set")
    off = {**CFG, "user_entry_enabled": False}
    async with f() as s:
        state = await bd.customer_state(s, 10, off)
    h.ok(not state["enabled"] and not state["can_set"], "entry switched off -> card hidden, cannot set")
    await h.raises(bd.user_set(f, 10, {"day": 1, "month": 1, "consent": True}, off, now=NOW), "disabled", "set while entry is off")
    await h.raises(bd.user_set(f, 10, {"day": 1, "month": 1, "consent": True}, {**CFG, "enabled": False}, now=NOW), "disabled", "set while plugin is off")
    await h.raises(bd.user_set(f, 10, {"day": 1, "month": 1}, CFG, now=NOW), "consent_required", "no consent")
    await h.raises(bd.user_set(f, 10, {"day": 31, "month": 2, "consent": True}, CFG, now=NOW), "invalid_date", "invalid date")
    await h.raises(bd.user_set(f, 10, {"day": 1, "month": 1, "year": 2030, "consent": True}, CFG, now=NOW), "invalid_year", "future year")
    h.ok(await db.count("ext_kiro_push_birthdates") == 0, "nothing stored by failed attempts")
    state = await bd.user_set(f, 10, {"day": 29, "month": 2, "consent": True}, CFG, now=NOW)
    h.ok(state["state"] == "set" and (state["day"], state["month"]) == (29, 2) and "year" not in state and state["locked"] and not state["can_set"], "29 Feb accepted, no year")
    row = await db.one("select * from ext_kiro_push_birthdates where user_id = 10")
    h.ok((row["source"], row["locked"], row["status"]) == ("user", True, "found"), "stored with source 'user', locked")
    audit = await db.one("select * from ext_kiro_push_birthdate_audit where user_id = 10")
    h.ok(audit["actor"] == "user:10" and audit["action"] == "set" and audit["new_value"] == "29.02", "audit: who / when / source")
    await h.raises(bd.user_set(f, 10, {"day": 1, "month": 1, "consent": True}, CFG, now=NOW), "already_set", "second set rejected")
    row = await db.one("select * from ext_kiro_push_birthdates where user_id = 10")
    h.ok(row["day"] == 29, "second set did not change the date")
    # year given
    state = await bd.user_set(f, 11, {"day": 12, "month": 8, "year": 1990, "consent": True}, CFG, now=NOW)
    h.ok(state["year"] == 1990, "year stored when given")
    # user placeholder row (telegram 'absent') can be filled by the user, but then telegram must not override
    await db.exec("insert into ext_kiro_push_birthdates (user_id, status, attempts, last_attempt_at) values (12, 'absent', 1, now())")
    state = await bd.user_set(f, 12, {"day": 7, "month": 7, "consent": True}, CFG, now=NOW)
    h.ok(state["state"] == "set", "user fills an 'absent' placeholder row")
    async with f() as s:
        out = await bd.store_lookup(s, 12, Lookup("found", 1, 2, None), now=NOW)
        await s.commit()
    h.ok(out == "locked" and (await db.one("select day, source from ext_kiro_push_birthdates where user_id = 12"))["source"] == "user", "telegram rescan does not override a user date")
    # admin deletes -> the user may enter again
    async with f() as s:
        await bd.admin_delete(s, 11, "admin:1")
        await s.commit()
    state = await bd.user_set(f, 11, {"day": 13, "month": 8, "consent": True}, CFG, now=NOW)
    h.ok(state["day"] == 13, "after an admin delete the user can set the date again")
    # admin edit of a user date
    async with f() as s:
        await bd.admin_set(s, 11, {"day": 14, "month": 8}, "admin:1", today=TODAY)
        await s.commit()
    h.ok((await db.one("select day, source from ext_kiro_push_birthdates where user_id = 11"))["source"] == "admin", "admin edits a user date")
    await h.raises(bd.user_set(f, 11, {"day": 1, "month": 1, "consent": True}, CFG, now=NOW), "already_set", "still locked after admin edit")
    # rate limit: 5 attempts per hour
    await db.add_user(13)
    for _ in range(5):
        try:
            await bd.user_set(f, 13, {"day": 40, "month": 1, "consent": True}, CFG, now=NOW)
        except PushError:
            pass
    await h.raises(bd.user_set(f, 13, {"day": 1, "month": 1, "consent": True}, CFG, now=NOW), "rate_limited", "6th attempt within the hour")
    await db.add_user(14)
    state = await bd.user_set(f, 14, {"day": 1, "month": 1, "consent": True}, CFG, now=NOW)
    h.ok(state["state"] == "set", "rate limit is per user")
    await db.exec("update ext_kiro_push_user_attempts set created_at = now() - interval '2 hours' where user_id = 13")
    state = await bd.user_set(f, 13, {"day": 1, "month": 1, "consent": True}, CFG, now=NOW)
    h.ok(state["state"] == "set", "attempts expire after an hour")


async def backfill_tests(db: h.Db) -> None:
    print("backfill")
    await db.reset()
    gw = h.FakeGateway()
    # 10 users: 1-3 expose a date, 4 hidden, 5 blocked, 6 error, 7 banned (NULL-safe), 8 banned NULL, 9 no telegram, 10 already has a row
    for uid in range(1, 11):
        await db.add_user(uid, tg=uid * 10)
    await db.exec("update users set is_banned = true where user_id = 7")
    await db.exec("update users set is_banned = null where user_id = 8")
    await db.exec("update users set telegram_id = null where user_id = 9")
    await db.exec("insert into ext_kiro_push_birthdates (user_id, day, month, source, locked, status) values (10, 1, 1, 'admin', true, 'found')")
    for uid in (1, 2, 3):
        gw.lookups[uid * 10] = [Lookup("found", uid, uid, 1990 + uid)]
    gw.lookups[50] = [Lookup("blocked", error="chat not found")]
    gw.lookups[60] = [Lookup("error", error="boom")]
    gw.lookups[80] = [Lookup("found", 8, 8, None)]
    f = db.factory

    async with f() as s:
        st = await bf.start(s, dry_run=True, rate=20, now=NOW)
        await s.commit()
    h.ok(st["status"] == "running" and st["dry_run"] and st["total"] == 7, "dry run counts candidates (banned/no-telegram/has-row excluded, NULL banned included)")
    async with f() as s:
        await h.raises(bf.start(s, dry_run=False, rate=20), "already_running", "second start while running", PushError)
    res = await bf.run_chunk(f, gw, budget_seconds=30, clock=lambda: NOW, sleep=h.no_sleep)
    st = await _status(db)
    h.ok(res["finished"] and st["status"] == "done", "dry run finishes")
    h.ok((st["scanned"], st["found"], st["absent"], st["blocked"], st["errors"]) == (7, 4, 1, 1, 1), f"dry-run counters {st['scanned']}/{st['found']}/{st['absent']}/{st['blocked']}/{st['errors']}")
    h.ok(await db.count("ext_kiro_push_birthdates") == 1, "dry run stores NOTHING (only the pre-existing row)")
    h.ok(st["hit_rate"] == round(4 / 7, 4), "hit rate reported")
    h.ok(len(gw.lookup_calls) == 7, "banned / no-telegram / known users were not asked")
    h.ok(7 * 10 not in gw.lookup_calls and 90 not in gw.lookup_calls and 100 not in gw.lookup_calls, "skipped users never reach Telegram")

    # real run with a stop in the middle and a resume
    gw.lookup_calls.clear()
    async with f() as s:
        await bf.start(s, dry_run=False, rate=20, now=NOW)
        await s.commit()
    calls = {"n": 0}

    class StopAfter(h.FakeGateway):
        async def lookup_birthdate(self, chat_id):
            calls["n"] += 1
            if calls["n"] == 3:
                async with f() as s:
                    await bf.stop(s)
                    await s.commit()
            return await super().lookup_birthdate(chat_id)

    gw2 = StopAfter(lookups=gw.lookups)
    await bf.run_chunk(f, gw2, budget_seconds=30, clock=lambda: NOW, sleep=h.no_sleep)
    st = await _status(db)
    h.ok(st["status"] == "stopped" and st["can_resume"] and 0 < st["scanned"] < 7, f"stopped midway ({st['scanned']} scanned)")
    done_first = st["scanned"]
    stored = await db.count("ext_kiro_push_birthdates")
    h.ok(stored == 1 + done_first, "real run stores a row for every scanned user")
    async with f() as s:
        await bf.start(s, dry_run=False, rate=20, resume=True, now=NOW)
        await s.commit()
    await bf.run_chunk(f, h.FakeGateway(lookups=gw.lookups), budget_seconds=30, clock=lambda: NOW, sleep=h.no_sleep)
    st = await _status(db)
    h.ok(st["status"] == "done" and st["scanned"] == 7, "resume continues from the cursor and completes (no user scanned twice)")
    rows = {r["user_id"]: r for r in await db.q("select * from ext_kiro_push_birthdates")}
    h.ok((rows[1]["day"], rows[1]["source"], rows[1]["locked"]) == (1, "telegram", True), "found date stored and locked")
    h.ok(rows[4]["status"] == "absent" and rows[5]["status"] == "blocked" and rows[6]["status"] == "error", "absent / blocked / error marked")
    h.ok(rows[8]["day"] == 8, "user with NULL is_banned scanned")
    h.ok(rows[10]["source"] == "admin" and rows[10]["day"] == 1, "existing row untouched")
    h.ok(7 not in rows and 9 not in rows, "banned and telegram-less users get no row")

    # restart from scratch is allowed once finished
    async with f() as s:
        st = await bf.start(s, dry_run=True, rate=5, now=NOW)
        await s.commit()
    h.ok(st["status"] == "running" and st["scanned"] == 0 and st["total"] == 0 and st["rate"] == 5, "new run after done resets counters")
    res = await bf.run_chunk(f, gw, budget_seconds=30, clock=lambda: NOW, sleep=h.no_sleep)
    h.ok(res["finished"] and (await _status(db))["status"] == "done", "empty run completes")
    async with f() as s:
        await h.raises(bf.start(s, dry_run=False, rate=20, resume=True), "nothing_to_resume", "resume without a stopped run", PushError)
    await h.raises(_async(bf.clean_rate, "0", 20), "invalid_rate", "rate 0 rejected", PushError)
    await h.raises(_async(bf.clean_rate, "99", 20), "invalid_rate", "rate 99 rejected", PushError)
    h.ok(bf.clean_rate(None, 20) == 20 and bf.clean_rate("7", 20) == 7, "rate parsing")

    # 429 handling
    print("backfill 429")
    await db.reset()
    for uid in (1, 2, 3):
        await db.add_user(uid, tg=uid * 10)
    waits: list[float] = []

    async def record_sleep(seconds: float) -> None:
        waits.append(seconds)

    gw3 = h.FakeGateway()
    gw3.lookups[20] = [Lookup("retry_after", retry_after=7), Lookup("retry_after", retry_after=2), Lookup("found", 2, 2, None)]
    async with f() as s:
        await bf.start(s, dry_run=False, rate=20, now=NOW)
        await s.commit()
    await bf.run_chunk(f, gw3, budget_seconds=30, clock=lambda: NOW, sleep=record_sleep)
    st = await _status(db)
    h.ok(st["status"] == "done" and st["scanned"] == 3 and st["found"] == 1 and st["errors"] == 0, "user retried after retry_after and then found")
    h.ok(any(w >= 7 for w in waits) and any(2 <= w < 7 for w in waits), f"retry_after honoured (waited {sorted(set(waits))})")
    h.ok(st["rate_limited"] == 2 and gw3.lookup_calls.count(20) == 3, "rate limit events counted, same user asked again")
    # permanent 429 -> error, run goes on
    await db.reset()
    for uid in (1, 2):
        await db.add_user(uid, tg=uid * 10)
    gw4 = h.FakeGateway()
    gw4.lookups[10] = [Lookup("retry_after", retry_after=1)]
    async with f() as s:
        await bf.start(s, dry_run=False, rate=20, now=NOW)
        await s.commit()
    await bf.run_chunk(f, gw4, budget_seconds=30, clock=lambda: NOW, sleep=h.no_sleep)
    st = await _status(db)
    h.ok(st["status"] == "done" and st["errors"] == 1 and st["scanned"] == 2, "endless 429 for one user becomes an error, the run continues")
    # throttle: sleep between users
    await db.reset()
    for uid in (1, 2, 3, 4):
        await db.add_user(uid, tg=uid * 10)
    waits.clear()
    async with f() as s:
        await bf.start(s, dry_run=True, rate=10, now=NOW)
        await s.commit()
    await bf.run_chunk(f, h.FakeGateway(), budget_seconds=30, clock=lambda: NOW, sleep=record_sleep)
    h.ok(waits.count(0.1) == 4, "throttled to the configured rate (10/s -> 0.1 s per user)")
    # time budget
    await db.reset()
    for uid in range(1, 21):
        await db.add_user(uid, tg=uid * 10)
    ticks = iter(range(0, 10_000))
    async with f() as s:
        await bf.start(s, dry_run=False, rate=20, now=NOW)
        await s.commit()
    await bf.run_chunk(f, h.FakeGateway(), budget_seconds=5, clock=lambda: NOW, sleep=h.no_sleep, monotonic=lambda: next(ticks) * 0.5)
    st = await _status(db)
    h.ok(st["status"] == "running" and 0 < st["scanned"] < 20, f"time budget respected, cursor kept ({st['scanned']} scanned, running)")
    for _ in range(5):
        await bf.run_chunk(f, h.FakeGateway(), budget_seconds=5, clock=lambda: NOW, sleep=h.no_sleep, monotonic=lambda: next(ticks) * 0.5)
    st = await _status(db)
    h.ok(st["status"] == "done" and st["scanned"] == 20 and await db.count("ext_kiro_push_birthdates") == 20, "several ticks complete the run exactly once per user")
    h.ok(st["progress"] == 1.0, "progress 100%")


async def _async(fn, *args):
    return fn(*args)


async def _status(db: h.Db) -> dict:
    async with db.factory() as s:
        return await bf.status(s)


async def scan_tests(db: h.Db) -> None:
    print("new users / re-scan / events")
    await db.reset()
    f = db.factory
    for uid in (1, 2):
        await db.add_user(uid, tg=uid * 10)
    gw = h.FakeGateway()
    res = await bf.scan_new_users(f, gw, CFG, sleep=h.no_sleep, clock=lambda: NOW)
    h.ok(res["scanned"] == 0 and not gw.lookup_calls, "first pass only sets the cursor (old users are the backfill's job)")
    await db.add_user(3, tg=30)
    await db.add_user(4, tg=None)
    await db.exec("update users set telegram_id = null where user_id = 4")
    await db.add_user(5, tg=50, banned=True)
    gw.lookups[30] = [Lookup("found", 4, 4, 1995)]
    res = await bf.scan_new_users(f, gw, CFG, sleep=h.no_sleep, clock=lambda: NOW)
    h.ok(res == {"scanned": 1, "found": 1} and gw.lookup_calls == [30], "new user scanned; telegram-less and banned ones are not")
    h.ok((await db.one("select day, source from ext_kiro_push_birthdates where user_id = 3"))["source"] == "telegram", "new user's date stored")
    res = await bf.scan_new_users(f, gw, CFG, sleep=h.no_sleep, clock=lambda: NOW)
    h.ok(res["scanned"] == 0, "cursor advanced: nobody is scanned twice")
    # event path
    await db.add_user(6, tg=60)
    gw.lookups[60] = [Lookup("found", 6, 6, None)]
    h.ok(await bf.scan_one(f, gw, 6, sleep=h.no_sleep, clock=lambda: NOW) == "found", "scan_one (user.registered)")
    h.ok(await bf.scan_one(f, gw, 6, sleep=h.no_sleep, clock=lambda: NOW) is None, "scan_one skips users that already have a row")
    h.ok(await bf.scan_one(f, gw, 5, sleep=h.no_sleep, clock=lambda: NOW) is None, "scan_one skips banned users")
    # re-scan
    await db.reset()
    for uid in range(1, 6):
        await db.add_user(uid, tg=uid * 10)
    old = NOW - timedelta(days=40)
    await db.exec("insert into ext_kiro_push_birthdates (user_id, status, attempts, last_attempt_at) values (1, 'absent', 1, :t)", t=old)
    await db.exec("insert into ext_kiro_push_birthdates (user_id, status, attempts, last_attempt_at) values (2, 'absent', 3, :t)", t=old)
    await db.exec("insert into ext_kiro_push_birthdates (user_id, status, attempts, last_attempt_at) values (3, 'absent', 1, :t)", t=NOW - timedelta(days=5))
    await db.exec("insert into ext_kiro_push_birthdates (user_id, status, attempts, last_attempt_at) values (4, 'blocked', 1, :t)", t=old)
    await db.exec("insert into ext_kiro_push_birthdates (user_id, status, attempts, last_attempt_at) values (5, 'cleared', 1, :t)", t=old)
    gw = h.FakeGateway()
    gw.lookups[10] = [Lookup("found", 2, 2, None)]
    res = await bf.rescan_missing(f, gw, CFG, sleep=h.no_sleep, clock=lambda: NOW)
    h.ok(gw.lookup_calls == [10], "re-scan asks only users that are old enough, under the attempt cap, not blocked/cleared")
    h.ok(res["found"] == 1 and (await db.one("select day from ext_kiro_push_birthdates where user_id = 1"))["day"] == 2, "a date opened later is stored")
    res = await bf.rescan_missing(f, gw, CFG, sleep=h.no_sleep, clock=lambda: NOW)
    h.ok(res["scanned"] == 0, "found user is not re-scanned")
    # a user who entered the date himself between scans is not overridden
    await db.exec("insert into ext_kiro_push_birthdates (user_id, status, attempts, last_attempt_at) values (6, 'absent', 1, :t)", t=old)
    await db.add_user(6, tg=60)
    gw.lookups[60] = [Lookup("found", 9, 9, None)]
    await bd.user_set(f, 6, {"day": 1, "month": 2, "consent": True}, CFG, now=NOW)
    await bf.rescan_missing(f, gw, CFG, sleep=h.no_sleep, clock=lambda: NOW)
    h.ok((await db.one("select day, source from ext_kiro_push_birthdates where user_id = 6"))["source"] == "user", "re-scan leaves a user-entered date alone")


async def main() -> None:
    pure_tests()
    db = await h.make_db()
    await lock_tests(db)
    await customer_tests(db)
    await backfill_tests(db)
    await scan_tests(db)
    h.finish()


if __name__ == "__main__":
    asyncio.run(main())
