"""HTTP API (customer + admin) through aiohttp against the real package __init__, the periodic job, event hooks.

Core's session/admin middleware is replaced by headers: X-Uid (customer session), X-Admin (+ X-Admin-Tg).
CSRF and the real admin middleware belong to Core and are not simulated here.

Run:  python tests/run_api_tests.py     (needs: pgserver sqlalchemy[asyncio] asyncpg aiohttp)
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness as h  # noqa: E402
import run_scheduler_tests as rs  # noqa: E402

pkg = h.load_package()
tg = sys.modules["kiro_push.telegram"]
Lookup = tg.BirthdateLookup
PREFIX_U = "/api/plugins/kiro-push"
PREFIX_A = "/api/admin/kiro-push"
NOW = datetime(2026, 11, 10, 7, 0, tzinfo=UTC)


@web.middleware
async def fake_core(request: web.Request, handler):
    if request.headers.get("X-Uid"):
        request["uid"] = int(request.headers["X-Uid"])
    if request.headers.get("X-Admin"):
        request["admin_authorized"] = True
        if request.headers.get("X-Admin-Tg"):
            request["admin_telegram_id"] = int(request.headers["X-Admin-Tg"])
    return await handler(request)


ADMIN = {"X-Admin": "1", "X-Admin-Tg": "999001"}


def plugin_wiring_tests() -> None:
    print("plugin wiring")
    p = pkg.plugin
    h.ok(p.name == "kiro-push" and p.version == "0.1.0", "name / version")
    h.ok([m.id for m in p.migrations()] == ["kiro-push.0001_initial"], "migration chain kiro-push.0001_*")
    ext = p.extensions(SimpleNamespace())
    job = ext.jobs[0]
    h.ok(job.id == "tick" and job.interval_seconds == 60 and job.max_attempts == 1, "periodic durable job every 60 s")
    h.ok(ext.permissions == frozenset({"rewards.days", "rewards.traffic", "rewards.balance"}), "reward permissions declared (promo codes use PromoCodeService, wheel tickets the wheel tables)")
    h.ok(p.locales_dir() and (p.locales_dir() / "ru.json").exists() and (p.locales_dir() / "en.json").exists(), "locales ru / en")
    locale = json.loads((p.locales_dir() / "ru.json").read_text(encoding="utf-8"))
    h.ok("admin_kiro_push_section" in locale and "wa_kiro_push_card" in locale, "nav keys present")
    subscribed = {name for name, _handler in h.SUBSCRIBERS}
    p.setup(SimpleNamespace(require_session_factory=lambda: None))
    subscribed = {name for name, _handler in h.SUBSCRIBERS}
    h.ok({"user.registered", "account.telegram_linked"} <= subscribed, "subscribed to user.registered and account.telegram_linked")


async def main_async() -> None:
    plugin_wiring_tests()
    db = await h.make_db()
    f = db.factory
    gw = h.FakeGateway()
    ctx = SimpleNamespace(
        settings=SimpleNamespace(USER_BALANCE_CURRENCY="", DEFAULT_CURRENCY_SYMBOL="RUB"), bot=object(), i18n=None,
        require_session_factory=lambda: f,
    )
    pkg.RUNTIME["ctx"] = ctx
    pkg.RUNTIME["gateway"] = gw
    app = web.Application(middlewares=[fake_core])
    app["factory"] = f
    pkg.plugin.setup_web(ctx, app, scope="webapp")
    pkg.plugin.setup_web(ctx, web.Application(), scope="webhooks")
    client = TestClient(TestServer(app))
    await client.start_server()

    async def call(method: str, path: str, *, headers=None, body=None, raw=None, expect=200):
        kwargs = {"headers": headers or {}}
        if body is not None:
            kwargs["json"] = body
        if raw is not None:
            kwargs["data"] = raw
        resp = await client.request(method, path, **kwargs)
        ctype = resp.headers.get("Content-Type", "")
        data = await resp.json() if "json" in ctype else await resp.text()
        h.ok(resp.status == expect, f"{method} {path.split('?')[0]} -> {resp.status} (expected {expect})" + ("" if resp.status == expect else f" {data}"))
        return data

    await db.reset()
    for uid in (1, 2, 3, 4):
        await db.add_user(uid, username=f"user{uid}", name=f"Имя{uid}")
    me = {"X-Uid": "1"}

    print("customer API")
    await call("GET", f"{PREFIX_U}/birthdate", expect=401)
    d = await call("GET", f"{PREFIX_U}/birthdate", headers=me)
    h.ok(d["state"] == "none" and d["enabled"] is False and d["can_set"] is False, "plugin disabled in config: card hidden (enabled=false), cannot set")
    d = await call("POST", f"{PREFIX_U}/birthdate", headers=me, body={"day": 1, "month": 1, "consent": True}, expect=403)
    h.ok(d["error"] == "disabled", "set refused while the plugin is disabled")
    d = await call("PUT", f"{PREFIX_A}/config", headers=ADMIN, body={"enabled": True, "user_attempts_per_hour": 30})
    h.ok(d["config"]["enabled"] is True, "plugin enabled")
    d = await call("GET", f"{PREFIX_U}/birthdate", headers=me)
    h.ok(d["state"] == "none" and d["can_set"] and d["enabled"] and "обработк" in d["consent_text"], "state none with the default consent text")
    d = await call("POST", f"{PREFIX_U}/birthdate", headers=me, body={"day": 1, "month": 1}, expect=400)
    h.ok(d["error"] == "consent_required", "consent required")
    d = await call("POST", f"{PREFIX_U}/birthdate", headers=me, body={"day": 31, "month": 4, "consent": True}, expect=400)
    h.ok(d["error"] == "invalid_date", "invalid date")
    d = await call("POST", f"{PREFIX_U}/birthdate", headers=me, body={"day": 1, "month": 1, "year": 2030, "consent": True}, expect=400)
    h.ok(d["error"] == "invalid_year", "future year")
    await call("POST", f"{PREFIX_U}/birthdate", headers=me, raw="not json", expect=400)
    d = await call("POST", f"{PREFIX_U}/birthdate", headers=me, body={"day": 29, "month": 2, "consent": True, "user_id": 2})
    h.ok(d["state"] == "set" and d["locked"] and (d["day"], d["month"]) == (29, 2) and d["source"] == "user", "date saved, locked")
    h.ok(await db.count("ext_kiro_push_birthdates", "user_id = 1") == 1 and await db.count("ext_kiro_push_birthdates", "user_id = 2") == 0, "a user_id in the body is ignored: only the session user's row is written")
    d = await call("POST", f"{PREFIX_U}/birthdate", headers=me, body={"day": 3, "month": 3, "consent": True}, expect=409)
    h.ok(d["error"] == "already_set", "second set -> 409 already_set")
    d = await call("GET", f"{PREFIX_U}/birthdate", headers=me)
    h.ok(d["state"] == "set" and d["day"] == 29 and "year" not in d and d["can_set"] is False, "GET shows the stored date, no year")
    d = await call("GET", f"{PREFIX_U}/birthdate", headers={"X-Uid": "2"})
    h.ok(d["state"] == "none", "other users are unaffected")
    await call("PUT", f"{PREFIX_A}/birthdates/user1", headers=me, body={"day": 1, "month": 1}, expect=403)
    await call("GET", f"{PREFIX_A}/overview", headers=me, expect=403)
    await call("GET", f"{PREFIX_A}/overview", expect=403)
    # admin edit / delete, then the user may set again
    d = await call("PUT", f"{PREFIX_A}/birthdates/user1", headers=ADMIN, body={"day": 5, "month": 6, "year": 1991})
    h.ok(d["source"] == "admin" and d["year"] == 1991, "admin edits by @username")
    d = await call("POST", f"{PREFIX_U}/birthdate", headers=me, body={"day": 3, "month": 3, "consent": True}, expect=409)
    await call("DELETE", f"{PREFIX_A}/birthdates/100001", headers=ADMIN)
    d = await call("GET", f"{PREFIX_U}/birthdate", headers=me)
    h.ok(d["state"] == "none" and d["can_set"], "after an admin delete the user can set it again")
    d = await call("POST", f"{PREFIX_U}/birthdate", headers=me, body={"day": 7, "month": 7, "year": 1992, "consent": True})
    h.ok(d["year"] == 1992, "set again after delete")
    await call("DELETE", f"{PREFIX_A}/birthdates/nobody", headers=ADMIN, expect=404)
    await call("DELETE", f"{PREFIX_A}/birthdates/3", headers=ADMIN, expect=404)
    # switch the entry off
    await call("PUT", f"{PREFIX_A}/config", headers=ADMIN, body={"user_entry_enabled": False})
    d = await call("GET", f"{PREFIX_U}/birthdate", headers=me)
    h.ok(d["enabled"] is False, "user_entry_enabled=false hides the card (enabled=false)")
    await call("POST", f"{PREFIX_U}/birthdate", headers={"X-Uid": "2"}, body={"day": 1, "month": 1, "consent": True}, expect=403)
    d = await call("PUT", f"{PREFIX_A}/config", headers=ADMIN, body={"user_entry_enabled": True, "consent_text": "Согласен на обработку даты рождения"})
    d = await call("GET", f"{PREFIX_U}/birthdate", headers={"X-Uid": "2"})
    h.ok(d["consent_text"] == "Согласен на обработку даты рождения", "admin-editable consent text")
    # rate limit
    await call("PUT", f"{PREFIX_A}/config", headers=ADMIN, body={"user_attempts_per_hour": 5})
    for _ in range(5):
        await call("POST", f"{PREFIX_U}/birthdate", headers={"X-Uid": "3"}, body={"day": 40, "month": 1, "consent": True}, expect=400)
    d = await call("POST", f"{PREFIX_U}/birthdate", headers={"X-Uid": "3"}, body={"day": 1, "month": 1, "consent": True}, expect=429)
    h.ok(d["error"] == "rate_limited", "rate limit per user")
    d = await call("POST", f"{PREFIX_U}/birthdate", headers={"X-Uid": "4"}, body={"day": 1, "month": 1, "consent": True})
    h.ok(d["state"] == "set", "…other users unaffected")
    j = await db.one("select count(*) n from ext_kiro_push_log where event = 'birthdate_set_by_user'")
    h.ok(j["n"] == 3, "journal: birthdate_set_by_user entries")
    h.ok(await db.count("ext_kiro_push_birthdate_audit", "actor like 'user:%'") == 3 and await db.count("ext_kiro_push_birthdate_audit", "actor like 'admin:%'") >= 2, "audit records for user and admin actions")

    print("admin API: config / overview")
    d = await call("PUT", f"{PREFIX_A}/config", headers=ADMIN, body={"timezone": "Mars/Base"}, expect=400)
    h.ok(d["error"] == "invalid_timezone", "invalid timezone")
    d = await call("PUT", f"{PREFIX_A}/config", headers=ADMIN, body={"send_rate_per_second": 99}, expect=400)
    d = await call("PUT", f"{PREFIX_A}/config", headers=ADMIN, body={"timezone": "America/New_York", "rescan_missing_days": 10})
    h.ok(d["config"]["timezone"] == "America/New_York" and d["config"]["rescan_missing_days"] == 10, "config saved")
    await call("PUT", f"{PREFIX_A}/config", headers=ADMIN, body={"timezone": "Europe/Moscow"})
    d = await call("GET", f"{PREFIX_A}/overview", headers=ADMIN)
    caps = d["capabilities"]
    h.ok(d["wheel_installed"] is False and d["bot_available"] and d["version"] == "0.1.0", "overview: flags")
    h.ok({k["id"] for k in caps["event_kinds"]} == {"birthday", "registration_anniversary", "fixed_date", "one_time", "manual"}, "capabilities: event kinds")
    h.ok([g["available"] for g in caps["gift_kinds"] if g["id"] == "wheel_spins"] == [False], "capabilities: wheel_spins unavailable without the wheel")
    h.ok(len(caps["placeholders"]) == 11 and caps["limits"]["text"] == 4096 and caps["limits"]["caption"] == 1024 and caps["emoji_library"]["library"].endswith("/library"), "capabilities: placeholders, limits, emoji endpoints")
    h.ok({"birthdates", "upcoming", "deliveries", "backfill", "config"} <= set(d), "overview: stats blocks")
    h.ok(isinstance(d.get("currency"), str) and len(d["currency"]) >= 1, f"overview: currency for the gift editor ({d.get('currency')})")

    print("admin API: events")
    tpl_ru = {"text": "С днём рождения, {name}![[ Вам исполнилось {age}.]][[ Подарок: {gift}.]]", "text_no_gift": "С днём рождения, {name}!",
              "buttons": [[{"kind": "url", "label": "Открыть", "url": "https://x.io/?c={promo_code}", "style": "primary"}]]}
    event_body = {"name": "Birthday", "kind": "birthday", "enabled": True, "send_time": "10:00", "params": {"offset_days": 0},
                  "gift": {"kind": "days", "days": 7}, "templates": {"ru": tpl_ru}}
    d = await call("POST", f"{PREFIX_A}/events", headers=ADMIN, body={**event_body, "templates": {"ru": {"text": "<b>x {oops}"}}}, expect=400)
    h.ok(d["error"] == "invalid_event" and len(d["issues"]) >= 2, f"invalid event: issues listed ({len(d['issues'])})")
    d = await call("POST", f"{PREFIX_A}/events", headers=ADMIN, body={**event_body, "gift": {"kind": "wheel_spins", "spins": 1}}, expect=409)
    h.ok(d["error"] == "wheel_not_installed", "wheel gift rejected on save")
    d = await call("POST", f"{PREFIX_A}/events", headers=ADMIN, body=event_body, expect=201)
    eid = d["id"]
    d = await call("GET", f"{PREFIX_A}/events", headers=ADMIN)
    h.ok(len(d["events"]) == 1 and d["events"][0]["id"] == eid and d["events"][0]["gift"] == {"kind": "days", "days": 7}, "list events")
    d = await call("GET", f"{PREFIX_A}/events/{eid}", headers=ADMIN)
    h.ok(d["event"]["templates"]["ru"]["text_no_gift"] == "С днём рождения, {name}!" and d["event"]["audience"]["segments"] == ["none", "trial", "paid"], "get event (templates round-trip, default audience)")
    await call("GET", f"{PREFIX_A}/events/999", headers=ADMIN, expect=404)
    d = await call("PUT", f"{PREFIX_A}/events/{eid}", headers=ADMIN, body={**event_body, "name": "Birthday 2", "send_time": "11:30", "feb29": "mar1"})
    d = await call("GET", f"{PREFIX_A}/events/{eid}", headers=ADMIN)
    h.ok(d["event"]["name"] == "Birthday 2" and d["event"]["send_time"] == "11:30" and d["event"]["feb29"] == "mar1", "update event")
    # preview
    d = await call("POST", f"{PREFIX_A}/events/{eid}/preview", headers=ADMIN, body={"language": "ru"})
    r = d["rendered"]
    h.ok(r["text"] == "С днём рождения, Алексей! Вам исполнилось 30. Подарок: +7 дней." and r["used_no_gift"] is False and r["buttons"][0][0]["url"] == "https://x.io/?c=PUSH-AB12CD", f"preview with sample values ({r['text']!r})")
    d = await call("POST", f"{PREFIX_A}/events/{eid}/preview", headers=ADMIN, body={"language": "ru", "with_gift": False, "sample_user": {"age": "", "name": "<i>X</i>"}})
    h.ok(d["rendered"]["text"] == "С днём рождения, &lt;i&gt;X&lt;/i&gt;!" and d["rendered"]["used_no_gift"], "preview without gift and without age; sample values escaped")
    await call("POST", f"{PREFIX_A}/events/{eid}/preview", headers=ADMIN, body={"language": "en"}, expect=404)
    await db.exec("update users set first_name = 'Анна' where user_id = 4")
    await db.exec("insert into ext_kiro_push_birthdates (user_id, day, month, year, source, locked, status) values (4, 1, 1, 1980, 'admin', true, 'found') on conflict (user_id) do update set year = 1980")
    d = await call("POST", f"{PREFIX_A}/events/{eid}/preview", headers=ADMIN, body={"language": "ru", "sample_user": "@user4"})
    h.ok(d["rendered"]["text"].startswith("С днём рождения, Анна! Вам исполнилось ") and "+7 дней" in d["rendered"]["text"], f"preview for a real user ({d['rendered']['text']!r})")
    await call("POST", f"{PREFIX_A}/events/{eid}/preview", headers=ADMIN, body={"language": "ru", "sample_user": "ghost"}, expect=404)
    d = await call("POST", f"{PREFIX_A}/preview", headers=ADMIN, body={"event": {**event_body, "templates": {"ru": {"text": "Черновик {name}[[ {age}]]"}}}, "language": "ru"})
    h.ok(d["rendered"]["text"] == "Черновик Алексей 30", "draft preview (unsaved event)")
    d = await call("POST", f"{PREFIX_A}/preview", headers=ADMIN, body={"event": {**event_body, "templates": {"ru": {"text": "<b>открыто"}}}, "language": "ru"})
    h.ok(d["rendered"] is None and d["errors"], "draft preview returns validation errors instead of failing")
    d = await call("POST", f"{PREFIX_A}/preview", headers=ADMIN, body={"event": {**event_body, "templates": {"ru": {"text": "Привет {name}{age}"}}}, "language": "ru"})
    h.ok(any("{age}" in w for w in d["warnings"]), "preview warns about {age} outside an optional block")
    # test send
    await db.add_user(100, tg=999001, username="boss", name="Босс")
    d = await call("POST", f"{PREFIX_A}/events/{eid}/test", headers=ADMIN, body={"language": "ru"})
    h.ok(len(gw.sent) == 1 and gw.sent[0][0] == 999001 and "Босс" in gw.sent[0][1].text and not h.GRANTS, "test send goes to the requesting admin's own telegram id, grants nothing")
    await call("POST", f"{PREFIX_A}/events/{eid}/test", headers={"X-Admin": "1"}, body={}, expect=403)
    gw.sends[999001] = [tg.SendResult("failed", "boom")]
    d = await call("POST", f"{PREFIX_A}/events/{eid}/test", headers=ADMIN, body={"language": "ru"}, expect=502)
    h.ok(d["error"] == "test_failed" and d["detail"] == "boom", "test send failure reported")
    gw.sends.clear()
    # manual run
    d = await call("POST", f"{PREFIX_A}/events", headers=ADMIN, body={"name": "Promo", "kind": "manual", "enabled": True,
                                                                     "templates": {"ru": {"text": "Акция, {name}!"}}}, expect=201)
    mid = d["id"]
    d = await call("POST", f"{PREFIX_A}/events/{mid}/run", headers=ADMIN, body={"dry_run": True})
    h.ok(d["dry_run"] and d["matched"] == 5 and len(d["sample"]) == 5, "manual run dry-run: matched + sample")
    d = await call("POST", f"{PREFIX_A}/events/{mid}/run", headers=ADMIN, body={"batch_id": "b1"})
    h.ok(d["queued"] == 5, "manual run queued")
    d = await call("POST", f"{PREFIX_A}/events/{mid}/run", headers=ADMIN, body={"batch_id": "b1"})
    h.ok(d["queued"] == 0 and d["already"] == 5, "manual run is idempotent by batch_id")
    await call("POST", f"{PREFIX_A}/events/{eid}/run", headers=ADMIN, body={"dry_run": True}, expect=400)
    d = await call("DELETE", f"{PREFIX_A}/events/{mid}", headers=ADMIN)
    d = await call("GET", f"{PREFIX_A}/events", headers=ADMIN)
    h.ok([e["id"] for e in d["events"]] == [eid], "deleted event disappears from the list")

    print("admin API: birthdates")
    d = await call("GET", f"{PREFIX_A}/birthdates", headers=ADMIN)
    h.ok(d["total"] >= 2 and all(i["date"] for i in d["items"]), "default list: users with a date")
    d = await call("GET", f"{PREFIX_A}/birthdates?q=@user4&status=all", headers=ADMIN)
    h.ok(d["total"] == 1 and d["items"][0]["username"] == "user4" and d["items"][0]["date"] == "01.01.1980", "search by @username")
    d = await call("GET", f"{PREFIX_A}/birthdates?q=100004", headers=ADMIN)
    h.ok(d["total"] == 1, "search by telegram id")
    d = await call("GET", f"{PREFIX_A}/birthdates?q=3", headers=ADMIN)
    h.ok(d["total"] == 1 and d["items"][0]["status"] == "unknown", "searching a user without a row finds him (status unknown)")
    d = await call("GET", f"{PREFIX_A}/birthdates?month=1&status=set", headers=ADMIN)
    h.ok(d["total"] == 1, "filter by month")
    d = await call("GET", f"{PREFIX_A}/birthdates?status=unknown&per_page=2&page=1", headers=ADMIN)
    h.ok(len(d["items"]) <= 2 and d["per_page"] == 2, "pagination")
    await call("GET", f"{PREFIX_A}/birthdates?status=weird", headers=ADMIN, expect=400)
    d = await call("POST", f"{PREFIX_A}/birthdates/import", headers=ADMIN, body={"items": [{"user": "user2", "day": 2, "month": 2}, {"user": "zzz", "day": 1, "month": 1}, {"user": "user4", "day": 9, "month": 9}]})
    h.ok((d["saved"], d["skipped"], d["errors"]) == (1, 1, 1), "import: saved / skipped / error")
    await call("POST", f"{PREFIX_A}/birthdates/import", headers=ADMIN, body={"items": []}, expect=400)
    await call("PUT", f"{PREFIX_A}/birthdates/user3", headers=ADMIN, body={"day": 30, "month": 2}, expect=400)

    print("admin API: backfill")
    d = await call("POST", f"{PREFIX_A}/backfill/start", headers=ADMIN, body={"dry_run": True, "rate": 12})
    b = d["backfill"]
    h.ok(b["status"] == "running" and b["dry_run"] and b["rate"] == 12 and b["total"] >= 1, "backfill start (dry run)")
    await call("POST", f"{PREFIX_A}/backfill/start", headers=ADMIN, body={}, expect=409)
    d = await call("POST", f"{PREFIX_A}/backfill/stop", headers=ADMIN, body={})
    h.ok(d["backfill"]["status"] == "stopped" and d["backfill"]["can_resume"] is False, "backfill stop (nothing scanned: nothing to resume)")
    d = await call("GET", f"{PREFIX_A}/backfill/status", headers=ADMIN)
    h.ok(d["backfill"]["status"] == "stopped", "backfill status")
    await call("POST", f"{PREFIX_A}/backfill/start", headers=ADMIN, body={"rate": 500}, expect=400)

    print("periodic job + hooks")
    await db.reset()
    pkg.RUNTIME["gateway"] = gw = h.FakeGateway()
    for uid in (1, 2, 3):
        await db.add_user(uid, username=f"user{uid}", name=f"Имя{uid}")
        await db.exec("insert into ext_kiro_push_birthdates (user_id, day, month, source, locked, status) values (:u, 10, 11, 'telegram', true, 'found')", u=uid)
    await db.add_sub(1)
    eid = {"id": await rs.add_event(db, gift={"kind": "days", "days": 3},
                                    templates={"ru": {"text": "Привет {name}! {gift}", "text_no_gift": "Привет {name}!"}})}
    res = await pkg.run_tick(f, gw, currency="RUB", tariff_titles={}, clock=h.Clock(NOW), sleep=h.no_sleep)
    h.ok(res == {"skipped": "disabled"}, "tick: plugin disabled -> nothing happens")
    await db.exec("insert into ext_kiro_push_config (id, data) values (1, cast(:d as jsonb))", d=json.dumps({"enabled": True, "auto_backfill": True}))
    res = await pkg.run_tick(f, gw, currency="RUB", tariff_titles={}, clock=h.Clock(NOW), sleep=h.no_sleep)
    h.ok(res["plan"]["created"] == 3 and res["send"]["sent"] == 3, f"tick: planned and sent ({res['plan']}, {res['send']})")
    texts = sorted(m.text for _c, m in gw.sent)
    h.ok(texts == ["Привет Имя1! +3 дня", "Привет Имя2!", "Привет Имя3!"], f"tick: gift only for the subscriber ({texts})")
    h.ok(len(h.GRANTS) == 1, "tick: one grant")
    res2 = await pkg.run_tick(f, gw, currency="RUB", tariff_titles={}, clock=h.Clock(NOW), sleep=h.no_sleep)
    h.ok(res2["send"]["claimed"] == 0 and len(gw.sent) == 3, "tick again: nothing is sent twice")
    h.ok(res["backfill"]["ran"] if "backfill" in res else True, "tick: backfill chunk")
    bf = await db.one("select status, dry_run from ext_kiro_push_backfill")
    h.ok(bf is not None and not bf["dry_run"], "auto_backfill started once on first enable (real run)")
    await call("POST", f"{PREFIX_A}/backfill/stop", headers=ADMIN, body={})
    await call("POST", f"{PREFIX_A}/backfill/start", headers=ADMIN, body={})
    await pkg.run_tick(f, gw, currency="RUB", tariff_titles={}, clock=h.Clock(NOW), sleep=h.no_sleep)
    h.ok((await db.one("select count(*) n from ext_kiro_push_meta where key = 'auto_backfill_started'"))["n"] == 1, "auto-start flag set; not started again by ticks")
    # events
    pkg.EVENT_SCAN_DELAY = 0.05
    await db.add_user(50, tg=5050, username="newbie")
    gw.lookups[5050] = [Lookup("found", 8, 8, None)]
    pkg.plugin.setup(ctx)
    handler = [hd for name, hd in h.SUBSCRIBERS if name == "user.registered"][-1]
    await handler("user.registered", {"user_id": 50})
    await handler("user.registered", {"user_id": "x"})
    await handler("user.registered", {})
    await asyncio.sleep(0.5)
    row = await db.one("select * from ext_kiro_push_birthdates where user_id = 50")
    h.ok(row and row["day"] == 8 and row["source"] == "telegram", "user.registered: birthdate fetched from Telegram and stored")
    await db.add_user(51, tg=5151)
    await db.exec("update ext_kiro_push_config set data = jsonb_set(data, '{scan_new_users}', 'false')")
    await handler("user.registered", {"user_id": 51})
    await asyncio.sleep(0.4)
    h.ok(await db.count("ext_kiro_push_birthdates", "user_id = 51") == 0, "scan_new_users=false: event ignored")

    print("deliveries / logs / stats")
    d = await call("GET", f"{PREFIX_A}/deliveries?per_page=2", headers=ADMIN)
    h.ok(d["total"] == 3 and len(d["items"]) == 2 and d["items"][0]["event_name"] == "ev", "deliveries list with pagination")
    d = await call("GET", f"{PREFIX_A}/deliveries?status=sent&user=@user1", headers=ADMIN)
    h.ok(d["total"] == 1 and d["items"][0]["gift_status"] == "granted" and d["items"][0]["gift_kind"] == "days", "filter by status and user")
    d = await call("GET", f"{PREFIX_A}/deliveries?event_id=%d&period=2026" % eid["id"], headers=ADMIN)
    h.ok(d["total"] == 3, "filter by event and period")
    await call("GET", f"{PREFIX_A}/deliveries?event_id=abc", headers=ADMIN, expect=400)
    text_ = await call("GET", f"{PREFIX_A}/deliveries/download?limit=10", headers=ADMIN)
    dump = text_
    h.ok(dump["masked"] and len(dump["items"]) == 3 and all(str(i["user_id"]).startswith("u") for i in dump["items"]) and all(i["username"].startswith("u") for i in dump["items"]), "download: user ids / usernames masked")
    raw = await call("GET", f"{PREFIX_A}/deliveries/download?mask=0", headers=ADMIN)
    h.ok(str(raw["items"][0]["user_id"]) in ("1", "2", "3"), "download: mask=0 gives raw ids")
    d = await call("GET", f"{PREFIX_A}/stats", headers=ADMIN)
    h.ok(d["deliveries"]["total"]["sent"] == 3 and d["birthdates"]["collected"] >= 3 and len(d["upcoming"]["days"]) == 30, "stats endpoint")
    d = await call("GET", f"{PREFIX_A}/logs?limit=50", headers=ADMIN)
    h.ok(d["rows"] and d["max_rows"] == 5000 and "counts" in d, "journal list")
    d = await call("GET", f"{PREFIX_A}/logs?level=warn", headers=ADMIN)
    report = await call("GET", f"{PREFIX_A}/logs/download", headers=ADMIN)
    h.ok("KIRO Push journal" in report and "kiro-push 0.1.0" in report and "masked" in report, "journal download (header + masked)")
    d = await call("POST", f"{PREFIX_A}/logs/clear", headers=ADMIN, body={})
    h.ok(d["removed"] > 0, "journal cleared")

    print("media")
    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 40
    d = await call("POST", f"{PREFIX_A}/media", headers=ADMIN, body={"kind": "photo", "size": len(jpeg), "filename": "a.jpg", "content_type": "image/jpeg"})
    mid_ = d["id"]
    async def chunk(offset, data, expect=200):
        resp = await client.put(f"{PREFIX_A}/media/{mid_}/chunk", headers={**ADMIN, "X-Offset": str(offset)}, data=data)
        h.ok(resp.status == expect, f"chunk @{offset} -> {resp.status}")
        return await resp.json()
    await chunk(20, jpeg[20:], 409)
    await chunk(0, jpeg[:20])
    await chunk(20, jpeg[20:])
    await call("POST", f"{PREFIX_A}/media/{mid_}/finish", headers=ADMIN, body={})
    d = await call("GET", f"{PREFIX_A}/media", headers=ADMIN)
    h.ok([m["id"] for m in d["media"]] == [mid_], "media listing")
    resp = await client.get(f"{PREFIX_A}/media/{mid_}", headers=ADMIN)
    h.ok((await resp.read()) == jpeg and resp.status == 200, "media download")
    h.ok(resp.headers.get("Content-Type", "").startswith("image/jpeg"), "download type is derived from the file, not the client")
    h.ok(resp.headers.get("X-Content-Type-Options") == "nosniff" and "sandbox" in resp.headers.get("Content-Security-Policy", ""), "download has nosniff and CSP sandbox")
    # A document uploaded with a hostile client content type is served as an opaque attachment.
    html_doc = b"<html><script>alert(1)</script></html>"
    d = await call("POST", f"{PREFIX_A}/media", headers=ADMIN, body={"kind": "document", "size": len(html_doc), "filename": "../evil.html", "content_type": "text/html"})
    doc_id = d["id"]
    await client.put(f"{PREFIX_A}/media/{doc_id}/chunk", headers={**ADMIN, "X-Offset": "0"}, data=html_doc)
    await call("POST", f"{PREFIX_A}/media/{doc_id}/finish", headers=ADMIN, body={})
    resp = await client.get(f"{PREFIX_A}/media/{doc_id}", headers=ADMIN)
    h.ok(resp.headers.get("Content-Type", "").startswith("application/octet-stream"), "document is never served as html")
    cd = resp.headers.get("Content-Disposition", "")
    h.ok(cd.startswith("attachment") and "/" not in cd and ".." not in cd.split("filename=")[-1].strip('"').replace("evil.html", ""), "document is an attachment with a sanitised name")
    await call("DELETE", f"{PREFIX_A}/media/{doc_id}", headers=ADMIN)
    d = await call("POST", f"{PREFIX_A}/media", headers=ADMIN, body={"kind": "photo", "size": 10})
    await client.put(f"{PREFIX_A}/media/{d['id']}/chunk", headers={**ADMIN, "X-Offset": "0"}, data=b"notanimage")
    await call("POST", f"{PREFIX_A}/media/{d['id']}/finish", headers=ADMIN, body={}, expect=400)
    await call("POST", f"{PREFIX_A}/media", headers=ADMIN, body={"kind": "photo", "size": 99 * 1024 * 1024}, expect=400)
    await call("POST", f"{PREFIX_A}/media", headers=ADMIN, body={"kind": "audio", "size": 5}, expect=400)
    d = await call("POST", f"{PREFIX_A}/events", headers=ADMIN, body={"name": "M", "kind": "manual", "enabled": True,
                                                                     "templates": {"ru": {"text": "x", "media": [{"kind": "photo", "id": mid_}]}}}, expect=201)
    await call("DELETE", f"{PREFIX_A}/media/{mid_}", headers=ADMIN, expect=409)

    await client.close()
    h.finish()


if __name__ == "__main__":
    asyncio.run(main_async())
