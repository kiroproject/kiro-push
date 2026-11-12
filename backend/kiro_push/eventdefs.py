"""Event definitions: validation and the pure date logic that decides when an event is due.

An event is {name, kind, params, enabled, audience, send_time, feb29, templates, gift, gift_fallback}.
Kinds: birthday, registration_anniversary, fixed_date, one_time, manual.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .errors import PushError
from .gifts import clean_gift
from .storage import jload
from .templates import clean_templates
from .users import SEGMENTS

KINDS = ("birthday", "registration_anniversary", "fixed_date", "one_time", "manual")
KIND_LABELS = {
    "birthday": "День рождения",
    "registration_anniversary": "Годовщина регистрации",
    "fixed_date": "Праздник (каждый год)",
    "one_time": "Разовое уведомление",
    "manual": "Ручной запуск",
}
FEB29_RULES = ("feb28", "mar1")
GIFT_FALLBACKS = ("send_without_gift", "skip")
LANGUAGE_FILTERS = ("any", "ru", "en")
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_MMDD = re.compile(r"^(\d{2})-(\d{2})$")
_BATCH_ID = re.compile(r"^[0-9A-Za-z_-]{1,32}$")


def parse_hhmm(value: str) -> tuple[int, int]:
    match = _HHMM.match(str(value or ""))
    if not match:
        raise PushError("invalid_send_time")
    return int(match.group(1)), int(match.group(2))


def clean_batch_id(raw: Any) -> str | None:
    value = str(raw or "").strip()
    if not value:
        return None
    if not _BATCH_ID.match(value):
        raise PushError("invalid_batch_id")
    return value


def clean_audience(raw: Any) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    if isinstance(raw.get("segments"), list):
        segments = [str(s) for s in raw["segments"]]
    else:
        single = str(raw.get("segment") or "all")
        segments = list(SEGMENTS) if single == "all" else [single]
    if any(s not in SEGMENTS for s in segments):
        raise PushError("invalid_audience")
    segments = [s for s in SEGMENTS if s in segments]
    if not segments:
        raise PushError("audience_empty")
    language = str(raw.get("language") or "any").lower()
    if language not in LANGUAGE_FILTERS:
        raise PushError("invalid_audience_language")
    keys: list[str] = []
    for item in raw.get("tariff_keys") or []:
        key = str(item or "").strip()
        if key and len(key) <= 64 and key not in keys:
            keys.append(key)
    skip_blocked = raw.get("skip_blocked", True)
    if skip_blocked is None:
        skip_blocked = True  # a missing key (old events) behaves as "skip"
    if not isinstance(skip_blocked, bool):
        raise PushError("invalid_audience")
    return {"segments": segments, "language": language, "tariff_keys": keys[:100], "skip_blocked": skip_blocked}


def valid_month_day(month: int, day: int) -> bool:
    return 1 <= month <= 12 and 1 <= day <= calendar.monthrange(2000, month)[1]


def clean_params(kind: str, raw: Any) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    params: dict[str, Any] = {}
    if kind in ("birthday", "registration_anniversary"):
        try:
            offset = int(raw.get("offset_days", 0) or 0)
        except (TypeError, ValueError):
            raise PushError("invalid_offset_days") from None
        if not -30 <= offset <= 30:
            raise PushError("invalid_offset_days")
        params["offset_days"] = offset
    if kind == "registration_anniversary":
        try:
            years = int(raw.get("min_years", 1) or 1)
        except (TypeError, ValueError):
            raise PushError("invalid_min_years") from None
        if not 1 <= years <= 50:
            raise PushError("invalid_min_years")
        params["min_years"] = years
    if kind == "fixed_date":
        match = _MMDD.match(str(raw.get("date") or ""))
        if not match or not valid_month_day(int(match.group(1)), int(match.group(2))):
            raise PushError("invalid_date")
        params["date"] = f"{int(match.group(1)):02d}-{int(match.group(2)):02d}"
    if kind == "one_time":
        try:
            params["date"] = date.fromisoformat(str(raw.get("date") or "")).isoformat()
        except ValueError:
            raise PushError("invalid_date") from None
    return params


def clean_event(body: dict[str, Any], *, wheel_installed: bool) -> dict[str, Any]:
    """Validate a whole event. Content problems come back as PushError('invalid_event', issues=[...])."""
    name = " ".join(str(body.get("name") or "").split())[:120]
    if not name:
        raise PushError("name_required")
    kind = str(body.get("kind") or "")
    if kind not in KINDS:
        raise PushError("invalid_kind")
    gift = clean_gift(body.get("gift"), wheel_installed=wheel_installed)
    fallback = str(body.get("gift_fallback") or "send_without_gift")
    if fallback not in GIFT_FALLBACKS:
        raise PushError("invalid_gift_fallback")
    feb29 = str(body.get("feb29") or "feb28")
    if feb29 not in FEB29_RULES:
        raise PushError("invalid_feb29")
    send_time = str(body.get("send_time") or "10:00")
    parse_hhmm(send_time)
    enabled = bool(body.get("enabled"))
    templates, errors, warnings = clean_templates(body.get("templates"), gift_kind=gift["kind"])
    if enabled and not templates:
        errors.append("Включённому событию нужен хотя бы один шаблон")
    if errors:
        raise PushError("invalid_event", issues=errors, warnings=warnings)
    return {
        "name": name,
        "kind": kind,
        "enabled": enabled,
        "params": clean_params(kind, body.get("params")),
        "audience": clean_audience(body.get("audience")),
        "send_time": send_time,
        "feb29": feb29,
        "gift": gift,
        "gift_fallback": fallback,
        "templates": templates,
        "warnings": warnings,
    }


def public_event(row: dict[str, Any]) -> dict[str, Any]:
    def iso(value: Any) -> Any:
        return value.isoformat() if hasattr(value, "isoformat") else value

    return {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "kind_label": KIND_LABELS.get(row["kind"], row["kind"]),
        "enabled": bool(row["enabled"]),
        "params": jload(row["params"]) or {},
        "audience": clean_audience(jload(row["audience"])),
        "send_time": row["send_time"],
        "feb29": row["feb29"],
        "gift": jload(row["gift"]) or {"kind": "none"},
        "gift_fallback": row["gift_fallback"],
        "templates": jload(row["templates"]) or {},
        "created_at": iso(row.get("created_at")),
        "updated_at": iso(row.get("updated_at")),
    }


# ------------------------------------------------------------------ date logic


def resolve_occurrence(year: int, month: int, day: int, feb29: str) -> date:
    """The calendar date a yearly MM-DD falls on in ``year`` (29 Feb moves by the event's rule in common years)."""
    if month == 2 and day == 29 and not calendar.isleap(year):
        return date(year, 2, 28) if feb29 == "feb28" else date(year, 3, 1)
    return date(year, month, day)


def month_day_keys(base: date, feb29: str) -> list[int]:
    """month*100+day values matching ``base`` as a birthday/anniversary. 29 Feb people also match per the rule."""
    keys = [base.month * 100 + base.day]
    if not calendar.isleap(base.year):
        if (base.month, base.day) == (2, 28) and feb29 == "feb28":
            keys.append(229)
        elif (base.month, base.day) == (3, 1) and feb29 == "mar1":
            keys.append(229)
    return keys


@dataclass(frozen=True)
class DueSlot:
    local_date: date  # the day the message goes out (in the configured timezone)
    base_date: date  # the birthday / anniversary / holiday date the slot is for
    period_key: str
    scheduled_for: datetime  # tz-aware instant of local_date at send_time

    @property
    def plan_key(self) -> str:
        return self.local_date.isoformat()


def candidate_days(now: datetime, tz: ZoneInfo, send_time: str, catchup_hours: int) -> list[tuple[date, datetime]]:
    """Local dates whose send time has passed no longer than ``catchup_hours`` ago, newest first."""
    hh, mm = parse_hhmm(send_time)
    now_local = now.astimezone(tz)
    out: list[tuple[date, datetime]] = []
    for back in range(0, catchup_hours // 24 + 2):
        day = now_local.date() - timedelta(days=back)
        scheduled = datetime.combine(day, time(hh, mm), tzinfo=tz)
        if scheduled <= now_local and now_local - scheduled < timedelta(hours=catchup_hours):
            out.append((day, scheduled))
    return out


def due_slots(event: dict[str, Any], now: datetime, tz: ZoneInfo, catchup_hours: int) -> list[DueSlot]:
    """Slots of an automatic event that are due at ``now`` (manual events never are)."""
    kind = event["kind"]
    if kind == "manual":
        return []
    params = jload(event["params"]) or {}
    slots: list[DueSlot] = []
    for day, scheduled in candidate_days(now, tz, event["send_time"], catchup_hours):
        if kind in ("birthday", "registration_anniversary"):
            base = day - timedelta(days=int(params.get("offset_days", 0)))
            slots.append(DueSlot(day, base, str(base.year), scheduled))
        elif kind == "fixed_date":
            month, dd = (int(x) for x in str(params["date"]).split("-"))
            for year in (day.year, day.year + 1, day.year - 1):
                occurrence = resolve_occurrence(year, month, dd, event["feb29"])
                if occurrence == day:
                    slots.append(DueSlot(day, occurrence, str(year), scheduled))
                    break
        elif kind == "one_time":
            if str(params.get("date")) == day.isoformat():
                slots.append(DueSlot(day, day, day.isoformat(), scheduled))
    return slots
