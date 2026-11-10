"""Shared test harness: Core stubs, a throwaway PostgreSQL (pgserver), fake Core tables and a fake Telegram gateway.

Core (bot.*, db.*) is replaced by small fakes; the plugin modules run unchanged and the real migration SQL creates
the plugin tables. Core tables are faked with the real column names (only the columns the plugin touches).

Needs: pgserver sqlalchemy[asyncio] asyncpg aiohttp
"""

from __future__ import annotations

import importlib
import sys
import tempfile
import types
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pgserver
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles default to a legacy codepage
except Exception:  # noqa: BLE001
    pass

BACKEND = Path(__file__).resolve().parents[1] / "backend"
STATE = SimpleNamespace(passed=0)

# in-memory ledgers of the fake Core
GRANTS: list[dict[str, Any]] = []
SEEN_KEYS: set[str] = set()
SUBSCRIBERS: list[tuple[str, Any]] = []
BALANCES: dict[int, int] = {}
ISSUED_CODES: list[str] = []


class ExtensionError(ValueError):
    def __init__(self, code: str, status: int = 409) -> None:
        super().__init__(code)
        self.code, self.status = code, status


class Migration:
    def __init__(self, id, description, upgrade):
        self.id, self.description, self.upgrade = id, description, upgrade


class Reward:
    def __init__(self, kind, amount, currency="RUB"):
        self.kind, self.amount, self.currency = kind, amount, currency


def mod(name: str, **attrs: Any) -> types.ModuleType:
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


async def fake_grant(session, *, owner, user_id, idempotency_key, reward):
    if reward.kind in ("days", "traffic"):
        active = await session.scalar(
            text("select 1 from subscriptions where user_id = :u and is_active and end_date > now()"), {"u": user_id}
        )
        if not active:
            raise ExtensionError("extension_reward_requires_subscription")
    if idempotency_key in SEEN_KEYS:
        return
    SEEN_KEYS.add(idempotency_key)
    GRANTS.append({"key": idempotency_key, "kind": reward.kind, "amount": reward.amount, "currency": reward.currency, "user_id": user_id})


class FakePromoEffects:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakePromoCodeService:
    @staticmethod
    async def issue_code(session, *, effects, code, max_activations, valid_until, origin, created_by_admin_id, user_id=None, **kw):
        value = f"PUSH{len(ISSUED_CODES) + 1:05d}"
        ISSUED_CODES.append(value)
        row = await session.execute(
            text(
                "insert into promo_codes (code, discount_percent, user_id, valid_until, origin) "
                "values (:c, :d, :u, :v, :o) returning promo_code_id"
            ),
            {"c": value, "d": getattr(effects, "discount_percent", None), "u": user_id, "v": valid_until, "o": origin},
        )
        return SimpleNamespace(code=value, promo_code_id=row.scalar())


async def fake_lock_user(session, user_id):
    row = await session.execute(text("select user_id from users where user_id = :u for update"), {"u": user_id})
    return row.first()


async def fake_balance_minor(session, user_id, currency):
    return BALANCES.get(int(user_id), 0)


def subscribe_stub(event_name, handler):
    SUBSCRIBERS.append((event_name, handler))


def install_stubs() -> None:
    for name in (
        "bot", "bot.plugins", "bot.plugins.extensions", "bot.services", "bot.app", "bot.app.web", "bot.infra", "db",
        "db.migrator",
    ):
        mod(name)
    mod("bot.plugins.extensions.contracts", ExtensionError=ExtensionError, ExtensionContributions=lambda **kw: SimpleNamespace(**kw),
        JobHandler=lambda **kw: SimpleNamespace(**kw), OperationContext=object)
    mod("bot.plugins.extensions.rewards", Reward=Reward, grant=fake_grant)
    mod("bot.services.promo_code_service", PromoCodeService=FakePromoCodeService)
    mod("bot.services.promo_effects", PromoEffects=FakePromoEffects)
    mod("bot.plugins.spec", WEB_SCOPE_WEBAPP="webapp", Plugin=type("Plugin", (), {}), PluginContext=object)
    mod("bot.app.web.context", get_session_factory=lambda request: request.app["factory"])
    mod("bot.app.web.session", extract_authenticated_user_id=lambda request: request.get("uid"))
    mod("bot.infra.events", USER_REGISTERED="user.registered", ACCOUNT_TELEGRAM_LINKED="account.telegram_linked", subscribe=subscribe_stub)
    dal = mod("db.dal", user_dal=SimpleNamespace(lock_user_by_id=fake_lock_user),
              user_balance_dal=SimpleNamespace(balance_minor=fake_balance_minor))
    sys.modules["db"].dal = dal
    mod("db.migrator.engine", Migration=Migration)
    package = types.ModuleType("kiro_push")
    package.__path__ = [str(BACKEND / "kiro_push")]
    sys.modules["kiro_push"] = package


