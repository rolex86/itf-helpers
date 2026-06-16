from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config.settings import DateRangeConfig


PRESET_TO_DAYS = {
    "LAST_30_DAYS": 30,
    "LAST_90_DAYS": 90,
    "LAST_365_DAYS": 365,
}


@dataclass(slots=True)
class ResolvedDateRange:
    date_from: date
    date_to: date
    label: str
    change_history_from: date
    change_history_to: date
    export_date: date
    warnings: list[str] = field(default_factory=list)


def parse_iso_date(value: str | None) -> date | None:
    if value in (None, "", "null"):
        return None
    return date.fromisoformat(str(value))


def resolve_date_range(config: DateRangeConfig) -> ResolvedDateRange:
    today = date.today()
    warnings: list[str] = []

    if config.date_from and config.date_to:
        date_from = config.date_from
        date_to = config.date_to
        label = "CUSTOM"
    else:
        preset = config.preset or "LAST_90_DAYS"
        if preset not in PRESET_TO_DAYS:
            raise ValueError(
                f"Unsupported preset '{preset}'. Use LAST_30_DAYS, LAST_90_DAYS or LAST_365_DAYS."
            )
        days = PRESET_TO_DAYS[preset]
        date_to = today
        date_from = today - timedelta(days=days - 1)
        label = preset

    if date_from > date_to:
        raise ValueError("date_from cannot be after date_to.")

    change_history_to = min(date_to, today)
    min_allowed_change_date = today - timedelta(days=29)
    change_history_from = max(date_from, min_allowed_change_date)
    if change_history_from != date_from or change_history_to != date_to:
        warnings.append(
            "Change history was restricted to the last 30 days because the Google Ads API requires that window."
        )

    return ResolvedDateRange(
        date_from=date_from,
        date_to=date_to,
        label=label,
        change_history_from=change_history_from,
        change_history_to=change_history_to,
        export_date=today,
        warnings=warnings,
    )
