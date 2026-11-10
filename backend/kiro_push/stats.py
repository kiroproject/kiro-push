"""Statistics for the admin overview: birthdate coverage, upcoming birthdays, per-event delivery results."""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Any

from sqlalchemy import text

_ELIGIBLE = "u.telegram_id is not null and not coalesce(u.is_banned, false)"
DELIVERY_STATUSES = ("pending", "sending", "sent", "skipped", "failed", "blocked")


async def birthdate_stats(session: Any, today: date) -> dict[str, Any]:
    eligible = int(await session.scalar(text(f"select count(*) from users u where {_ELIGIBLE}")) or 0)
    rows = (
        await session.execute(
            text(
                "select b.status, b.source, count(*) as n from ext_kiro_push_birthdates b join users u on u.user_id = b.user_id "
                f"where {_ELIGIBLE} group by b.status, b.source"
            )
        )
    ).mappings().all()
    by_source = {"telegram": 0, "user": 0, "admin": 0}
    by_status: dict[str, int] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + int(r["n"])
        if r["status"] == "found" and r["source"] in by_source:
            by_source[r["source"]] += int(r["n"])
    with_row = sum(by_status.values())
    collected = by_status.get("found", 0)
    return {
        "eligible_users": eligible,
        "collected": collected,
        "by_source": by_source,
        "hidden": by_status.get("absent", 0),  # Telegram exposes no date (may be re-checked later)
        "blocked": by_status.get("blocked", 0),
        "errors": by_status.get("error", 0),
        "cleared": by_status.get("cleared", 0),
        "unknown": max(eligible - with_row, 0),  # never looked up yet
        "coverage": round(collected / eligible, 4) if eligible else None,
        "with_year": int(
            await session.scalar(
                text(
                    "select count(*) from ext_kiro_push_birthdates b join users u on u.user_id = b.user_id "
                    f"where b.day is not null and b.year is not null and {_ELIGIBLE}"
                )
            )
            or 0
        ),
    }


async def upcoming_birthdays(session: Any, today: date, days: int = 30) -> dict[str, Any]:
    rows = (
        await session.execute(
            text(
                "select b.month, b.day, count(*) as n from ext_kiro_push_birthdates b join users u on u.user_id = b.user_id "
                f"where b.day is not null and {_ELIGIBLE} group by b.month, b.day"
            )
        )
    ).all()
    counts = {(int(m), int(d)): int(n) for m, d, n in rows}
    out = []
    total = 0
    for offset in range(days):
        day = today + timedelta(days=offset)
        n = counts.get((day.month, day.day), 0)
        if (day.month, day.day) == (2, 28) and not calendar.isleap(day.year):
            n += counts.get((2, 29), 0)  # 29 Feb people are greeted on 28 Feb in common years (default rule)
        out.append({"date": day.isoformat(), "count": n})
        total += n
    return {"days": out, "total": total}


async def delivery_stats(session: Any) -> dict[str, Any]:
    rows = (
        await session.execute(
            text(
                "select d.event_id, d.status, count(*) as n from ext_kiro_push_deliveries d group by d.event_id, d.status"
            )
        )
    ).all()
    per_event: dict[int, dict[str, int]] = {}
    for event_id, status, n in rows:
        per_event.setdefault(int(event_id), dict.fromkeys(DELIVERY_STATUSES, 0))[status] = int(n)
    gift_rows = (
        await session.execute(
            text(
                "select event_id, count(*) filter (where gift_status = 'granted') as granted, "
                "count(*) filter (where gift_kind <> 'none' and gift_status in ('not_eligible', 'unavailable', 'failed')) as withheld "
                "from ext_kiro_push_deliveries group by event_id"
            )
        )
    ).all()
    gifts_by_event = {int(e): {"granted": int(g), "withheld": int(w)} for e, g, w in gift_rows}
    promo_rows = (
        await session.execute(
            text(
                "select d.event_id, count(distinct d.id) as issued, count(a.activation_id) as activated "
                "from ext_kiro_push_deliveries d left join promo_code_activations a "
                "on a.promo_code_id = d.promo_code_id and a.user_id = d.user_id "
                "where d.promo_code_id is not null group by d.event_id"
            )
        )
    ).all()
    promo_by_event = {int(e): {"issued": int(i), "activated": int(a)} for e, i, a in promo_rows}
    total = dict.fromkeys(DELIVERY_STATUSES, 0)
    for counts in per_event.values():
        for k, v in counts.items():
            total[k] += v
    return {
        "total": total,
        "events": {
            str(eid): {
                **counts, "gifts": gifts_by_event.get(eid, {"granted": 0, "withheld": 0}),
                "promo": promo_by_event.get(eid, {"issued": 0, "activated": 0}),
            }
            for eid, counts in per_event.items()
        },
        "promo": {
            "issued": sum(p["issued"] for p in promo_by_event.values()),
            "activated": sum(p["activated"] for p in promo_by_event.values()),
        },
    }
