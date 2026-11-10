"""Tables, defaults and config validation. Every table is plugin-owned (ext_kiro_push_*)."""

from __future__ import annotations

import json
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession

from db.migrator.engine import Migration

from .errors import PushError

PLUGIN_ID = "kiro-push"

DEFAULT_CONSENT = "Даю согласие на обработку даты рождения"

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "timezone": "Europe/Moscow",
    "default_language": "ru",
    # Telegram allows ~30 msg/s overall for a bot; stay below.
    "send_rate_per_second": 15,
    "scan_rate_per_second": 20,
    "batch_size": 400,  # max deliveries claimed per tick
    "catchup_hours": 24,  # how long after the send time a missed delivery is still sent
    "max_send_attempts": 3,
    "retry_minutes": 10,
    # birthdates
    "auto_backfill": False,
    "scan_new_users": True,
    "rescan_missing_days": 30,
    "rescan_max_attempts": 3,
    "blocked_retry_days": 90,
    # customer self-entry
    "user_entry_enabled": True,
    "consent_text": DEFAULT_CONSENT,
    "user_attempts_per_hour": 5,
    "debug_log": False,
}

CONFIG_BOUNDS = {
    "send_rate_per_second": (1, 28),
    "scan_rate_per_second": (1, 28),
    "batch_size": (10, 5000),
    "catchup_hours": (1, 168),
    "max_send_attempts": (1, 10),
    "retry_minutes": (1, 1440),
    "rescan_missing_days": (1, 365),
    "rescan_max_attempts": (1, 20),
    "blocked_retry_days": (1, 3650),
    "user_attempts_per_hour": (1, 50),
}
BOOL_KEYS = ("enabled", "auto_backfill", "scan_new_users", "user_entry_enabled", "debug_log")
LANGUAGES = ("ru", "en")


