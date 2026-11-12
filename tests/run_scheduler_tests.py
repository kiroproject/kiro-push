"""Scheduler: due selection per event kind (timezones, DST, Feb 29, offsets), planning, audience filters, idempotency,
delivery states (sent / skipped / failed / blocked), 429 handling, crash recovery, manual runs, stats.

Run:  python tests/run_scheduler_tests.py     (needs: pgserver sqlalchemy[asyncio] asyncpg)
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness as h  # noqa: E402

storage, errors, tg, ev, sch, st, users = h.load("storage", "errors", "telegram", "eventdefs", "scheduler", "stats", "users")
PushError = errors.PushError
MSK = ZoneInfo("Europe/Moscow")
NY = ZoneInfo("America/New_York")
CFG = {**storage.DEFAULT_CONFIG, "enabled": True, "send_rate_per_second": 28}


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def event(kind: str, send_time: str = "10:00", feb29: str = "feb28", **params) -> dict:
    return {"id": 1, "kind": kind, "send_time": send_time, "feb29": feb29, "params": params}


def keys(slots) -> list[tuple[str, str]]:
    return [(s.local_date.isoformat(), s.period_key) for s in slots]


def pure_tests() -> None:
    print("due slots")
    b = event("birthday")
    h.ok(ev.due_slots(b, utc(2026, 11, 10, 6, 59), MSK, 24) == [] or all(s.local_date != date(2026, 11, 10) for s in ev.due_slots(b, utc(2026, 11, 10, 6, 59), MSK, 24)), "09:59 Moscow: today's 10:00 is not due yet")
    h.ok(keys(ev.due_slots(b, utc(2026, 11, 10, 7, 0), MSK, 24))[0] == ("2026-11-10", "2026"), "10:00 Moscow sharp is due")
    h.ok(keys(ev.due_slots(b, utc(2026, 11, 10, 7, 0), MSK, 24)) == [("2026-11-10", "2026")], "exactly 24 h after yesterday's slot only today's is due")
    h.ok(keys(ev.due_slots(b, utc(2026, 11, 10, 6, 0), MSK, 24)) == [("2026-11-09", "2026")], "before today's send time yesterday's missed slot is caught up")
    h.ok(keys(ev.due_slots(b, utc(2026, 11, 10, 12, 0), MSK, 48)) == [("2026-11-10", "2026"), ("2026-11-09", "2026")], "wider catch-up window: both days")
    h.ok(ev.due_slots(b, utc(2026, 11, 12, 12, 0), MSK, 24) == [] or all(s.local_date != date(2026, 11, 9) for s in ev.due_slots(b, utc(2026, 11, 12, 12, 0), MSK, 24)), "old slots expire")
    # timezone boundary: 21:30Z on 9 Nov is already 10 Nov 00:30 in Moscow (send 00:10)
    midnight = event("birthday", "00:10")
    got = ev.due_slots(midnight, utc(2026, 11, 9, 21, 30), MSK, 24)
    h.ok(got and got[0].local_date == date(2026, 11, 10), "local date follows the configured timezone, not UTC")
    h.ok(got[0].scheduled_for == utc(2026, 11, 9, 21, 10), "scheduled_for is the exact UTC instant of the local send time")
    # DST (New York, spring forward on 2026-03-08 at 02:00)
    ny = event("birthday", "10:00")
    h.ok(not any(s.local_date == date(2026, 3, 7) for s in ev.due_slots(ny, utc(2026, 3, 7, 14, 59), NY, 24)), "NY 7 Mar (EST): 14:59Z is 09:59 -> not due")
    h.ok(any(s.local_date == date(2026, 3, 7) for s in ev.due_slots(ny, utc(2026, 3, 7, 15, 0), NY, 24)), "NY 7 Mar (EST): 15:00Z is 10:00 -> due")
    h.ok(not any(s.local_date == date(2026, 3, 8) for s in ev.due_slots(ny, utc(2026, 3, 8, 13, 59), NY, 24)), "NY 8 Mar (EDT, DST day): 13:59Z is 09:59 -> not due")
    h.ok(any(s.local_date == date(2026, 3, 8) for s in ev.due_slots(ny, utc(2026, 3, 8, 14, 0), NY, 24)), "NY 8 Mar (EDT): 14:00Z is 10:00 -> due (wall clock, DST-agnostic)")
    h.ok(any(s.local_date == date(2026, 11, 1) for s in ev.due_slots(ny, utc(2026, 11, 1, 15, 0), NY, 24)), "NY 1 Nov (fall back day): 10:00 EST = 15:00Z -> due")
    h.ok(not any(s.local_date == date(2026, 11, 1) for s in ev.due_slots(ny, utc(2026, 11, 1, 14, 59), NY, 24)), "NY 1 Nov: 14:59Z is 09:59 EST -> not due")
    # a nonexistent wall time (02:30 on the spring-forward day) still fires once, just after the gap
    gap = event("birthday", "02:30")
    got = ev.due_slots(gap, utc(2026, 3, 8, 7, 31), NY, 24)
    h.ok(any(s.local_date == date(2026, 3, 8) for s in got), "send time inside the DST gap fires once the gap is over")
    # offset
    off = event("birthday", offset_days=-1)
    s = ev.due_slots(off, utc(2026, 11, 10, 7, 0), MSK, 1)[0]
    h.ok(s.local_date == date(2026, 11, 10) and s.base_date == date(2026, 11, 11), "offset -1: sent on the 10th for an 11th birthday")
    off = event("birthday", offset_days=1)
    s = ev.due_slots(off, utc(2026, 11, 10, 7, 0), MSK, 1)[0]
    h.ok(s.base_date == date(2026, 11, 9), "offset +1: sent on the 10th for a 9th birthday")
    new_year = event("birthday", offset_days=-1)
    s = ev.due_slots(new_year, utc(2026, 12, 31, 7, 0), MSK, 1)[0]
    h.ok(s.base_date == date(2027, 1, 1) and s.period_key == "2027", "offset across New Year: period is the birthday's year")
    # Feb 29
    h.ok(ev.month_day_keys(date(2027, 2, 28), "feb28") == [228, 229], "feb28 rule: 29 Feb people match 28 Feb in a common year")
    h.ok(ev.month_day_keys(date(2027, 3, 1), "feb28") == [301], "feb28 rule: not on 1 Mar")
    h.ok(ev.month_day_keys(date(2027, 3, 1), "mar1") == [301, 229], "mar1 rule: 29 Feb people match 1 Mar")
    h.ok(ev.month_day_keys(date(2027, 2, 28), "mar1") == [228], "mar1 rule: not on 28 Feb")
    h.ok(ev.month_day_keys(date(2028, 2, 28), "feb28") == [228] and ev.month_day_keys(date(2028, 2, 29), "feb28") == [229], "leap year: each on its own day")
    # fixed date
    ny_eve = event("fixed_date", date="12-31")
    h.ok(keys(ev.due_slots(ny_eve, utc(2026, 12, 31, 7, 0), MSK, 1)) == [("2026-12-31", "2026")], "fixed date 12-31")
    h.ok(ev.due_slots(ny_eve, utc(2026, 12, 30, 7, 0), MSK, 1) == [], "fixed date: other days are not due")
    leap_day = event("fixed_date", feb29="feb28", date="02-29")
    h.ok(keys(ev.due_slots(leap_day, utc(2027, 2, 28, 7, 0), MSK, 1)) == [("2027-02-28", "2027")], "fixed 02-29 -> 28 Feb in a common year (feb28)")
    leap_day = event("fixed_date", feb29="mar1", date="02-29")
    h.ok(keys(ev.due_slots(leap_day, utc(2027, 3, 1, 7, 0), MSK, 1)) == [("2027-03-01", "2027")], "fixed 02-29 -> 1 Mar in a common year (mar1)")
    h.ok(keys(ev.due_slots(leap_day, utc(2028, 2, 29, 7, 0), MSK, 1)) == [("2028-02-29", "2028")], "fixed 02-29 in a leap year")
    # one time
    one = event("one_time", date="2026-11-12")
    h.ok(keys(ev.due_slots(one, utc(2026, 11, 12, 7, 0), MSK, 24)) == [("2026-11-12", "2026-11-12")], "one-time: its own date, period = the date")
    h.ok(ev.due_slots(one, utc(2026, 11, 11, 7, 0), MSK, 24) == [], "one-time: not before the date")
    h.ok(ev.due_slots(one, utc(2026, 11, 14, 7, 0), MSK, 24) == [], "one-time: not after the catch-up window")
    h.ok(ev.due_slots(event("manual"), utc(2026, 11, 12, 7, 0), MSK, 24) == [], "manual events are never due")
    h.ok(keys(ev.due_slots(event("registration_anniversary", min_years=1), utc(2026, 5, 17, 7, 0), MSK, 1)) == [("2026-05-17", "2026")], "anniversary slot")

    print("event validation")
    base = {"name": "Д", "kind": "birthday", "send_time": "10:00", "templates": {"ru": {"text": "С днём рождения, {name}!"}}, "enabled": True}
    c = ev.clean_event(base, wheel_installed=False)
    h.ok(c["params"] == {"offset_days": 0} and c["audience"]["segments"] == ["none", "trial", "paid"] and c["gift"] == {"kind": "none"}, "defaults")
    for patch, code in (
        ({"kind": "weekly"}, "invalid_kind"),
        ({"send_time": "25:00"}, "invalid_send_time"),
        ({"send_time": "9:00"}, "invalid_send_time"),
        ({"params": {"offset_days": 99}}, "invalid_offset_days"),
        ({"feb29": "apr1"}, "invalid_feb29"),
        ({"name": " "}, "name_required"),
        ({"gift_fallback": "x"}, "invalid_gift_fallback"),
        ({"audience": {"segments": ["vip"]}}, "invalid_audience"),
        ({"audience": {"segments": []}}, "audience_empty"),
        ({"audience": {"language": "de"}}, "invalid_audience_language"),
        ({"kind": "fixed_date", "params": {"date": "13-40"}}, "invalid_date"),
        ({"kind": "one_time", "params": {"date": "tomorrow"}}, "invalid_date"),
        ({"gift": {"kind": "wheel_spins", "spins": 1}}, "wheel_not_installed"),
        ({"gift": {"kind": "days", "days": 0}}, "invalid_gift_days"),
        ({"gift": {"kind": "traffic", "gb": -1}}, "invalid_gift_gb"),
        ({"gift": {"kind": "balance", "amount_minor": 0}}, "invalid_gift_amount"),
        ({"gift": {"kind": "promo_discount", "percent": 101}}, "invalid_gift_percent"),
        ({"gift": {"kind": "bonus"}}, "invalid_gift_kind"),
    ):
        try:
            ev.clean_event({**base, **patch}, wheel_installed=False)
        except PushError as exc:
            h.ok(exc.code == code, f"rejects {list(patch)[0]}={str(list(patch.values())[0])[:30]} -> {code}")
        else:
            raise AssertionError(f"accepted {patch}")
    try:
        ev.clean_event({**base, "templates": {}}, wheel_installed=False)
    except PushError as exc:
        h.ok(exc.code == "invalid_event" and exc.extra["issues"], "enabled event without templates is rejected")
    h.ok(ev.clean_event({**base, "templates": {}, "enabled": False}, wheel_installed=False)["templates"] == {}, "disabled draft without templates is fine")
    h.ok(ev.clean_event({**base, "gift": {"kind": "wheel_spins", "spins": 2}}, wheel_installed=True)["gift"]["spins"] == 2, "wheel gift accepted when the wheel is installed")
    h.ok(ev.clean_event({**base, "audience": {"segment": "paid"}}, wheel_installed=False)["audience"]["segments"] == ["paid"], "single 'segment' form accepted")
    h.ok(ev.clean_event({**base, "kind": "fixed_date", "params": {"date": "02-29"}}, wheel_installed=False)["params"]["date"] == "02-29", "fixed date 02-29 accepted")


async def add_event(db: h.Db, kind: str = "birthday", *, enabled: bool = True, send_time: str = "10:00", feb29: str = "feb28",
                    params: dict | None = None, audience: dict | None = None, gift: dict | None = None, fallback: str = "send_without_gift",
                    templates: dict | None = None) -> int:
    templates = templates if templates is not None else {"ru": {"text": "Привет, {name}!"}, "en": {"text": "Hi, {name}!"}}
    row = await db.one(
        "insert into ext_kiro_push_events (name, kind, enabled, params, audience, send_time, feb29, gift, gift_fallback, templates) "
        "values ('ev', :k, :en, cast(:p as jsonb), cast(:a as jsonb), :st, :f, cast(:g as jsonb), :fb, cast(:t as jsonb)) returning id",
        k=kind, en=enabled, p=json.dumps(params or {}), a=json.dumps(audience or {"segments": ["none", "trial", "paid"], "language": "any", "tariff_keys": []}),
        st=send_time, f=feb29, g=json.dumps(gift or {"kind": "none"}), fb=fallback, t=json.dumps(templates, ensure_ascii=False),
    )
    return int(row["id"])


async def set_bd(db: h.Db, uid: int, day: int, month: int, year: int | None = None) -> None:
    await db.exec(
        "insert into ext_kiro_push_birthdates (user_id, day, month, year, source, locked, status) values (:u, :d, :m, :y, 'telegram', true, 'found')",
        u=uid, d=day, m=month, y=year,
    )


async def delivered(db: h.Db, event_id: int | None = None) -> dict[int, str]:
    where = f"where event_id = {event_id}" if event_id else ""
    return {int(r["user_id"]): r["status"] for r in await db.q(f"select user_id, status from ext_kiro_push_deliveries {where}")}


async def planning_tests(db: h.Db) -> None:
    print("planning")
    f = db.factory
    NOW = utc(2026, 11, 10, 7, 0)  # 10:00 Moscow
    await db.reset()
    for uid in (1, 2, 3, 4, 5, 6, 7, 8):
        await db.add_user(uid, banned=None if uid == 3 else False)
    await db.exec("update users set is_banned = true where user_id = 4")
    await db.exec("update users set telegram_id = null where user_id = 5")
    await db.exec("update users set language_code = 'en' where user_id = 7")
    await db.exec("insert into ext_kiro_push_blocked (user_id, reason) values (6, 'bot was blocked')")
    for uid in (1, 2, 3, 4, 5, 6, 7):
        await set_bd(db, uid, 10, 11, 1990)
    await set_bd(db, 8, 11, 11)
    eid = await add_event(db)
    res = await sch.plan_events(f, CFG, now=NOW)
    got = await delivered(db)
    h.ok(set(got) == {1, 2, 3, 7}, f"birthday today: banned, telegram-less, blocked and other-day users excluded, NULL is_banned kept ({sorted(got)})")
    row = await db.one("select * from ext_kiro_push_deliveries where user_id = 1")
    h.ok(row["period_key"] == "2026" and row["base_year"] == 2026 and row["status"] == "pending" and row["event_id"] == eid, "delivery row: period = year")
    h.ok(res["created"] == 4, "plan reports created rows")
    again = await sch.plan_events(f, CFG, now=NOW + timedelta(minutes=1))
    h.ok(again["created"] == 0 and await db.count("ext_kiro_push_deliveries") == 4, "planning twice creates nothing new")
    # re-plan later in the day picks up a date that appeared meanwhile
    await db.add_user(9)
    await set_bd(db, 9, 10, 11)
    again = await sch.plan_events(f, CFG, now=NOW + timedelta(minutes=30))
    h.ok(again["created"] == 1 and 9 in await delivered(db), "re-plan after 10 min adds a user whose date arrived later")
    # parallel planning of the same slot
    await db.exec("truncate table ext_kiro_push_deliveries, ext_kiro_push_plans")
    ev_row = dict(await db.one("select * from ext_kiro_push_events where id = :i", i=eid))
    slot = ev.due_slots(ev_row, NOW, MSK, 24)[0]

    async def run_one() -> int:
        async with f() as s:
            sql, params = sch._plan_statement(ev_row, slot, CFG)
            r = await s.execute(sch.text(sql), params)
            await s.commit()
            return int(r.rowcount or 0)

    counts = await asyncio.gather(*[run_one() for _ in range(6)])
    h.ok(sum(counts) == 5 and await db.count("ext_kiro_push_deliveries") == 5, f"6 parallel planners -> every person exactly once ({counts})")

    print("audience filters")
    for case, audience, expected in (
        ("segment paid", {"segments": ["paid"], "language": "any", "tariff_keys": []}, {2, 3}),
        ("segment trial", {"segments": ["trial"], "language": "any", "tariff_keys": []}, {1}),
        ("segment none", {"segments": ["none"], "language": "any", "tariff_keys": []}, {7}),
        ("paid + trial", {"segments": ["trial", "paid"], "language": "any", "tariff_keys": []}, {1, 2, 3}),
        ("tariff key", {"segments": ["paid"], "language": "any", "tariff_keys": ["pro"]}, {3}),
        ("tariff key keeps non-paid segments", {"segments": ["trial", "paid"], "language": "any", "tariff_keys": ["pro"]}, {1, 3}),
        ("language en", {"segments": ["none", "trial", "paid"], "language": "en", "tariff_keys": []}, {7}),
        ("language ru", {"segments": ["none", "trial", "paid"], "language": "ru", "tariff_keys": []}, {1, 2, 3}),
    ):
        await db.reset()
        for uid in (1, 2, 3, 7):
            await db.add_user(uid, banned=None if uid == 3 else False)
            await set_bd(db, uid, 10, 11)
        await db.exec("update users set language_code = 'en' where user_id = 7")
        await db.add_sub(1, provider="trial", tariff=None, status="TRIAL")
        await db.add_sub(2, tariff="start")
        await db.add_sub(3, tariff="pro")
        await add_event(db, audience=audience)
        await sch.plan_events(f, CFG, now=NOW)
        h.ok(set(await delivered(db)) == expected, f"audience: {case} -> {sorted(expected)} (got {sorted(await delivered(db))})")
    await db.exec("insert into subscriptions (user_id, panel_user_uuid, tariff_key, end_date, is_active) values (7, 'x', 'start', now() - interval '1 day', true)")
    await db.exec("truncate table ext_kiro_push_deliveries, ext_kiro_push_plans")
    await db.exec("truncate table ext_kiro_push_events restart identity")
    await add_event(db, audience={"segments": ["none"], "language": "any", "tariff_keys": []})
    await sch.plan_events(f, CFG, now=NOW)
    h.ok(7 in await delivered(db), "an expired subscription counts as 'none'")

    print("other kinds")
    await db.reset()
    for uid in range(1, 6):
        await db.add_user(uid)
    await db.exec("update users set registration_date = '2023-05-17 12:00+00' where user_id in (1, 2)")
    await db.exec("update users set registration_date = '2026-05-17 12:00+00' where user_id = 3")  # this year's registration
    await db.exec("update users set registration_date = '2020-02-29 12:00+00' where user_id = 4")
    await db.exec("update users set registration_date = '2022-05-18 12:00+00' where user_id = 5")
    await add_event(db, "registration_anniversary", params={"min_years": 2})
    await sch.plan_events(f, CFG, now=utc(2026, 5, 17, 7, 0))
    h.ok(set(await delivered(db)) == {1, 2}, "anniversary 17 May: only users registered >= 2 years ago on that day")
    await db.exec("truncate table ext_kiro_push_deliveries, ext_kiro_push_plans")
    await sch.plan_events(f, CFG, now=utc(2027, 2, 28, 7, 0))
    h.ok(set(await delivered(db)) == {4}, "anniversary of a 29 Feb registration falls on 28 Feb in a common year")
    await db.exec("truncate table ext_kiro_push_events restart identity")
    await db.exec("truncate table ext_kiro_push_deliveries, ext_kiro_push_plans")
    await add_event(db, "registration_anniversary", feb29="mar1", params={"min_years": 1})
    await sch.plan_events(f, CFG, now=utc(2027, 3, 1, 7, 0))
    h.ok(set(await delivered(db)) == {4}, "…and on 1 Mar with the mar1 rule")
    # registration date near midnight is judged in the configured timezone
    await db.exec("truncate table ext_kiro_push_events restart identity")
    await db.exec("truncate table ext_kiro_push_deliveries, ext_kiro_push_plans")
    await db.exec("update users set registration_date = '2023-05-16 22:00+00' where user_id = 1")  # 17 May 01:00 in Moscow
    await add_event(db, "registration_anniversary", params={"min_years": 1})
    await sch.plan_events(f, CFG, now=utc(2026, 5, 17, 7, 0))
    h.ok(1 in await delivered(db), "registration date is taken in the configured timezone")
    # fixed date + one time
    await db.exec("truncate table ext_kiro_push_events restart identity")
    await db.exec("truncate table ext_kiro_push_deliveries, ext_kiro_push_plans")
    await add_event(db, "fixed_date", params={"date": "12-31"}, send_time="12:00")
    await add_event(db, "one_time", params={"date": "2026-12-31"}, send_time="12:00")
    await add_event(db, "manual")
    await add_event(db, "fixed_date", params={"date": "12-31"}, enabled=False)
    res = await sch.plan_events(f, CFG, now=utc(2026, 12, 31, 8, 59))
    h.ok(res["created"] == 0, "fixed/one-time: not before 12:00 Moscow")
    res = await sch.plan_events(f, CFG, now=utc(2026, 12, 31, 9, 0))
    rows = await db.q("select event_id, period_key, count(*) n from ext_kiro_push_deliveries group by 1, 2 order by 1")
    h.ok([(r["event_id"], r["period_key"], r["n"]) for r in rows] == [(1, "2026", 5), (2, "2026-12-31", 5)], "fixed date (period = year) and one-time (period = date) planned; manual and disabled are not")
    # next year, same fixed event: a new period, same people again
    await sch.plan_events(f, CFG, now=utc(2027, 12, 31, 9, 0))
    h.ok(await db.count("ext_kiro_push_deliveries", "event_id = 1") == 10, "the next year is a new period (yearly event repeats)")
    # birthday offset -1 plans on the day before
    await db.reset()
    for uid in (1, 2):
        await db.add_user(uid)
    await set_bd(db, 1, 11, 11)
    await set_bd(db, 2, 10, 11)
    await add_event(db, params={"offset_days": -1})
    await sch.plan_events(f, CFG, now=utc(2026, 11, 10, 7, 0))
    h.ok(set(await delivered(db)) == {1}, "offset -1: the 11th's birthdays are planned on the 10th")
    await db.reset()
    for uid in (1, 2, 3):
        await db.add_user(uid)
    await set_bd(db, 1, 29, 2)
    await set_bd(db, 2, 28, 2)
    await set_bd(db, 3, 1, 3)
    await add_event(db, feb29="feb28")
    await sch.plan_events(f, CFG, now=utc(2027, 2, 28, 7, 0))
    h.ok(set(await delivered(db)) == {1, 2}, "feb28 rule on 28 Feb 2027: 28 Feb and 29 Feb people")
    await db.exec("truncate table ext_kiro_push_deliveries, ext_kiro_push_plans")
    await sch.plan_events(f, CFG, now=utc(2027, 3, 1, 7, 0))
    h.ok(set(await delivered(db)) == {3}, "feb28 rule on 1 Mar 2027: only 1 Mar people (29 Feb people were done on the 28th)")
    await db.exec("truncate table ext_kiro_push_events restart identity")
    await db.exec("truncate table ext_kiro_push_deliveries, ext_kiro_push_plans")
    await add_event(db, feb29="mar1")
    await sch.plan_events(f, CFG, now=utc(2027, 3, 1, 7, 0))
    h.ok(set(await delivered(db)) == {1, 3}, "mar1 rule on 1 Mar 2027: 1 Mar and 29 Feb people")
    await db.exec("truncate table ext_kiro_push_deliveries, ext_kiro_push_plans")
    await sch.plan_events(f, CFG, now=utc(2028, 2, 29, 7, 0))
    h.ok(set(await delivered(db)) == {1}, "leap year 2028: 29 Feb people on 29 Feb")
    # timezone boundary: 21:30Z on 9 Nov is the 10th in Moscow
    await db.reset()
    await db.add_user(1)
    await set_bd(db, 1, 10, 11)
    await add_event(db, send_time="00:10")
    await sch.plan_events(f, CFG, now=utc(2026, 11, 9, 21, 5))
    h.ok(await delivered(db) == {}, "00:10 Moscow has not come yet at 21:05Z")
    await sch.plan_events(f, CFG, now=utc(2026, 11, 9, 21, 30))
    h.ok(await delivered(db) == {1: "pending"}, "…and has at 21:30Z (10 Nov in Moscow)")
    # DST zone config
    await db.reset()
    await db.add_user(1)
    await set_bd(db, 1, 8, 3)
    await add_event(db)
    ny_cfg = {**CFG, "timezone": "America/New_York"}
    await sch.plan_events(f, ny_cfg, now=utc(2026, 3, 8, 13, 59))
    h.ok(await delivered(db) == {}, "New York, DST day: 09:59 local -> nothing")
    await sch.plan_events(f, ny_cfg, now=utc(2026, 3, 8, 14, 0))
    h.ok(await delivered(db) == {1: "pending"}, "New York, DST day: 10:00 EDT -> planned")


async def delivery_tests(db: h.Db) -> None:
    print("delivery")
    f = db.factory
    NOW = utc(2026, 11, 10, 7, 0)
    clock = h.Clock(NOW)

    async def setup(n: int = 3, **kw) -> int:
        await db.reset()
        for uid in range(1, n + 1):
            await db.add_user(uid, username=f"user{uid}", name=f"Имя{uid}")
            await set_bd(db, uid, 10, 11, 1990 + uid)
        eid = await add_event(db, **kw)
        await sch.plan_events(f, CFG, now=NOW)
        return eid

    async def run(gw, cfg=None, **kw):
        return await sch.process_batch(f, gw, cfg or CFG, clock=clock, sleep=h.no_sleep, **kw)

    await setup(3)
    gw = h.FakeGateway()
    res = await run(gw)
    h.ok(res["sent"] == 3 and len(gw.sent) == 3, "all pending deliveries sent")
    texts = sorted(m.text for _c, m in gw.sent)
    h.ok(texts == ["Привет, Имя1!", "Привет, Имя2!", "Привет, Имя3!"], f"text rendered per user ({texts})")
    row = await db.one("select * from ext_kiro_push_deliveries where user_id = 1")
    h.ok(row["status"] == "sent" and row["sent_at"] and row["message_id"] and row["attempts"] == 1, "delivery logged as sent with message id")
    res = await run(gw)
    h.ok(res["claimed"] == 0 and len(gw.sent) == 3, "second run sends nothing")
    await sch.plan_events(f, CFG, now=NOW + timedelta(hours=1))
    await run(gw)
    h.ok(len(gw.sent) == 3, "re-planning + running again never double-sends (unique period)")

    # parallel workers
    await setup(12)
    gw = h.FakeGateway()
    await asyncio.gather(run(gw), run(gw), run(gw))
    chats = [c for c, _m in gw.sent]
    h.ok(len(chats) == 12 and len(set(chats)) == 12, f"3 parallel workers: each of 12 people exactly once ({len(chats)} sends)")
    h.ok(await db.count("ext_kiro_push_deliveries", "status = 'sent'") == 12, "all rows sent")

    # language pick, default language, nothing to send
    await setup(3, templates={"ru": {"text": "ru {name}"}, "en": {"text": "en {name}"}})
    await db.exec("update users set language_code = 'en-US' where user_id = 1")
    await db.exec("update users set language_code = 'de' where user_id = 2")
    await db.exec("update users set language_code = null where user_id = 3")
    gw = h.FakeGateway()
    await run(gw)
    by = {c: m.text for c, m in gw.sent}
    h.ok(by[100001].startswith("en ") and by[100002].startswith("ru ") and by[100003].startswith("ru "), "user language -> template, unknown/missing -> default language")
    await setup(2, templates={"en": {"text": "only en"}})
    gw = h.FakeGateway()
    await run(gw)
    h.ok(len(gw.sent) == 2 and gw.sent[0][1].text == "only en", "only template available is used for any language")

    # skips
    await setup(5)
    await db.exec("update users set is_banned = true where user_id = 1")
    await db.exec("update users set telegram_id = null where user_id = 2")
    await db.exec("insert into ext_kiro_push_blocked (user_id) values (3)")
    await db.exec("delete from users where user_id = 4")
    gw = h.FakeGateway()
    res = await run(gw)
    reasons = {int(r["user_id"]): (r["status"], r["reason"]) for r in await db.q("select user_id, status, reason from ext_kiro_push_deliveries")}
    h.ok(reasons[1] == ("skipped", "user_banned") and reasons[2] == ("skipped", "no_telegram") and reasons[3] == ("skipped", "user_blocked") and reasons[4] == ("skipped", "user_missing"), f"skip reasons logged ({reasons})")
    h.ok(len(gw.sent) == 1 and gw.sent[0][0] == 100005, "only the eligible user was messaged")
    await setup(2)
    await db.exec("update ext_kiro_push_events set enabled = false")
    gw = h.FakeGateway()
    await run(gw)
    h.ok(not gw.sent and (await delivered(db)) == {1: "skipped", 2: "skipped"}, "event disabled after planning -> skipped(event_disabled)")
    await setup(2)
    await db.exec("update ext_kiro_push_events set deleted_at = now()")
    await run(h.FakeGateway())
    h.ok((await db.one("select reason from ext_kiro_push_deliveries where user_id = 1"))["reason"] == "event_deleted", "event deleted -> skipped(event_deleted)")
    await setup(2)
    await db.exec("update ext_kiro_push_events set templates = '{}'::jsonb")
    await run(h.FakeGateway())
    h.ok((await db.one("select reason from ext_kiro_push_deliveries where user_id = 1"))["reason"] == "no_template", "no template -> skipped(no_template)")

    # blocked
    await setup(2)
    gw = h.FakeGateway(sends={100001: [tg.SendResult("blocked", "Forbidden: bot was blocked by the user")]})
    res = await run(gw)
    row = await db.one("select * from ext_kiro_push_deliveries where user_id = 1")
    h.ok(row["status"] == "blocked" and "blocked" in row["reason"] and res["blocked"] == 1, "blocked user marked blocked, no retry")
    h.ok(await db.count("ext_kiro_push_blocked", "user_id = 1") == 1, "blocked user remembered")
    res = await run(gw, )
    h.ok(res["claimed"] == 0, "blocked delivery is not retried")
    await db.exec("truncate table ext_kiro_push_deliveries, ext_kiro_push_plans")
    await sch.plan_events(f, CFG, now=NOW)
    h.ok(set(await delivered(db)) == {2}, "blocked users are not planned again for the next event")

    # transient failures: retry with back-off, then give up
    await setup(1)
    gw = h.FakeGateway(sends={100001: [tg.SendResult("failed", "network down")]})
    res = await run(gw)
    row = await db.one("select * from ext_kiro_push_deliveries where user_id = 1")
    h.ok(row["status"] == "pending" and row["attempts"] == 1 and row["next_try_at"] > NOW + timedelta(minutes=9) and res["retry"] == 1, "failure -> pending with back-off")
    res = await run(gw)
    h.ok(res["claimed"] == 0, "not retried before its time")
    clock.now = NOW + timedelta(minutes=11)
    await run(gw)
    clock.now = NOW + timedelta(minutes=22)
    await run(gw)
    row = await db.one("select * from ext_kiro_push_deliveries where user_id = 1")
    h.ok(row["status"] == "failed" and row["attempts"] == 3 and "network down" in row["reason"], "failed after max attempts, error stored")
    clock.now = NOW + timedelta(hours=2)
    res = await run(gw)
    h.ok(res["claimed"] == 0, "failed deliveries are final")
    clock.now = NOW
    # success on the second attempt
    await setup(1)
    gw = h.FakeGateway(sends={100001: [tg.SendResult("failed", "oops"), tg.SendResult("sent", message_id=7)]})
    await run(gw)
    clock.now = NOW + timedelta(minutes=11)
    await run(gw)
    row = await db.one("select * from ext_kiro_push_deliveries where user_id = 1")
    h.ok(row["status"] == "sent" and row["attempts"] == 2 and len(gw.sent) == 1, "retry succeeds -> sent once")
    clock.now = NOW

    # 429
    await setup(6)
    waits: list[float] = []

    async def rec_sleep(s: float) -> None:
        waits.append(s)

    gw = h.FakeGateway(sends={100003: [tg.SendResult("retry_after", "retry_after:5", retry_after=5), tg.SendResult("sent", message_id=9)]})
    res = await sch.process_batch(f, gw, {**CFG, "batch_size": 100}, clock=clock, sleep=rec_sleep)
    st_ = await delivered(db)
    h.ok(res["rate_limited"] == 1 and any(w >= 5 for w in waits), f"Telegram retry_after honoured (slept {sorted(set(waits))[-1]:.1f}s)")
    row = await db.one("select * from ext_kiro_push_deliveries where user_id = 3")
    h.ok(row["status"] == "pending" and row["attempts"] == 0 and row["next_try_at"] > NOW, "rate-limited delivery returns to pending without spending an attempt")
    h.ok(sum(1 for v in st_.values() if v == "sent") == 5 and st_[3] == "pending", "after the pause the rest of the batch goes out; only the limited one waits")
    clock.now = NOW + timedelta(seconds=10)
    await sch.process_batch(f, gw, {**CFG, "batch_size": 100}, clock=clock, sleep=h.no_sleep)
    h.ok(set((await delivered(db)).values()) == {"sent"} and len(gw.sent) == 6, "everyone delivered after the pause, nobody twice")
    clock.now = NOW

    # throttle + budget
    await setup(8)
    waits.clear()
    gw = h.FakeGateway()
    await sch.process_batch(f, gw, {**CFG, "send_rate_per_second": 10}, clock=clock, sleep=rec_sleep)
    h.ok(waits.count(0.1) == 8, "sends throttled to 10 msg/s")
    await setup(8)
    ticks = iter(range(10_000))
    gw = h.FakeGateway()
    res = await sch.process_batch(f, gw, CFG, clock=clock, sleep=h.no_sleep, monotonic=lambda: next(ticks) * 1.0, budget_seconds=3)
    n_sent = len(gw.sent)
    h.ok(0 < n_sent < 8 and await db.count("ext_kiro_push_deliveries", "status = 'pending'") == 8 - n_sent, f"time budget respected, leftovers stay pending ({n_sent} sent)")
    await sch.process_batch(f, gw, CFG, clock=clock, sleep=h.no_sleep)
    h.ok(len(gw.sent) == 8, "next tick finishes the rest")
    await setup(5)
    gw = h.FakeGateway()
    await sch.process_batch(f, gw, {**CFG, "batch_size": 10}, clock=clock, sleep=h.no_sleep)
    h.ok(len(gw.sent) == 5, "batch_size bound honoured")
    await setup(25)
    gw = h.FakeGateway()
    res = await sch.process_batch(f, gw, {**CFG, "batch_size": 10}, clock=clock, sleep=h.no_sleep)
    h.ok(len(gw.sent) == 10 and res["claimed"] == 10, "bounded batch per tick (10 of 25)")

    # crash recovery
    await setup(3)
    old = NOW - timedelta(minutes=30)
    await db.exec("update ext_kiro_push_deliveries set status = 'sending', claimed_at = :t, attempts = 1 where user_id = 1", t=old)
    await db.exec("update ext_kiro_push_deliveries set status = 'sending', claimed_at = :t, attempts = 1, send_started_at = :t where user_id = 2", t=old)
    await db.exec("update ext_kiro_push_deliveries set status = 'sending', claimed_at = :t, attempts = 1 where user_id = 3", t=NOW - timedelta(minutes=1))
    gw = h.FakeGateway()
    await run(gw)
    got = await delivered(db)
    h.ok(got[1] == "sent", "worker died before sending: the stale claim is picked up and sent")
    r2 = await db.one("select * from ext_kiro_push_deliveries where user_id = 2")
    h.ok(r2["status"] == "failed" and r2["reason"] == "interrupted_during_send", "worker died mid-send: never re-sent (uncertain), marked failed")
    h.ok(got[3] == "sending" and [c for c, _ in gw.sent] == [100001], "a fresh claim by a live worker is left alone")

    # long text
    await setup(1, templates={"ru": {"text": "я" * 5000}})
    gw = h.FakeGateway()
    await run(gw)
    h.ok((await db.one("select reason from ext_kiro_push_deliveries"))["reason"] == "message_too_long" and not gw.sent, "over-long text fails without calling Telegram")

    # optional block / age rendering in a real delivery
    await setup(2, templates={"ru": {"text": "С днём рождения, {name}![[ Вам исполнилось {age}!]]"}})
    await db.exec("update ext_kiro_push_birthdates set year = null where user_id = 2")
    gw = h.FakeGateway()
    await run(gw)
    by = {c: m.text for c, m in gw.sent}
    h.ok(by[100001] == "С днём рождения, Имя1! Вам исполнилось 35!" and by[100002] == "С днём рождения, Имя2!", f"{{age}} from the birth year; block dropped without a year ({by})")


async def manual_tests(db: h.Db) -> None:
    print("manual runs")
    f = db.factory
    clock = h.Clock(utc(2026, 11, 10, 7, 0))
    await db.reset()
    for uid in range(1, 7):
        await db.add_user(uid, username=f"u{uid}")
    await db.exec("update users set is_banned = true where user_id = 6")
    await db.add_sub(1, tariff="start")
    await db.add_sub(2, tariff="pro")
    eid = await add_event(db, "manual", audience={"segments": ["paid"], "language": "any", "tariff_keys": []})
    event_row = dict(await db.one("select * from ext_kiro_push_events where id = :i", i=eid))
    dry = await sch.run_manual(f, event_row, CFG, {"dry_run": True})
    h.ok(dry["dry_run"] and dry["matched"] == 2 and dry["sample"] == ["@u1", "@u2"] and await db.count("ext_kiro_push_deliveries") == 0, "dry run: count + sample, nothing queued")
    dry_all = await sch.run_manual(f, event_row, CFG, {"dry_run": True, "audience": {"segments": ["none", "trial", "paid"]}})
    h.ok(dry_all["matched"] == 5, "audience override in the request (banned excluded)")
    real = await sch.run_manual(f, event_row, CFG, {"batch_id": "promo-1"}, now=clock.now)
    h.ok(real["queued"] == 2 and real["already"] == 0 and real["batch_id"] == "promo-1", "real run queues the audience")
    again = await sch.run_manual(f, event_row, CFG, {"batch_id": "promo-1"}, now=clock.now)
    h.ok(again["queued"] == 0 and again["already"] == 2 and await db.count("ext_kiro_push_deliveries") == 2, "same batch_id -> nothing queued twice")
    other = await sch.run_manual(f, event_row, CFG, {"batch_id": "promo-2"}, now=clock.now)
    h.ok(other["queued"] == 2, "a new batch_id is a new send")
    gw = h.FakeGateway()
    await sch.process_batch(f, gw, CFG, clock=clock, sleep=h.no_sleep)
    h.ok(len(gw.sent) == 4, "manual deliveries are sent (even though the event is 'manual')")
    row = await db.one("select period_key, batch_id from ext_kiro_push_deliveries where user_id = 1 order by id limit 1")
    h.ok(row["period_key"] == "m:promo-1" and row["batch_id"] == "promo-1", "period key of a manual run is the batch")
    await h.raises(sch.run_manual(f, event_row, CFG, {"batch_id": "bad id!"}), "invalid_batch_id", "batch id validated")
    await h.raises(sch.run_manual(f, {**event_row, "kind": "birthday"}, CFG, {"dry_run": True}), "run_not_supported", "birthday events cannot be run manually")
    empty = {**event_row, "audience": json.dumps({"segments": ["trial"], "language": "any", "tariff_keys": []})}
    await h.raises(sch.run_manual(f, empty, CFG, {}), "no_recipients", "empty audience")
    await db.exec("update ext_kiro_push_events set enabled = false where id = :i", i=eid)
    await sch.run_manual(f, event_row, CFG, {"batch_id": "disabled-ok"}, now=clock.now)
    await sch.process_batch(f, gw, CFG, clock=clock, sleep=h.no_sleep)
    h.ok(len(gw.sent) == 6, "a manual run works even for a disabled event")

    print("stats")
    async with f() as s:
        d = await st.delivery_stats(s)
        b = await st.birthdate_stats(s, date(2026, 11, 10))
        up = await st.upcoming_birthdays(s, date(2026, 11, 10))
    h.ok(d["total"]["sent"] == 6 and d["events"][str(eid)]["sent"] == 6, "stats: per-event sent")
    h.ok(b["eligible_users"] == 5 and b["collected"] == 0 and b["unknown"] == 5, "stats: eligible users, nothing collected yet")
    await set_bd(db, 1, 12, 11, 1990)
    await set_bd(db, 2, 12, 11)
    await set_bd(db, 3, 29, 2)
    await db.exec("insert into ext_kiro_push_birthdates (user_id, status) values (4, 'absent')")
    await db.exec("insert into ext_kiro_push_birthdates (user_id, status) values (5, 'blocked')")
    async with f() as s:
        b = await st.birthdate_stats(s, date(2026, 11, 10))
        up = await st.upcoming_birthdays(s, date(2026, 11, 10))
        up2 = await st.upcoming_birthdays(s, date(2027, 2, 20))
    h.ok((b["collected"], b["hidden"], b["blocked"], b["unknown"], b["with_year"]) == (3, 1, 1, 0, 1), f"stats: collected/hidden/blocked/unknown ({b})")
    days = {x["date"]: x["count"] for x in up["days"]}
    h.ok(len(up["days"]) == 30 and days["2026-11-12"] == 2 and up["total"] == 2, "upcoming 30 days: counts per day")
    days2 = {x["date"]: x["count"] for x in up2["days"]}
    h.ok(days2["2027-02-28"] == 1, "29 Feb people shown on 28 Feb in a common year")
    # promo attribution
    await db.exec("insert into promo_codes (code) values ('PUSHA'), ('PUSHB')")
    await db.exec("update ext_kiro_push_deliveries set promo_code = 'PUSHA', promo_code_id = 1 where id = 1")
    await db.exec("update ext_kiro_push_deliveries set promo_code = 'PUSHB', promo_code_id = 2 where id = 2")
    await db.exec("insert into promo_code_activations (promo_code_id, user_id) select promo_code_id, user_id from ext_kiro_push_deliveries where id = 1")
    async with f() as s:
        d = await st.delivery_stats(s)
    h.ok(d["promo"] == {"issued": 2, "activated": 1}, f"promo activations attributable to push codes ({d['promo']})")


async def blocked_tests(db: h.Db) -> None:
    print("skip_blocked (Core status + own blocked table)")
    f = db.factory
    NOW = utc(2026, 11, 10, 7, 0)
    clock = h.Clock(NOW)
    ALL = {"segments": ["none", "trial", "paid"], "language": "any", "tariff_keys": []}

    async def world(audience: dict | None, kind: str = "birthday") -> int:
        await db.reset()
        # 1 plain (NULL status), 2 enabled, 3 unknown, 4 Core blocked, 5 own-table blocked, 6 banned + Core blocked,
        # 7 own-table blocked long ago (outside blocked_retry_days), 8 Core blocked + own table
        for uid, notif in ((1, None), (2, "enabled"), (3, "unknown"), (4, "blocked"), (5, None), (6, "blocked"), (7, None), (8, "blocked")):
            await db.add_user(uid, username=f"u{uid}", notif=notif, banned=uid == 6)
            await set_bd(db, uid, 10, 11, 1990)
        await db.exec("insert into ext_kiro_push_blocked (user_id, reason) values (5, 'bot was blocked'), (8, 'bot was blocked')")
        await db.exec("insert into ext_kiro_push_blocked (user_id, reason, created_at) values (7, 'old', now() - interval '200 days')")
        return await add_event(db, kind, audience=audience)

    # planning
    for label, aud, expected in (
        ("skip_blocked true", {**ALL, "skip_blocked": True}, {1, 2, 3, 7}),
        ("key missing (old event) behaves as true", ALL, {1, 2, 3, 7}),
        ("skip_blocked false", {**ALL, "skip_blocked": False}, {1, 2, 3, 4, 5, 7, 8}),
    ):
        await world(aud)
        await sch.plan_events(f, CFG, now=NOW)
        got = set(await delivered(db))
        h.ok(got == expected, f"planning, {label}: {sorted(expected)} (got {sorted(got)})")
    created = (await sch.plan_events(f, CFG, now=NOW + timedelta(minutes=30)))["created"]
    h.ok(created == 0 and await db.count("ext_kiro_push_deliveries") == 7, "re-planning with skip_blocked off creates nothing new (idempotent)")

    # one_time / fixed_date go through the same statement
    for kind, params in (("one_time", {"date": "2026-11-10"}), ("fixed_date", {"date": "11-10"})):
        await world({**ALL, "skip_blocked": True}, kind)
        await db.exec("update ext_kiro_push_events set params = cast(:p as jsonb)", p=json.dumps(params))
        await sch.plan_events(f, CFG, now=NOW)
        h.ok(set(await delivered(db)) == {1, 2, 3, 7}, f"{kind}: Core-blocked and recently own-blocked users skipped")

    # registration anniversary
    await world({**ALL, "skip_blocked": True}, "registration_anniversary")
    await db.exec("update users set registration_date = '2023-11-10 08:00+00'")
    await db.exec("update ext_kiro_push_events set params = cast(:p as jsonb)", p=json.dumps({"offset_days": 0, "min_years": 1}))
    await sch.plan_events(f, CFG, now=NOW)
    h.ok(set(await delivered(db)) == {1, 2, 3, 7}, "anniversary: blocked users skipped when skip_blocked")
    await db.exec("truncate table ext_kiro_push_deliveries, ext_kiro_push_plans")
    await db.exec("update ext_kiro_push_events set audience = cast(:a as jsonb)", a=json.dumps({**ALL, "skip_blocked": False}))
    await sch.plan_events(f, CFG, now=NOW)
    h.ok(set(await delivered(db)) == {1, 2, 3, 4, 5, 7, 8}, "anniversary: nobody skipped when skip_blocked is off (banned still out)")

    # delivery re-check
    await world({**ALL, "skip_blocked": False})
    await sch.plan_events(f, CFG, now=NOW)
    await db.exec("update ext_kiro_push_events set audience = cast(:a as jsonb)", a=json.dumps({**ALL, "skip_blocked": True}))
    gw = h.FakeGateway()
    await sch.process_batch(f, gw, CFG, clock=clock, sleep=h.no_sleep)
    rows = {int(r["user_id"]): (r["status"], r["reason"]) for r in await db.q("select user_id, status, reason from ext_kiro_push_deliveries")}
    h.ok(all(rows[u] == ("skipped", "user_blocked") for u in (4, 5, 8)), f"delivery re-check: Core/own blocked finished as skipped/user_blocked ({rows})")
    h.ok(all(rows[u][0] == "sent" for u in (1, 2, 3, 7)) and sorted(c for c, _ in gw.sent) == [100001, 100002, 100003, 100007], "others (NULL/enabled/unknown/old own row) are sent")

    eid = await world({**ALL, "skip_blocked": False})
    await sch.plan_events(f, CFG, now=NOW)
    gw = h.FakeGateway(sends={100004: [tg.SendResult("blocked", "Forbidden: bot was blocked by the user")]})
    await sch.process_batch(f, gw, CFG, clock=clock, sleep=h.no_sleep)
    rows = {int(r["user_id"]): r["status"] for r in await db.q("select user_id, status from ext_kiro_push_deliveries")}
    h.ok(rows[4] == "blocked" and rows[5] == "sent" and rows[8] == "sent" and rows[1] == "sent", f"skip_blocked off: blocked users are tried anyway ({rows})")
    h.ok(await db.count("ext_kiro_push_blocked", "user_id = 4") == 1, "a failed send still records the user as blocked")

    # a banned user is never contacted, whatever the option says
    h.ok(6 not in rows, "banned user is excluded from planning even with skip_blocked off")
    await db.exec("insert into ext_kiro_push_deliveries (event_id, user_id, period_key, base_year, status, next_try_at) values (:e, 6, 'x', 2026, 'pending', :t)", e=eid, t=NOW)
    gw = h.FakeGateway()
    await sch.process_batch(f, gw, CFG, clock=clock, sleep=h.no_sleep)
    h.ok((await db.one("select reason from ext_kiro_push_deliveries where user_id = 6"))["reason"] == "user_banned" and not gw.sent, "delivery step also refuses banned users")

    # manual run: dry run + queue
    await db.reset()
    for uid, notif in ((1, None), (2, "enabled"), (3, "blocked"), (4, "unknown"), (5, None)):
        await db.add_user(uid, username=f"u{uid}", notif=notif)
    await db.exec("insert into ext_kiro_push_blocked (user_id) values (5)")
    mid = await add_event(db, "manual", audience={**ALL, "skip_blocked": True})
    row = dict(await db.one("select * from ext_kiro_push_events where id = :i", i=mid))
    dry = await sch.run_manual(f, row, CFG, {"dry_run": True})
    h.ok(dry["matched"] == 3 and dry["sample"] == ["@u1", "@u2", "@u4"], f"manual dry run excludes Core and own blocked ({dry})")
    dry = await sch.run_manual(f, row, CFG, {"dry_run": True, "audience": {**ALL, "skip_blocked": False}})
    h.ok(dry["matched"] == 5, "manual dry run with override skip_blocked=false counts everybody")
    dry = await sch.run_manual(f, row, CFG, {"dry_run": True, "audience": ALL})
    h.ok(dry["matched"] == 3, "override without the key behaves as true")
    real = await sch.run_manual(f, row, CFG, {"batch_id": "b1"}, now=NOW)
    h.ok(real["queued"] == 3 and real["matched"] == 3, "manual run queues only the non-blocked users")
    real = await sch.run_manual(f, row, CFG, {"batch_id": "b1"}, now=NOW)
    h.ok(real["queued"] == 0 and await db.count("ext_kiro_push_deliveries") == 3, "same batch is idempotent")

    # Core without the status column: behaves as before (own table only)
    await db.reset()
    await db.set_core_status(False)
    for uid in (1, 2, 3):
        await db.add_user(uid, username=f"u{uid}")
        await set_bd(db, uid, 10, 11, 1990)
    await db.exec("insert into ext_kiro_push_blocked (user_id) values (3)")
    await add_event(db, audience={**ALL, "skip_blocked": True})
    await sch.plan_events(f, CFG, now=NOW)
    h.ok(set(await delivered(db)) == {1, 2}, "missing Core column: planning works, own blocked table still honoured")
    gw = h.FakeGateway()
    await sch.process_batch(f, gw, CFG, clock=clock, sleep=h.no_sleep)
    h.ok(len(gw.sent) == 2, "missing Core column: delivery works")
    dry = await sch.run_manual(f, {**row, "audience": json.dumps({**ALL, "skip_blocked": True})}, CFG, {"dry_run": True})
    h.ok(dry["matched"] == 2, "missing Core column: manual dry run works")
    await db.set_core_status(True)

    print("skip_blocked validation")
    base = {"name": "Д", "kind": "birthday", "send_time": "10:00", "templates": {"ru": {"text": "x"}}, "enabled": True}
    h.ok(ev.clean_event(base, wheel_installed=False)["audience"]["skip_blocked"] is True, "default audience skip_blocked = true")
    h.ok(ev.clean_event({**base, "audience": {"skip_blocked": False}}, wheel_installed=False)["audience"]["skip_blocked"] is False, "false is kept")
    h.ok(ev.clean_audience({"segments": ["paid"]})["skip_blocked"] is True, "old stored audience without the key -> true")
    for bad in ("false", 0, 1, "yes", [], {}):
        try:
            ev.clean_event({**base, "audience": {"skip_blocked": bad}}, wheel_installed=False)
        except PushError as exc:
            h.ok(exc.code == "invalid_audience", f"skip_blocked={bad!r} rejected as invalid_audience")
        else:
            raise AssertionError(f"accepted skip_blocked={bad!r}")


async def main() -> None:
    pure_tests()
    db = await h.make_db()
    await planning_tests(db)
    await delivery_tests(db)
    await manual_tests(db)
    await blocked_tests(db)
    h.finish()


if __name__ == "__main__":
    asyncio.run(main())
