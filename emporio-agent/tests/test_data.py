import runpy
from decimal import Decimal
from pathlib import Path

import pytest

from emporio.data import description_issues, get_data, normalize_text

REPORT_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "data_quality_report.py"


@pytest.fixture(scope="module")
def data():
    return get_data()


def product(data, product_id):
    return data.products.set_index("product_id").loc[product_id]


# --- Known findings from Section 3 of the plan ---


def test_active_product_with_zero_stock_is_unavailable(data):
    gf3d = product(data, 96)
    assert gf3d["status"] == "active"
    assert gf3d["stock_quantity"] == 0
    assert not gf3d["available"]


@pytest.mark.parametrize("product_id", [113, 130, 136])
def test_discontinued_and_coming_soon_are_unavailable(data, product_id):
    assert not product(data, product_id)["available"]


def test_exactly_four_active_promotions(data):
    with_promo = data.products[data.products["promotion"].notna()]
    found = {
        int(row.product_id): (row.promotion["promotion_id"], row.promotion["discount_percent"])
        for row in with_promo.itertuples()
    }
    assert found == {127: (12, 10), 121: (14, 20), 90: (19, 18), 94: (24, 8)}


def test_expired_promotion_is_ignored(data):
    # Product 91 had a 30% Black Friday promotion that is no longer active.
    assert product(data, 91)["promotion"] is None


def test_only_orders_3_and_20_have_mismatched_totals(data):
    mismatched = data.orders[~data.orders["total_matches_items"]]
    assert mismatched["order_id"].tolist() == [3, 20]
    order_3 = mismatched.iloc[0]
    assert (order_3["total_brl"], order_3["items_total"]) == (Decimal("3450"), Decimal("3498"))


def test_unreliable_descriptions(data):
    unreliable = data.products[~data.products["description_reliable"]]
    assert set(unreliable["product_id"]) == {135, 137, 139, 140, 142, 144}


def test_categories_without_products(data):
    used = set(data.products["category_id"])
    empty = set(data.categories["category_id"]) - used
    assert empty == {6, 7, 8}


# --- Types and cleaning ---


def test_money_is_decimal_parsed_from_text(data):
    assert product(data, 81)["price_brl"] == Decimal("599.9")
    assert all(isinstance(v, Decimal) for v in data.orders["total_brl"])


def test_specs_are_parsed_to_dicts(data):
    assert product(data, 142)["specs"]["keys"] == "61"


def test_missing_order_fields_are_none(data):
    pending = data.orders.set_index("order_id").loc[13]
    assert pending["tracking_code"] is None
    assert pending["estimated_delivery"] is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Yamaha F310 Aço Natural", "yamaha f310 aco natural"),
        ("  Takamine   GD20 ", "takamine gd20"),
        ("BARÍTONO Elétrico", "baritono eletrico"),
    ],
)
def test_normalize_text(raw, expected):
    assert normalize_text(raw) == expected


# --- Description heuristic edge cases ---


def test_brand_match_uses_whole_words():
    # 'Kala' must not be found inside 'Kalani'.
    assert description_issues("Kalani KAL-300S", "Ukulele Kalani soprano", {}) == []


def test_string_type_is_not_compared_as_a_count():
    # Guitars store 'nylon' in specs.strings, so '7 cordas' cannot be checked.
    assert description_issues("Rozini 7 Cordas", "Violão de 7 cordas", {"strings": "nylon"}) == []


def test_count_mismatch_is_flagged():
    issues = description_issues("Korg Synth", "Synth com 88 teclas", {"keys": "61"})
    assert issues == ["says 88 teclas, but specs say 61"]


# --- Report ---


def test_quality_report_runs_and_lists_key_findings(capsys):
    runpy.run_path(str(REPORT_SCRIPT), run_name="__main__")
    out = capsys.readouterr().out
    for expected in ["96 Giannini GF-3D", "order 3:", "order 20:", "bruno.martins@jmail.com"]:
        assert expected in out
