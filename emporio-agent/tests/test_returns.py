from datetime import date, datetime

import pytest

from emporio.data import get_data
from emporio.returns import return_windows
from emporio.store_hours import TIMEZONE


def order(order_id):
    return get_data().orders.set_index("order_id").loc[order_id]


def at(day):
    return datetime.fromisoformat(day).replace(hour=10, tzinfo=TIMEZONE)


def delivered(order_date, receipt):
    return {
        "status": "delivered",
        "order_date": date.fromisoformat(order_date),
        "estimated_delivery": date.fromisoformat(receipt),
    }


def test_order_5_within_regret_window():
    # Plan scenario 6: delivered 2026-01-18, asked on 2026-01-22.
    windows = return_windows(order(5), at("2026-01-22"))
    assert windows.receipt_date == date(2026, 1, 18)
    assert windows.receipt_date_is_estimate
    assert windows.days_since_receipt == 4
    assert windows.within_regret_window
    assert windows.regret_deadline == date(2026, 1, 25)


def test_order_7_regret_closed_warranty_open():
    # Plan scenario 7: delivered 2026-02-17, asked on 2026-03-23.
    windows = return_windows(order(7), at("2026-03-23"))
    assert windows.days_since_receipt == 34
    assert not windows.within_regret_window
    assert not windows.within_defect_window  # 48 days after the 2026-02-03 purchase
    assert windows.within_legal_warranty


def test_order_5_complaint_within_defect_window():
    # Plan scenario 17: purchased 2026-01-05, complaint on 2026-01-25.
    windows = return_windows(order(5), at("2026-01-25"))
    assert windows.days_since_order == 20
    assert windows.within_defect_window
    assert windows.defect_deadline == date(2026, 2, 4)


@pytest.mark.parametrize(
    ("today", "regret", "defect", "warranty"),
    [
        ("2026-01-17", True, True, True),  # day 7 after receipt: still open
        ("2026-01-18", False, True, True),  # day 8: closed
        ("2026-01-31", False, True, True),  # day 30 after purchase: still open
        ("2026-02-01", False, False, True),  # day 31: closed
        ("2026-04-10", False, False, True),  # day 90 after receipt: still open
        ("2026-04-11", False, False, False),  # day 91: closed
    ],
)
def test_window_boundaries_are_inclusive(today, regret, defect, warranty):
    windows = return_windows(delivered("2026-01-01", "2026-01-10"), at(today))
    assert windows.within_regret_window is regret
    assert windows.within_defect_window is defect
    assert windows.within_legal_warranty is warranty


@pytest.mark.parametrize("order_id", [8, 13, 18])  # shipped, pending, cancelled
def test_undelivered_orders_have_no_open_windows(order_id):
    windows = return_windows(order(order_id), at("2026-03-23"))
    assert not windows.delivered
    assert windows.receipt_date is None
    assert not (
        windows.within_regret_window
        or windows.within_defect_window
        or windows.within_legal_warranty
    )