def _upgrade_0001(connection: Connection) -> None:
    for statement in (
        """
        CREATE TABLE IF NOT EXISTS ext_kiro_push_config (
            id INTEGER PRIMARY KEY,
            data JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS ext_kiro_push_meta (
            key VARCHAR(64) PRIMARY KEY,
            value JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS ext_kiro_push_birthdates (
            user_id BIGINT PRIMARY KEY,
            day SMALLINT NULL,
            month SMALLINT NULL,
            year SMALLINT NULL,
            source VARCHAR(12) NULL,
            locked BOOLEAN NOT NULL DEFAULT FALSE,
            status VARCHAR(12) NOT NULL DEFAULT 'absent',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_attempt_at TIMESTAMPTZ NULL,
            fetched_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_ext_kiro_push_bd_day CHECK (day IS NULL OR day BETWEEN 1 AND 31),
            CONSTRAINT ck_ext_kiro_push_bd_month CHECK (month IS NULL OR month BETWEEN 1 AND 12)
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_ext_kiro_push_bd_md ON ext_kiro_push_birthdates (month, day)",
        "CREATE INDEX IF NOT EXISTS ix_ext_kiro_push_bd_status ON ext_kiro_push_birthdates (status, last_attempt_at)",
        """
        CREATE TABLE IF NOT EXISTS ext_kiro_push_birthdate_audit (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            action VARCHAR(12) NOT NULL,
            source VARCHAR(12) NULL,
            actor VARCHAR(40) NOT NULL,
            old_value VARCHAR(16) NULL,
            new_value VARCHAR(16) NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_ext_kiro_push_bd_audit_user ON ext_kiro_push_birthdate_audit (user_id, created_at)",
        """
        CREATE TABLE IF NOT EXISTS ext_kiro_push_user_attempts (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            ok BOOLEAN NOT NULL DEFAULT FALSE,
            code VARCHAR(40) NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_ext_kiro_push_attempts_user ON ext_kiro_push_user_attempts (user_id, created_at)",
        """
        CREATE TABLE IF NOT EXISTS ext_kiro_push_backfill (
            id INTEGER PRIMARY KEY,
            status VARCHAR(12) NOT NULL DEFAULT 'idle',
            dry_run BOOLEAN NOT NULL DEFAULT FALSE,
            rate INTEGER NOT NULL DEFAULT 20,
            cursor BIGINT NULL,
            total INTEGER NOT NULL DEFAULT 0,
            scanned INTEGER NOT NULL DEFAULT 0,
            found INTEGER NOT NULL DEFAULT 0,
            absent INTEGER NOT NULL DEFAULT 0,
            blocked INTEGER NOT NULL DEFAULT 0,
            errors INTEGER NOT NULL DEFAULT 0,
            rate_limited INTEGER NOT NULL DEFAULT 0,
            paused_until TIMESTAMPTZ NULL,
            last_error VARCHAR(200) NULL,
            started_at TIMESTAMPTZ NULL,
            finished_at TIMESTAMPTZ NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS ext_kiro_push_events (
            id SERIAL PRIMARY KEY,
            name VARCHAR(120) NOT NULL,
            kind VARCHAR(32) NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT FALSE,
            params JSONB NOT NULL DEFAULT '{}'::jsonb,
            audience JSONB NOT NULL DEFAULT '{}'::jsonb,
            send_time VARCHAR(5) NOT NULL DEFAULT '10:00',
            feb29 VARCHAR(8) NOT NULL DEFAULT 'feb28',
            gift JSONB NOT NULL DEFAULT '{"kind": "none"}'::jsonb,
            gift_fallback VARCHAR(20) NOT NULL DEFAULT 'send_without_gift',
            templates JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            deleted_at TIMESTAMPTZ NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS ext_kiro_push_plans (
            event_id INTEGER NOT NULL,
            plan_key VARCHAR(40) NOT NULL,
            planned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            matched INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (event_id, plan_key)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS ext_kiro_push_deliveries (
            id BIGSERIAL PRIMARY KEY,
            event_id INTEGER NOT NULL,
            user_id BIGINT NOT NULL,
            period_key VARCHAR(40) NOT NULL,
            base_year INTEGER NULL,
            status VARCHAR(10) NOT NULL DEFAULT 'pending',
            reason VARCHAR(80) NULL,
            language VARCHAR(16) NULL,
            gift_kind VARCHAR(20) NOT NULL DEFAULT 'none',
            gift_status VARCHAR(16) NULL,
            gift_note VARCHAR(120) NULL,
            promo_code VARCHAR(64) NULL,
            promo_code_id INTEGER NULL,
            message_id BIGINT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            batch_id VARCHAR(32) NULL,
            scheduled_for TIMESTAMPTZ NULL,
            next_try_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            claimed_at TIMESTAMPTZ NULL,
            send_started_at TIMESTAMPTZ NULL,
            sent_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_ext_kiro_push_delivery UNIQUE (event_id, user_id, period_key)
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_ext_kiro_push_deliv_due ON ext_kiro_push_deliveries (status, next_try_at)",
        "CREATE INDEX IF NOT EXISTS ix_ext_kiro_push_deliv_event ON ext_kiro_push_deliveries (event_id, status)",
        "CREATE INDEX IF NOT EXISTS ix_ext_kiro_push_deliv_user ON ext_kiro_push_deliveries (user_id, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_ext_kiro_push_deliv_promo ON ext_kiro_push_deliveries (promo_code_id)",
        """
        CREATE TABLE IF NOT EXISTS ext_kiro_push_batches (
            batch_id VARCHAR(32) PRIMARY KEY,
            event_id INTEGER NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            matched INTEGER NOT NULL DEFAULT 0,
            queued INTEGER NOT NULL DEFAULT 0
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS ext_kiro_push_blocked (
            user_id BIGINT PRIMARY KEY,
            reason VARCHAR(120) NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS ext_kiro_push_media (
            id VARCHAR(40) PRIMARY KEY,
            kind VARCHAR(12) NOT NULL,
            filename VARCHAR(120) NOT NULL DEFAULT '',
            content_type VARCHAR(80) NOT NULL DEFAULT '',
            size INTEGER NOT NULL DEFAULT 0,
            expected_size INTEGER NOT NULL DEFAULT 0,
            body BYTEA NOT NULL DEFAULT ''::bytea,
            complete BOOLEAN NOT NULL DEFAULT FALSE,
            tg_file_id TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS ext_kiro_push_log (
            id BIGSERIAL PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            level VARCHAR(8) NOT NULL,
            event VARCHAR(48) NOT NULL,
            user_id BIGINT NULL,
            path VARCHAR(160) NULL,
            status SMALLINT NULL,
            detail JSONB NOT NULL DEFAULT '{}'::jsonb
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_ext_kiro_push_log_created ON ext_kiro_push_log (created_at)",
    ):
        connection.execute(text(statement))


MIGRATIONS = [
    Migration(
        id=f"{PLUGIN_ID}.0001_initial",
        description="Push: config, birthdates, events, deliveries, media, journal",
        upgrade=_upgrade_0001,
    ),
]


def valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False
    return True


def clean_config(body: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    data = dict(current)
    for key in BOOL_KEYS:
        if key in body:
            data[key] = bool(body[key])
    for key, (lo, hi) in CONFIG_BOUNDS.items():
        if key in body:
            try:
                number = int(float(body[key]))
            except (TypeError, ValueError):
                raise PushError(f"invalid_{key}") from None
            if not lo <= number <= hi:
                raise PushError(f"invalid_{key}")
            data[key] = number
    if "timezone" in body:
        zone = str(body["timezone"] or "").strip()
        if not valid_timezone(zone):
            raise PushError("invalid_timezone")
        data["timezone"] = zone
    if "default_language" in body:
        lang = str(body["default_language"] or "").strip().lower()
        if lang not in LANGUAGES:
            raise PushError("invalid_default_language")
        data["default_language"] = lang
    if "consent_text" in body:
        consent = " ".join(str(body["consent_text"] or "").split())
        if not 5 <= len(consent) <= 300:
            raise PushError("invalid_consent_text")
        data["consent_text"] = consent
    return data


def jload(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return {}
    return value if value is not None else {}


async def load_config(session: AsyncSession) -> dict[str, Any]:
    raw = jload(await session.scalar(text("select data from ext_kiro_push_config where id = 1")))
    data = dict(DEFAULT_CONFIG)
    if isinstance(raw, dict):
        data.update({k: v for k, v in raw.items() if k in DEFAULT_CONFIG})
    from . import diag

    diag.set_debug(bool(data.get("debug_log")))
    return data


async def save_config(session: AsyncSession, data: dict[str, Any]) -> None:
    await session.execute(
        text(
            "insert into ext_kiro_push_config (id, data, updated_at) values (1, cast(:d as jsonb), now()) "
            "on conflict (id) do update set data = excluded.data, updated_at = now()"
        ),
        {"d": json.dumps(data, ensure_ascii=False)},
    )


async def get_meta(session: AsyncSession, key: str) -> Any:
    return jload(await session.scalar(text("select value from ext_kiro_push_meta where key = :k"), {"k": key}))


async def set_meta(session: AsyncSession, key: str, value: Any) -> None:
    await session.execute(
        text(
            "insert into ext_kiro_push_meta (key, value) values (:k, cast(:v as jsonb)) "
            "on conflict (key) do update set value = excluded.value, updated_at = now()"
        ),
        {"k": key, "v": json.dumps(value)},
    )
