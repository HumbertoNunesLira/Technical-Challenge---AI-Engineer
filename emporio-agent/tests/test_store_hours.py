from datetime import UTC, datetime

import pytest

from emporio.store_hours import TIMEZONE, store_status


def local(text):
    """'2026-03-23 10:00' in store time."""
    return datetime.fromisoformat(text).replace(tzinfo=TIMEZONE)


# 2026-03-21 Sat, 2026-03-22 Sun, 2026-03-23 Mon.
# 2026-04-03 Fri is Good Friday; 2026-11-02 Mon is Finados (All Souls' Day).
@pytest.mark.parametrize(
    ("now", "is_open", "boundary"),
    [
        ("2026-03-23 10:00", True, "2026-03-23 18:00"),  # weekday, open: closes at 18:00
        ("2026-03-23 17:59", True, "2026-03-23 18:00"),
        ("2026-03-23 18:00", False, "2026-03-24 09:00"),  # 18:00 sharp is already closed
        ("2026-03-23 08:59", False, "2026-03-23 09:00"),  # opens later the same day
        ("2026-03-21 12:59", True, "2026-03-21 13:00"),  # Saturday until 13:00
        ("2026-03-21 13:00", False, "2026-03-23 09:00"),  # Saturday 13:00 -> Monday
        ("2026-03-22 15:00", False, "2026-03-23 09:00"),  # Sunday -> Monday
        ("2026-11-02 10:00", False, "2026-11-03 09:00"),  # Finados on a Monday
        ("2026-04-03 10:00", False, "2026-04-04 09:00"),  # Good Friday -> Saturday
    ],
)
def test_store_status(now, is_open, boundary):
    status = store_status(local(now))
    assert status.is_open is is_open
    if is_open:
        assert status.closes_at == local(boundary)
        assert status.next_opening is None
    else:
        assert status.next_opening == local(boundary)
        assert status.closes_at is None


def test_sunday_summary_in_portuguese():
    status = store_status(local("2026-03-22 15:00"))
    expected = "Fechada agora (domingo). Abre amanhã (segunda-feira, 23/03) às 09:00."
    assert status.summary_pt == expected


def test_holiday_summary_names_the_holiday():
    status = store_status(local("2026-11-02 10:00"))
    assert status.holiday == "Finados"
    assert "feriado: Finados" in status.summary_pt


def test_open_summary():
    summary = store_status(local("2026-03-23 10:00")).summary_pt
    assert summary == "Aberta agora. Fecha hoje às 18:00."


def test_saturday_afternoon_skips_sunday():
    summary = store_status(local("2026-03-21 13:00")).summary_pt
    assert summary == "Fechada agora. Abre segunda-feira, 23/03 às 09:00."


def test_utc_input_is_converted_to_store_time():
    # 13:30 UTC is 09:30 in Campo Grande (UTC-4): open.
    status = store_status(datetime(2026, 3, 23, 13, 30, tzinfo=UTC))
    assert status.is_open
    assert status.now.hour == 9


def test_naive_input_is_treated_as_store_time():
    assert store_status(datetime(2026, 3, 23, 9, 0)).is_open