def load(*names: str) -> list[Any]:
    install_stubs()
    return [importlib.import_module(f"kiro_push.{n}") for n in names]


def load_package() -> Any:
    """Load the real package __init__ (routes, job, plugin class) against the stubbed Core."""
    import importlib.util

    install_stubs()
    spec = importlib.util.spec_from_file_location(
        "kiro_push", BACKEND / "kiro_push" / "__init__.py", submodule_search_locations=[str(BACKEND / "kiro_push")]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["kiro_push"] = module
    spec.loader.exec_module(module)
    return module


def ok(cond: bool, label: str) -> None:
    if not cond:
        raise AssertionError(label)
    STATE.passed += 1
    print(f"  ok  {label}")


async def raises(coro, code: str, label: str, error_type: type | None = None) -> None:
    from kiro_push.errors import PushError

    try:
        await coro
    except (error_type or PushError) as exc:
        ok(exc.code == code, f"{label} -> {code}" + ("" if exc.code == code else f" (got {exc.code})"))
        return
    raise AssertionError(f"{label}: no error, expected {code}")


DDL = [
    """CREATE TABLE users (user_id BIGINT PRIMARY KEY, telegram_id BIGINT, username TEXT, first_name TEXT, language_code TEXT,
        minishop_id TEXT, is_banned BOOLEAN, registration_date TIMESTAMPTZ DEFAULT now())""",
    """CREATE TABLE subscriptions (subscription_id SERIAL PRIMARY KEY, user_id BIGINT, panel_user_uuid TEXT,
        traffic_limit_bytes BIGINT, provider TEXT, status_from_panel TEXT, tariff_key TEXT,
        end_date TIMESTAMPTZ NOT NULL, is_active BOOLEAN DEFAULT TRUE)""",
    """CREATE TABLE promo_codes (promo_code_id SERIAL PRIMARY KEY, code TEXT UNIQUE NOT NULL, discount_percent NUMERIC,
        user_id BIGINT, valid_until TIMESTAMPTZ, origin TEXT)""",
    """CREATE TABLE promo_code_activations (activation_id SERIAL PRIMARY KEY, promo_code_id INT NOT NULL, user_id BIGINT NOT NULL,
        activated_at TIMESTAMPTZ DEFAULT now())""",
]
WHEEL_DDL = [
    "CREATE TABLE ext_kiro_wheel_config (id INTEGER PRIMARY KEY, data JSONB NOT NULL)",
    "CREATE TABLE ext_kiro_wheel_credits (user_id BIGINT PRIMARY KEY, spins INTEGER NOT NULL DEFAULT 0, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
    """CREATE TABLE ext_kiro_wheel_grants (key VARCHAR(128) PRIMARY KEY, user_id BIGINT NOT NULL, spins INTEGER NOT NULL,
        reason VARCHAR(32) NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
]


class Db:
    def __init__(self, server: Any, engine: Any, factory: Callable[[], AsyncSession]) -> None:
        self.server, self.engine, self.factory = server, engine, factory

    async def q(self, sql: str, **params: Any) -> list[Any]:
        async with self.factory() as s:
            rows = (await s.execute(text(sql), params)).mappings().all()
            await s.commit()  # also makes `insert ... returning` stick
            return rows

    async def one(self, sql: str, **params: Any) -> Any:
        rows = await self.q(sql, **params)
        return rows[0] if rows else None

    async def exec(self, sql: str, **params: Any) -> None:
        async with self.factory() as s:
            await s.execute(text(sql), params)
            await s.commit()

    async def count(self, table: str, where: str = "true") -> int:
        return int((await self.q(f"select count(*) as n from {table} where {where}"))[0]["n"])

    async def add_user(self, uid: int, tg: int | None = None, username: str | None = None, *, name: str | None = "Имя",
                       lang: str | None = "ru", banned: bool | None = False, reg: datetime | None = None) -> None:
        await self.exec(
            "insert into users (user_id, telegram_id, username, first_name, language_code, is_banned, registration_date) "
            "values (:u, :t, :n, :f, :l, :b, :r)",
            u=uid, t=tg if tg is not None else uid + 100000, n=username, f=name, l=lang, b=banned,
            r=reg or datetime(2023, 5, 17, 12, 0, tzinfo=UTC),
        )

    async def add_sub(self, uid: int, *, days: int = 30, provider: str = "payment", tariff: str | None = "start",
                      status: str | None = "ACTIVE", active: bool = True) -> None:
        await self.exec(
            "insert into subscriptions (user_id, panel_user_uuid, provider, status_from_panel, tariff_key, end_date, is_active) "
            "values (:u, :p, :pr, :st, :t, now() + make_interval(days => :d), :a)",
            u=uid, p=f"uuid{uid}", pr=provider, st=status, t=tariff, d=days, a=active,
        )

    async def reset(self) -> None:
        for table in (
            "users", "subscriptions", "promo_codes", "promo_code_activations", "ext_kiro_push_birthdates",
            "ext_kiro_push_birthdate_audit", "ext_kiro_push_user_attempts", "ext_kiro_push_backfill", "ext_kiro_push_events",
            "ext_kiro_push_plans", "ext_kiro_push_deliveries", "ext_kiro_push_batches", "ext_kiro_push_blocked",
            "ext_kiro_push_meta", "ext_kiro_push_log", "ext_kiro_push_config",
        ):
            await self.exec(f"truncate table {table} restart identity")
        GRANTS.clear()
        SEEN_KEYS.clear()
        ISSUED_CODES.clear()
        BALANCES.clear()

    async def with_wheel(self) -> None:
        for stmt in WHEEL_DDL:
            await self.exec(stmt)
        await self.exec("insert into ext_kiro_wheel_config (id, data) values (1, cast(:d as jsonb))", d='{"max_bonus_spins": 5}')

    async def without_wheel(self) -> None:
        for table in ("ext_kiro_wheel_config", "ext_kiro_wheel_credits", "ext_kiro_wheel_grants"):
            await self.exec(f"drop table if exists {table}")


async def make_db() -> Db:
    storage = importlib.import_module("kiro_push.storage")
    server = pgserver.get_server(tempfile.mkdtemp())
    engine = create_async_engine(server.get_uri().replace("postgresql://", "postgresql+asyncpg://"))
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        for stmt in DDL:
            await conn.exec_driver_sql(stmt)
        await conn.run_sync(storage._upgrade_0001)
        await conn.run_sync(storage._upgrade_0001)  # idempotent
    return Db(server, engine, factory)


@dataclass
class FakeGateway:
    """Scripted Telegram: per telegram_id lists of lookups / send results, everything recorded."""

    lookups: dict[int, list[Any]] = field(default_factory=dict)
    sends: dict[int, list[Any]] = field(default_factory=dict)
    lookup_calls: list[int] = field(default_factory=list)
    sent: list[tuple[int, Any]] = field(default_factory=list)
    default_lookup: Any = None

    async def lookup_birthdate(self, chat_id: int):
        from kiro_push.telegram import BirthdateLookup

        self.lookup_calls.append(chat_id)
        queue = self.lookups.get(chat_id)
        if queue:
            return queue.pop(0) if len(queue) > 1 else queue[0]
        return self.default_lookup or BirthdateLookup("absent")

    async def send(self, session, *, chat_id: int, user_id: int, message):
        from kiro_push.telegram import SendResult

        self.sent.append((chat_id, message))
        queue = self.sends.get(chat_id)
        if queue:
            result = queue.pop(0) if len(queue) > 1 else queue[0]
            if result.status != "sent":
                self.sent.pop()
                return result
        return SendResult("sent", message_id=1000 + len(self.sent))


async def no_sleep(_seconds: float) -> None:
    return None


class Clock:
    """A settable clock; also used as monotonic source (advances by `tick` per call when asked)."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def make_template(text_: str = "Привет, {name}!", **extra: Any) -> dict[str, Any]:
    return {"text": text_, **extra}


def finish() -> None:
    print(f"\n{STATE.passed} checks passed")
