"""Gifts: every kind (traffic, days, balance, promo_discount, wheel_spins present/absent/cap), the no-subscription
fallbacks, idempotency (no double grant when the message is retried), transient grant errors, blocked users.

Run:  python tests/run_gifts_tests.py     (needs: pgserver sqlalchemy[asyncio] asyncpg)
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness as h  # noqa: E402
import run_scheduler_tests as rs  # noqa: E402  (reuse add_event / set_bd helpers)

storage, errors, tg, gifts, sch, ev, st = h.load("storage", "errors", "telegram", "gifts", "scheduler", "eventdefs", "stats")
PushError = errors.PushError
NOW = datetime(2026, 11, 10, 7, 0, tzinfo=UTC)
CFG = {**storage.DEFAULT_CONFIG, "enabled": True, "send_rate_per_second": 28}
TEXT = {"ru": {"text": "Привет, {name}![[ Ваш подарок: {gift}.]][[ Промокод: {promo_code} ({promo_discount}).]]", "text_no_gift": "Привет, {name}! Без подарка."}}


def pure_tests() -> None:
    print("gift definitions")
    c = gifts.clean_gift
    h.ok(c(None, wheel_installed=False) == {"kind": "none"}, "no gift by default")
    h.ok(c({"kind": "days", "days": "7"}, wheel_installed=False) == {"kind": "days", "days": 7}, "days")
    h.ok(c({"kind": "traffic", "gb": 2.5}, wheel_installed=False) == {"kind": "traffic", "gb": 2.5}, "traffic")
    h.ok(c({"kind": "balance", "amount_minor": 10000}, wheel_installed=False)["amount_minor"] == 10000, "balance in minor units")
    p = c({"kind": "promo_discount", "percent": 20}, wheel_installed=False)
    h.ok(p == {"kind": "promo_discount", "percent": 20, "valid_days": 14, "applies_to": "subscription"}, "promo defaults")
    h.ok(c({"kind": "wheel_spins", "spins": 2, "ignore_cap": 1}, wheel_installed=True) == {"kind": "wheel_spins", "spins": 2, "ignore_cap": True}, "wheel spins when installed")
    for raw, code in (
        ({"kind": "days", "days": 1.5}, "invalid_gift_days"), ({"kind": "days", "days": 4000}, "invalid_gift_days"),
        ({"kind": "traffic", "gb": 0}, "invalid_gift_gb"), ({"kind": "balance", "amount_minor": -5}, "invalid_gift_amount"),
        ({"kind": "promo_discount", "percent": 0}, "invalid_gift_percent"),
        ({"kind": "promo_discount", "percent": 10, "valid_days": 999}, "invalid_gift_valid_days"),
        ({"kind": "promo_discount", "percent": 10, "applies_to": "hwid"}, "invalid_gift_applies_to"),
        ({"kind": "wheel_spins", "spins": 0}, "invalid_gift_spins"),
    ):
        try:
            c(raw, wheel_installed=True)
        except PushError as exc:
            h.ok(exc.code == code, f"{raw['kind']} {code}")
        else:
            raise AssertionError(f"accepted {raw}")
    try:
        c({"kind": "wheel_spins", "spins": 1}, wheel_installed=False)
    except PushError as exc:
        h.ok(exc.code == "wheel_not_installed" and exc.status == 409, "wheel gift rejected on save when the wheel is not installed")
    t = gifts.gift_text
    h.ok(t({"kind": "days", "days": 7}) == "+7 дней" and t({"kind": "days", "days": 1}) == "+1 день" and t({"kind": "days", "days": 22}) == "+22 дня", "days text, Russian plurals")
    h.ok(t({"kind": "traffic", "gb": 5}) == "+5 ГБ трафика" and t({"kind": "traffic", "gb": 0.5}) == "+0.5 ГБ трафика", "traffic text")
    h.ok(t({"kind": "balance", "amount_minor": 15050}, "RUB") == "+150,50 ₽ на баланс" and t({"kind": "balance", "amount_minor": 10000}, "RUB") == "+100 ₽ на баланс", "balance text")
    h.ok(t({"kind": "promo_discount", "percent": 20}) == "скидка 20% по промокоду" and gifts.discount_text({"kind": "promo_discount", "percent": 20}) == "20%", "promo text")
    h.ok(t({"kind": "wheel_spins", "spins": 3}) == "+3 билета колеса удачи", "wheel text")


async def main_async() -> None:
    db = await h.make_db()
    f = db.factory
    clock = h.Clock(NOW)

    async def setup(gift: dict, *, users: int = 1, subscribed: bool = True, fallback: str = "send_without_gift", templates=None) -> int:
        await db.reset()
        for uid in range(1, users + 1):
            await db.add_user(uid, username=f"user{uid}", name=f"Имя{uid}")
            await rs.set_bd(db, uid, 10, 11, 1990)
            if subscribed:
                await db.add_sub(uid, tariff="start")
        eid = await rs.add_event(db, gift=gifts.clean_gift(gift, wheel_installed=True), fallback=fallback, templates=templates or TEXT)
        await sch.plan_events(f, CFG, now=NOW)
        return eid

    async def run(gw, **kw):
        return await sch.process_batch(f, gw, CFG, clock=clock, sleep=h.no_sleep, currency=kw.pop("currency", "RUB"), **kw)

    def delivery(uid: int = 1):
        return db.one("select * from ext_kiro_push_deliveries where user_id = :u", u=uid)

    print("days / traffic / balance")
    await setup({"kind": "days", "days": 7})
    gw = h.FakeGateway()
    await run(gw)
    d = await delivery()
    h.ok(len(h.GRANTS) == 1 and h.GRANTS[0]["kind"] == "days" and h.GRANTS[0]["amount"] == 7 and h.GRANTS[0]["key"] == f"push:1:1:2026", f"days granted with the idempotency key ({h.GRANTS})")
    h.ok(d["status"] == "sent" and d["gift_status"] == "granted" and d["gift_kind"] == "days", "delivery: sent, gift granted")
    h.ok(gw.sent[0][1].text == "Привет, Имя1! Ваш подарок: +7 дней." and not gw.sent[0][1].used_no_gift, f"{{gift}} text in the message ({gw.sent[0][1].text!r})")
    await setup({"kind": "traffic", "gb": 5})
    await run(h.FakeGateway())
    h.ok(h.GRANTS[0]["kind"] == "traffic" and h.GRANTS[0]["amount"] == 5 * 1024**3, "traffic granted in bytes")
    await setup({"kind": "balance", "amount_minor": 15000}, subscribed=False)
    gw = h.FakeGateway()
    await run(gw, currency="USD")
    h.ok(h.GRANTS[0]["kind"] == "balance" and h.GRANTS[0]["amount"] == 15000 and h.GRANTS[0]["currency"] == "USD", "balance in minor units, currency from the settings")
    h.ok("+150 $ на баланс" in gw.sent[0][1].text, "balance gift text uses the currency symbol")
    h.BALANCES[1] = 25050
    await setup({"kind": "balance", "amount_minor": 100}, subscribed=False, templates={"ru": {"text": "Баланс: {balance}"}})
    h.BALANCES[1] = 25050
    gw = h.FakeGateway()
    await run(gw)
    h.ok(gw.sent[0][1].text == "Баланс: 250,50 ₽", f"{{balance}} placeholder ({gw.sent[0][1].text!r})")

    print("no active subscription")
    for kind, gift in (("days", {"kind": "days", "days": 7}), ("traffic", {"kind": "traffic", "gb": 1})):
        await setup(gift, subscribed=False)
        gw = h.FakeGateway()
        await run(gw)
        d = await delivery()
        h.ok(not h.GRANTS and d["status"] == "sent" and d["gift_status"] == "not_eligible", f"{kind}: no subscription -> no grant, greeting still sent")
        h.ok(gw.sent[0][1].text == "Привет, Имя1! Без подарка." and gw.sent[0][1].used_no_gift, f"{kind}: text_no_gift used")
        await setup(gift, subscribed=False, fallback="skip")
        gw = h.FakeGateway()
        await run(gw)
        d = await delivery()
        h.ok(not gw.sent and not h.GRANTS and d["status"] == "skipped" and d["reason"] == "gift_not_eligible", f"{kind}: fallback 'skip' -> skipped(gift_not_eligible)")
    await setup({"kind": "days", "days": 7}, subscribed=False, templates={"ru": {"text": "Привет![[ Подарок: {gift}.]] Хорошего дня"}})
    gw = h.FakeGateway()
    await run(gw)
    h.ok(gw.sent[0][1].text == "Привет! Хорошего дня", "without text_no_gift the optional gift block just disappears")
    # a subscription that expired
    await setup({"kind": "days", "days": 7}, subscribed=False)
    await db.add_sub(1, active=True, days=-3)
    await run(h.FakeGateway())
    h.ok((await delivery())["gift_status"] == "not_eligible", "expired subscription is no subscription")
    # trial counts as an active subscription for Core's rewards
    await setup({"kind": "days", "days": 3}, subscribed=False)
    await db.add_sub(1, provider="trial", tariff=None, status="TRIAL")
    await run(h.FakeGateway())
    h.ok((await delivery())["gift_status"] == "granted", "an active trial subscription can receive days")
    # balance / promo do not need a subscription
    await setup({"kind": "promo_discount", "percent": 15}, subscribed=False)
    await run(h.FakeGateway())
    h.ok((await delivery())["gift_status"] == "granted", "promo gift needs no subscription")

    print("promo_discount")
    await setup({"kind": "promo_discount", "percent": 20, "valid_days": 10, "applies_to": "all"})
    gw = h.FakeGateway()
    await run(gw)
    d = await delivery()
    code = await db.one("select * from promo_codes where code = :c", c=d["promo_code"])
    h.ok(code and code["user_id"] == 1 and float(code["discount_percent"]) == 20 and code["origin"] == "kiro-push", "personal code issued for the user, origin kiro-push")
    h.ok(timedelta(days=9) < code["valid_until"] - NOW < timedelta(days=11), "code valid for 10 days")
    h.ok(d["promo_code_id"] == code["promo_code_id"] and d["gift_status"] == "granted", "promo_code_id stored on the delivery (attribution)")
    h.ok(d["promo_code"] in gw.sent[0][1].text and "(20%)" in gw.sent[0][1].text, f"{{promo_code}} / {{promo_discount}} in the text ({gw.sent[0][1].text!r})")
    h.ok(len(h.ISSUED_CODES) == 1, "exactly one code issued")

    print("no double grant on message retry")
    for gift in ({"kind": "days", "days": 7}, {"kind": "promo_discount", "percent": 10}, {"kind": "balance", "amount_minor": 500}):
        await setup(gift)
        gw = h.FakeGateway(sends={100001: [tg.SendResult("failed", "net down"), tg.SendResult("failed", "net down"), tg.SendResult("sent", message_id=5)]})
        clock.now = NOW
        await run(gw)
        d = await delivery()
        h.ok(d["status"] == "pending" and d["gift_status"] == "granted", f"{gift['kind']}: message failed after the gift -> gift recorded, delivery pending")
        clock.now = NOW + timedelta(minutes=11)
        await run(gw)
        clock.now = NOW + timedelta(minutes=22)
        await run(gw)
        d = await delivery()
        h.ok(d["status"] == "sent" and d["attempts"] == 3, f"{gift['kind']}: retried until sent")
        h.ok(len(h.GRANTS) + len(h.ISSUED_CODES) == 1, f"{gift['kind']}: granted exactly once across 3 attempts ({len(h.GRANTS)} grants, {len(h.ISSUED_CODES)} codes)")
        clock.now = NOW
    await setup({"kind": "days", "days": 7})
    gw = h.FakeGateway(sends={100001: [tg.SendResult("failed", "dead")]})
    for minutes in (0, 11, 22):
        clock.now = NOW + timedelta(minutes=minutes)
        await run(gw)
    d = await delivery()
    h.ok(d["status"] == "failed" and d["gift_status"] == "granted" and len(h.GRANTS) == 1, "message failed for good after the gift: failed + gift_status granted (recorded, not repeated)")
    clock.now = NOW + timedelta(hours=3)
    await run(gw)
    h.ok(len(h.GRANTS) == 1, "failed delivery is final: no second grant")
    clock.now = NOW
    # blocked user: gift was granted, message blocked
    await setup({"kind": "days", "days": 7})
    gw = h.FakeGateway(sends={100001: [tg.SendResult("blocked", "Forbidden: bot was blocked by the user")]})
    await run(gw)
    d = await delivery()
    h.ok(d["status"] == "blocked" and d["gift_status"] == "granted" and len(h.GRANTS) == 1, "blocked user: gift granted, delivery blocked, nothing retried")
    # the same event the next year is a different period -> a new gift
    await setup({"kind": "days", "days": 7})
    await run(h.FakeGateway())
    await sch.plan_events(f, CFG, now=datetime(2027, 11, 10, 7, 0, tzinfo=UTC))
    clock.now = datetime(2027, 11, 10, 7, 0, tzinfo=UTC)
    await db.add_sub(1, tariff="start", days=400)
    await run(h.FakeGateway())
    h.ok(len(h.GRANTS) == 2 and {g["key"] for g in h.GRANTS} == {"push:1:1:2026", "push:1:1:2027"}, "next year: new period key -> new gift")
    clock.now = NOW

    print("transient grant errors")
    await setup({"kind": "days", "days": 7})
    real_grant = gifts.grant
    calls = {"n": 0}

    async def flaky(session, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database hiccup")
        return await real_grant(session, **kw)

    gifts.grant = flaky
    gw = h.FakeGateway()
    await run(gw)
    d = await delivery()
    h.ok(d["status"] == "pending" and d["gift_status"] == "pending" and not gw.sent and not h.GRANTS, "transient grant error: nothing sent, delivery retried later")
    clock.now = NOW + timedelta(minutes=11)
    await run(gw)
    clock.now = NOW
    d = await delivery()
    h.ok(d["status"] == "sent" and d["gift_status"] == "granted" and len(h.GRANTS) == 1 and len(gw.sent) == 1, "…then granted and sent once")
    gifts.grant = real_grant
    # permanent Core refusal -> fallback
    await setup({"kind": "days", "days": 7})

    async def refuse(session, **kw):
        raise gifts.ExtensionError("access_denied", 403)

    gifts.grant = refuse
    gw = h.FakeGateway()
    await run(gw)
    d = await delivery()
    h.ok(d["status"] == "sent" and d["gift_status"] == "failed" and d["gift_note"] == "access_denied" and gw.sent[0][1].used_no_gift, "permanent Core error -> greeting without the gift, reason stored")
    gifts.grant = real_grant

    print("wheel_spins")
    await db.without_wheel()
    await setup({"kind": "none"})
    await db.exec("update ext_kiro_push_events set gift = cast(:g as jsonb)", g=json.dumps({"kind": "wheel_spins", "spins": 2, "ignore_cap": False}))
    await db.exec("update ext_kiro_push_deliveries set gift_kind = 'wheel_spins', gift_status = 'pending'")
    gw = h.FakeGateway()
    await run(gw)
    d = await delivery()
    h.ok(d["status"] == "sent" and d["gift_status"] == "unavailable" and d["gift_note"] == "wheel_not_installed" and gw.sent[0][1].used_no_gift, "wheel absent at delivery: skipped gift with a reason, greeting without the gift")
    await setup({"kind": "none"}, fallback="skip")
    await db.exec("update ext_kiro_push_events set gift = cast(:g as jsonb)", g=json.dumps({"kind": "wheel_spins", "spins": 2}))
    await db.exec("update ext_kiro_push_deliveries set gift_kind = 'wheel_spins', gift_status = 'pending'")
    gw = h.FakeGateway()
    await run(gw)
    d = await delivery()
    h.ok(not gw.sent and d["status"] == "skipped" and d["reason"] == "gift_unavailable", "wheel absent + fallback skip -> skipped(gift_unavailable)")
    async with f() as s:
        h.ok(not await gifts.wheel_installed(s), "wheel not detected without its tables")
    await db.with_wheel()
    async with f() as s:
        h.ok(await gifts.wheel_installed(s), "wheel detected by ext_kiro_wheel_credits + ext_kiro_wheel_grants")
    await db.exec("drop table ext_kiro_wheel_grants")
    async with f() as s:
        h.ok(not await gifts.wheel_installed(s), "one table missing -> not installed")
    await db.without_wheel()
    await db.with_wheel()
    await db.exec("truncate table ext_kiro_wheel_credits, ext_kiro_wheel_grants")
    await setup({"kind": "none"}, users=3, subscribed=False)
    await db.exec("update ext_kiro_push_events set gift = cast(:g as jsonb)", g=json.dumps({"kind": "wheel_spins", "spins": 3, "ignore_cap": False}))
    await db.exec("update ext_kiro_push_deliveries set gift_kind = 'wheel_spins', gift_status = 'pending'")
    await db.exec("insert into ext_kiro_wheel_credits (user_id, spins) values (2, 4) on conflict (user_id) do update set spins = excluded.spins")
    gw = h.FakeGateway()
    await run(gw, )
    credits = {r["user_id"]: r["spins"] for r in await db.q("select user_id, spins from ext_kiro_wheel_credits")}
    h.ok(credits[1] == 3, "tickets credited (3)")
    h.ok(credits[2] == 5, "cap from ext_kiro_wheel_config.max_bonus_spins (4 + 3 -> 5)")
    ledger = await db.one("select * from ext_kiro_wheel_grants where user_id = 1")
    h.ok(ledger["key"] == "push:1:1:2026" and ledger["reason"] == "push" and ledger["spins"] == 3, "ledger row: key push:{event}:{user}:{period}, reason 'push'")
    statuses = [(await db.one("select gift_status from ext_kiro_push_deliveries where user_id = :u", u=u))["gift_status"] for u in (1, 2, 3)]
    h.ok(statuses == ["granted"] * 3, "all three deliveries record the grant")
    await db.exec("truncate table ext_kiro_wheel_credits, ext_kiro_wheel_grants")
    await setup({"kind": "none"}, users=1, subscribed=False)
    await db.exec("update ext_kiro_push_events set gift = cast(:g as jsonb)", g=json.dumps({"kind": "wheel_spins", "spins": 3, "ignore_cap": True}))
    await db.exec("update ext_kiro_push_deliveries set gift_kind = 'wheel_spins', gift_status = 'pending'")
    await db.exec("insert into ext_kiro_wheel_credits (user_id, spins) values (1, 4) on conflict (user_id) do update set spins = excluded.spins")
    await run(h.FakeGateway())
    h.ok((await db.one("select spins from ext_kiro_wheel_credits where user_id = 1"))["spins"] == 7, "ignore_cap: 4 + 3 = 7")
    # idempotent by key: a repeated grant does not add again
    d = dict(await delivery())
    out = await gifts.apply_gift(f, {**d}, {"kind": "wheel_spins", "spins": 3, "ignore_cap": True}, currency="RUB")
    h.ok(out.status == "granted" and (await db.one("select spins from ext_kiro_wheel_credits where user_id = 1"))["spins"] == 7, "repeat apply_gift: already granted, no extra tickets")
    await db.exec("update ext_kiro_push_deliveries set gift_status = 'pending'")
    out = await gifts.apply_gift(f, {**d}, {"kind": "wheel_spins", "spins": 3, "ignore_cap": True}, currency="RUB")
    h.ok(out.note == "already" and (await db.one("select spins from ext_kiro_wheel_credits where user_id = 1"))["spins"] == 7, "even with the delivery flag reset the ledger key stops a second credit")
    await db.exec("truncate table ext_kiro_wheel_credits, ext_kiro_wheel_grants")
    await setup({"kind": "none"}, users=1, subscribed=False, templates={"ru": {"text": "Билетов: {spins}"}})
    await db.exec("insert into ext_kiro_wheel_credits (user_id, spins) values (1, 2) on conflict (user_id) do update set spins = excluded.spins")
    gw = h.FakeGateway()
    await run(gw)
    h.ok(gw.sent[0][1].text == "Билетов: 2", "{spins} placeholder from the wheel credits")
    await db.without_wheel()

    h.finish()


def main() -> None:
    pure_tests()
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
