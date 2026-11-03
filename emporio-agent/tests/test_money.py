from decimal import Decimal

import pytest

from emporio.data import get_data
from emporio.money import (
    final_price,
    format_brl,
    installment_value,
    max_installments,
    pix_price,
)


def product(product_id):
    return get_data().products.set_index("product_id").loc[product_id]


def fake_product(price, discount=None):
    promotion = {"promotion_id": 1, "discount_percent": discount, "name": "x"} if discount else None
    return {"price_brl": Decimal(price), "promotion": promotion}


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        ("1299", "R$ 1.299,00"),
        ("2089.05", "R$ 2.089,05"),
        ("12000", "R$ 12.000,00"),
        ("1234567.891", "R$ 1.234.567,89"),
        ("599.9", "R$ 599,90"),
        ("0", "R$ 0,00"),
        ("0.005", "R$ 0,01"),  # half up
    ],
)
def test_format_brl(amount, expected):
    assert format_brl(Decimal(amount)) == expected


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        ("299", 3),  # values from the plan
        ("699", 6),
        ("700", 7),
        ("1199", 11),
        ("1200", 12),
        ("49.99", 1),  # below R$ 50: single payment only
        ("50", 1),
        ("100", 2),
        ("150", 3),
        ("240", 3),  # 4x would be R$ 60, below the R$ 80 minimum
        ("320", 4),
        ("480", 6),
        ("100000", 12),  # never above 12x
    ],
)
def test_max_installments(amount, expected):
    assert max_installments(Decimal(amount)) == expected


@pytest.mark.parametrize(
    ("amount", "n", "expected"),
    [
        ("2199", 12, "183.25"),
        ("7500", 12, "625.00"),
        ("1000", 3, "333.33"),
        ("549", 1, "549.00"),
    ],
)
def test_installment_value(amount, n, expected):
    assert installment_value(Decimal(amount), n) == Decimal(expected)


@pytest.mark.parametrize(("amount", "n"), [("699", 7), ("1000", 0), ("100000", 13)])
def test_installment_value_rejects_disallowed_counts(amount, n):
    with pytest.raises(ValueError):
        installment_value(Decimal(amount), n)


def test_promotion_applies_and_blocks_pix():
    promoted = fake_product("549", discount=20)
    assert final_price(promoted) == Decimal("439.20")
    assert pix_price(promoted) is None


def test_pix_is_five_percent_off_list_price():
    regular = fake_product("2199")
    assert final_price(regular) == Decimal("2199.00")
    assert pix_price(regular) == Decimal("2089.05")


# --- Real catalog products used in the scenarios ---


def test_takamine_gd20():
    gd20 = product(95)
    assert final_price(gd20) == Decimal("2199.00")
    assert pix_price(gd20) == Decimal("2089.05")
    assert max_installments(final_price(gd20)) == 12
    assert installment_value(final_price(gd20), 12) == Decimal("183.25")


def test_ohana_ck20_promotion():
    ck20 = product(121)
    assert final_price(ck20) == Decimal("439.20")
    assert pix_price(ck20) is None


def test_ibanez_rg550_installments_and_pix():
    rg550 = product(132)
    assert installment_value(final_price(rg550), 12) == Decimal("625.00")
    assert pix_price(rg550) == Decimal("7125.00")


def test_expired_promotion_gives_list_price():
    woodstock = product(91)  # Black Friday 30% is inactive
    assert final_price(woodstock) == Decimal("899.00")
    assert pix_price(woodstock) == Decimal("854.05")
