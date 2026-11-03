"""Return and warranty windows for an order, from section 4 and 8 of the manual.

- Regret (arrependimento): up to 7 calendar days after receipt, no reason needed.
- Defect exchange: up to 30 calendar days after the purchase.
- Legal warranty: 90 days after receipt.

The data has no real delivery date, so the receipt date is the estimated delivery date of
a delivered order. The result says so, and the agent must tell the customer.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from emporio.store_hours import to_local

REGRET_DAYS = 7
DEFECT_DAYS = 30
LEGAL_WARRANTY_DAYS = 90


@dataclass(frozen=True)
class ReturnWindows:
    delivered: bool
    receipt_date: date | None
    receipt_date_is_estimate: bool
    days_since_receipt: int | None
    days_since_order: int
    regret_deadline: date | None
    within_regret_window: bool
    defect_deadline: date | None
    within_defect_window: bool
    legal_warranty_deadline: date | None
    within_legal_warranty: bool
    note: str


def return_windows(order: Mapping, now: datetime) -> ReturnWindows:
    """Which windows still apply to the order on the store's local date of `now`.

    Deadlines are inclusive: on the 7th day after receipt the regret window is still open.
    """
    today = to_local(now).date()
    order_date: date = order["order_date"]
    days_since_order = (today - order_date).days

    receipt: date | None = order["estimated_delivery"]
    if order["status"] != "delivered" or receipt is None:
        return ReturnWindows(
            delivered=False,
            receipt_date=None,
            receipt_date_is_estimate=False,
            days_since_receipt=None,
            days_since_order=days_since_order,
            regret_deadline=None,
            within_regret_window=False,
            defect_deadline=None,
            within_defect_window=False,
            legal_warranty_deadline=None,
            within_legal_warranty=False,
            note="Not delivered yet: return and warranty windows start at receipt.",
        )

    regret_deadline = receipt + timedelta(days=REGRET_DAYS)
    defect_deadline = order_date + timedelta(days=DEFECT_DAYS)
    warranty_deadline = receipt + timedelta(days=LEGAL_WARRANTY_DAYS)
    return ReturnWindows(
        delivered=True,
        receipt_date=receipt,
        receipt_date_is_estimate=True,
        days_since_receipt=(today - receipt).days,
        days_since_order=days_since_order,
        regret_deadline=regret_deadline,
        within_regret_window=today <= regret_deadline,
        defect_deadline=defect_deadline,
        within_defect_window=today <= defect_deadline,
        legal_warranty_deadline=warranty_deadline,
        within_legal_warranty=today <= warranty_deadline,
        note=(
            "Receipt date is the estimated delivery date; the data has no actual delivery "
            "date. Tell the customer, and offer human follow-up in borderline cases."
        ),
    )
