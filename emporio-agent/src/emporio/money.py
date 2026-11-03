"""Money rules: BRL formatting, promotions, PIX discount and installments.

Every amount the agent states comes from these functions, never from the model.
All math uses Decimal and rounds to cents with ROUND_HALF_UP.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal

CENTS = Decimal("0.01")
PIX_DISCOUNT_PERCENT = Decimal(5)
MAX_INSTALLMENTS = 12

# (max installments in the tier, minimum installment value), from section 3.1 of the manual.
# Section 3.1 prevails over the summary table; "R$50 up to 3x" is our reading of
# "sem valor mínimo de parcela (exceto abaixo de R$ 50,00)".
INSTALLMENT_TIERS = (
    (3, Decimal(50)),
    (6, Decimal(80)),
    (12, Decimal(100)),
)


def to_cents(amount: Decimal) -> Decimal:
    return amount.quantize(CENTS, rounding=ROUND_HALF_UP)


def format_brl(amount: Decimal) -> str:
    """Decimal('1299') -> 'R$ 1.299,00'."""
    us_style = f"{to_cents(amount):,.2f}"  # '1,299.00'
    return "R$ " + us_style.replace(",", "_").replace(".", ",").replace("_", ".")


def _apply_discount(amount: Decimal, percent: Decimal | int) -> Decimal:
    return to_cents(amount * (Decimal(100) - Decimal(percent)) / Decimal(100))


def final_price(product: Mapping) -> Decimal:
    """List price with the active promotion applied, if any."""
    promotion = product["promotion"]
    if promotion:
        return _apply_discount(product["price_brl"], promotion["discount_percent"])
    return to_cents(product["price_brl"])


def pix_price(product: Mapping) -> Decimal | None:
    """5% off the list price. None for promoted products: discounts do not stack."""
    if product["promotion"]:
        return None
    return _apply_discount(product["price_brl"], PIX_DISCOUNT_PERCENT)


def _minimum_installment(n: int) -> Decimal:
    for tier_max, minimum in INSTALLMENT_TIERS:
        if n <= tier_max:
            return minimum
    raise ValueError(f"at most {MAX_INSTALLMENTS} installments")


def max_installments(amount: Decimal) -> int:
    """Largest n (1 to 12) whose installment respects the minimum of its tier.

    Paying in one installment is always allowed, even below R$ 50.
    """
    for n in range(MAX_INSTALLMENTS, 1, -1):
        if amount / n >= _minimum_installment(n):
            return n
    return 1


def installment_value(amount: Decimal, n: int) -> Decimal:
    """Value of each of n interest-free installments."""
    allowed = max_installments(amount)
    if not 1 <= n <= allowed:
        raise ValueError(f"{format_brl(amount)} allows 1 to {allowed} installments, not {n}")
    return to_cents(amount / n)
