"""Store opening hours: open or closed at a given moment, and the next opening.

Times are in the store's time zone (America/Campo_Grande, UTC-4, no daylight saving).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import holidays

TIMEZONE = ZoneInfo("America/Campo_Grande")

# weekday() -> (opens, closes). Sunday (6) is absent: always closed.
OPENING_HOURS: dict[int, tuple[time, time]] = {
    0: (time(9), time(18)),
    1: (time(9), time(18)),
    2: (time(9), time(18)),
    3: (time(9), time(18)),
    4: (time(9), time(18)),
    5: (time(9), time(13)),
}

# Public national holidays plus Mato Grosso do Sul state holidays. Municipal holidays and
# optional days (Carnival, Corpus Christi) are not covered by the package.
STORE_HOLIDAYS = holidays.Brazil(subdiv="MS")

# Customer-facing weekday names; the summary sentence goes almost verbatim to the customer.
WEEKDAYS_PT = (
    "segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
    "sexta-feira", "sábado", "domingo",
)

MAX_DAYS_AHEAD = 14


@dataclass(frozen=True)
class StoreStatus:
    is_open: bool
    now: datetime
    closes_at: datetime | None  # set when open
    next_opening: datetime | None  # set when closed
    holiday: str | None  # holiday name when today is a holiday
    summary_pt: str  # short sentence for the customer, in Brazilian Portuguese


def to_local(moment: datetime) -> datetime:
    """Convert to store time. A naive datetime is assumed to already be store time."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=TIMEZONE)
    return moment.astimezone(TIMEZONE)


def opening_hours(day: date) -> tuple[time, time] | None:
    """Opening and closing time for that day, or None when the store is closed all day."""
    if day in STORE_HOLIDAYS:
        return None
    return OPENING_HOURS.get(day.weekday())


def next_opening(after: datetime) -> datetime:
    """First opening time strictly after the given moment."""
    local = to_local(after)
    for offset in range(MAX_DAYS_AHEAD + 1):
        day = local.date() + timedelta(days=offset)
        hours = opening_hours(day)
        if hours:
            opening = datetime.combine(day, hours[0], tzinfo=TIMEZONE)
            if opening > local:
                return opening
    raise RuntimeError(f"no opening found in the next {MAX_DAYS_AHEAD} days")


def _describe_day(target: datetime, today: date) -> str:
    if target.date() == today:
        return "hoje"
    weekday_and_date = f"{WEEKDAYS_PT[target.weekday()]}, {target:%d/%m}"
    if target.date() == today + timedelta(days=1):
        return f"amanhã ({weekday_and_date})"
    return weekday_and_date


def store_status(now: datetime) -> StoreStatus:
    """Whether the store is open at `now`, when it closes or when it opens next."""
    local = to_local(now)
    today = local.date()
    holiday = STORE_HOLIDAYS.get(today)
    hours = opening_hours(today)

    if hours and hours[0] <= local.time() < hours[1]:
        closes_at = datetime.combine(today, hours[1], tzinfo=TIMEZONE)
        return StoreStatus(
            is_open=True,
            now=local,
            closes_at=closes_at,
            next_opening=None,
            holiday=None,
            summary_pt=f"Aberta agora. Fecha hoje às {closes_at:%H:%M}.",
        )

    opening = next_opening(local)
    if holiday:
        reason = f" (feriado: {holiday})"
    elif today.weekday() == 6:
        reason = " (domingo)"
    else:
        reason = ""
    when = _describe_day(opening, today)
    return StoreStatus(
        is_open=False,
        now=local,
        closes_at=None,
        next_opening=opening,
        holiday=holiday,
        summary_pt=f"Fechada agora{reason}. Abre {when} às {opening:%H:%M}.",
    )
