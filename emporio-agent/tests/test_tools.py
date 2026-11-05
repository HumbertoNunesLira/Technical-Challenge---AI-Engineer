import json
from datetime import datetime

import pytest

from emporio.data import get_data, normalize_text
from emporio.money import final_price
from emporio.policies import TOPICS
from emporio.store_hours import TIMEZONE
from emporio.tools import (
    CATEGORIES,
    TOOL_FUNCTIONS,
    TOOL_SCHEMAS,
    ToolContext,
    escalate_to_human,
    get_order_status,
    get_product,
    get_store_status,
    run_tool,
    search_products,
)

ORDER_5_EMAIL = "rafael.pereira@jmail.com"
ORDER_5_PHONE = "67987654321"


@pytest.fixture
def ctx(tmp_path):
    return ToolContext(
        session_id="test",
        now=datetime(2026, 3, 23, 10, 0, tzinfo=TIMEZONE),
        outbox_path=tmp_path / "escalations.jsonl",
    )


def product_row(product_id):
    return get_data().products.set_index("product_id").loc[product_id]


# --- search_products ----------------------------------------------------------------------


def test_violoes_up_to_1000_only_available(ctx):
    result = search_products(ctx, category="violoes", max_price=1000, limit=10)
    ids = [item["product_id"] for item in result["items"]]
    assert result["total_matches"] == 12
    assert len(ids) == 10  # capped by limit
    assert 96 not in ids and 113 not in ids
    for product_id in ids:
        row = product_row(product_id)
        assert row["available"]
        assert final_price(row) <= 1000
    assert result["unavailable_matches_hidden"] == 2  # 96 (no stock) and 113 (discontinued)


def test_search_is_sorted_by_final_price_and_limited(ctx):
    result = search_products(ctx, category="ukuleles")
    assert len(result["items"]) == 5
    prices = [final_price(product_row(i["product_id"])) for i in result["items"]]
    assert prices == sorted(prices)


def test_search_filters_string_type_and_electronics(ctx):
    result = search_products(ctx, category="violoes", string_type="nylon", electronics=True)
    for item in result["items"]:
        specs = product_row(item["product_id"])["specs"]
        assert specs["strings"] == "nylon" and specs["electronics"] == "yes"


def test_search_empty_category_says_so(ctx):
    result = search_products(ctx, query="saxofone")
    assert result["total_matches"] == 0
    assert "Do not invent" in result["note"]


def test_search_rejects_unknown_category(ctx):
    assert search_products(ctx, category="pianos")["error"] == "unknown_category"


def test_category_enum_matches_categories_csv():
    categories = get_data().categories.set_index("category_id")["name"]
    for slug, category_id in CATEGORIES.items():
        assert slug.split("_")[0] in normalize_text(categories[category_id])


# --- get_product --------------------------------------------------------------------------


def test_takamine_gd20(ctx):
    result = get_product(ctx, name="Takamine GD20")
    assert result["product_id"] == 95
    assert result["list_price"] == "R$ 2.199,00"
    assert result["pix_price"] == "R$ 2.089,05"
    assert result["promotion"] is None
    assert result["stock"] == 5
    assert result["max_installments"] == 12
    assert result["installment_options"]["12"] == "R$ 183,25"


@pytest.mark.parametrize("loose_name", ["gd20", "takamine gd 20", "TAKAMINE  GD-20"])
def test_loose_names_match(ctx, loose_name):
    assert get_product(ctx, name=loose_name)["product_id"] == 95


def test_ohana_ck20_promotion_without_pix(ctx):
    result = get_product(ctx, name="Ohana CK-20")
    assert result["promotion"] == {"name": "Desconto Exclusivo", "discount_percent": 20}
    assert result["list_price"] == "R$ 549,00"
    assert result["final_price"] == "R$ 439,20"
    assert result["pix_price"] is None


@pytest.mark.parametrize("vague_name", ["Kala", "Yamaha", "Martin D-28"])
def test_vague_name_returns_candidates(ctx, vague_name):
    result = get_product(ctx, name=vague_name)
    assert result["match"] == "ambiguous"
    assert len(result["candidates"]) >= 2


def test_out_of_stock_returns_available_alternatives(ctx):
    result = get_product(ctx, name="Giannini GF-3D")
    assert result["availability"] == "out_of_stock"
    assert 1 <= len(result["alternatives"]) <= 3
    assert all(alt["availability"] == "available" for alt in result["alternatives"])


def test_discontinued_seven_string_suggests_seven_strings(ctx):
    result = get_product(ctx, name="Shelby SN-7C 7 cordas")
    assert result["availability"] == "discontinued"
    names = {alt["name"] for alt in result["alternatives"]}
    assert names == {
        "Giannini GWNE-7 7 Cordas Nylon Natural",
        "Rozini RV-174 7 Cordas Nylon Natural",
    }


def test_coming_soon_is_not_available(ctx):
    assert get_product(ctx, product_id=130)["availability"] == "coming_soon"


def test_unreliable_description_is_withheld(ctx):
    result = get_product(ctx, product_id=135)  # description talks about a Fender Jazz Bass
    assert "description" not in result
    assert "withheld" in result["description_note"]


def test_unknown_product_is_not_invented(ctx):
    assert get_product(ctx, name="saxofone alto")["error"] == "not_found"


# --- get_order_status ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "verification",
    [
        ORDER_5_EMAIL,
        " Rafael.Pereira@JMAIL.com ",
        ORDER_5_PHONE,
        "(67) 98765-4321",
        "+55 67 98765-4321",
    ],
)
def test_order_5_verifies_with_email_or_phone(ctx, verification):
    result = get_order_status(ctx, 5, verification)
    assert result["verified"] is True
    assert result["status"] == "delivered"
    assert result["customer_first_name"] == "Rafael"


def test_wrong_email_and_missing_order_get_the_same_error(ctx):
    wrong_email = get_order_status(ctx, 5, "someone@else.com")
    missing_order = get_order_status(ctx, 999, ORDER_5_EMAIL)
    assert wrong_email["error"] == missing_order["error"] == "verification_failed"
    assert wrong_email["message"] == missing_order["message"]
    assert "status" not in wrong_email and "total" not in wrong_email


def test_three_failures_lock_the_session(ctx):
    for _ in range(3):
        assert get_order_status(ctx, 5, "wrong@mail.com")["error"] == "verification_failed"
    # Even the right credentials are refused now.
    assert get_order_status(ctx, 5, ORDER_5_EMAIL)["error"] == "verification_locked"


def test_order_result_has_no_customer_contact_data(ctx):
    text = json.dumps(get_order_status(ctx, 5, ORDER_5_EMAIL), ensure_ascii=False)
    assert ORDER_5_EMAIL not in text
    assert "98765-4321" not in text
    assert "Rafael Augusto Pereira" not in text


def test_shipped_order_past_estimate_is_overdue(ctx):
    result = get_order_status(ctx, 8, "anacarol.ferreira@coldmail.com")
    assert result["tracking_code"] == "BRJL5544332BR"
    assert result["estimated_delivery"] == "28/02/2026"
    assert result["delivery_overdue"] is True


def test_pending_order_has_no_tracking(ctx):
    result = get_order_status(ctx, 13, "fernanda.ribeiro@coldmail.com")
    assert result["status"] == "pending"
    assert result["tracking_code"] is None
    assert result["delivery_overdue"] is False


def test_cancelled_order_shares_the_reason(ctx):
    result = get_order_status(ctx, 18, "gustavo.nunes@inlook.com")
    assert result["notes"] == "Item fora de estoque no momento do pedido"


def test_order_includes_return_windows(ctx):
    ctx.now = datetime(2026, 1, 22, 10, 0, tzinfo=TIMEZONE)
    windows = get_order_status(ctx, 5, ORDER_5_EMAIL)["return_windows"]
    assert windows["days_since_receipt"] == 4
    assert windows["within_regret_window"] is True
    assert windows["regret_deadline"] == "25/01/2026"


# --- get_store_status, escalate_to_human --------------------------------------------------


def test_store_status_on_sunday(ctx):
    ctx.now = datetime(2026, 3, 22, 15, 0, tzinfo=TIMEZONE)
    result = get_store_status(ctx)
    assert result["is_open"] is False
    assert result["next_opening"] == "23/03/2026 09:00"


def test_escalation_appends_exactly_one_line(ctx):
    result = escalate_to_human(ctx, reason="delayed_order", summary="Pedido 8 atrasado", order_id=8)
    lines = ctx.outbox_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["protocol"] == result["protocol"]
    assert record["order_id"] == 8 and record["session_id"] == "test"
    assert result["protocol"].startswith("EMP-20260323-")
    assert "24 horas úteis" in result["response_time"]


# --- Registry, schemas and the dispatcher -------------------------------------------------


def test_every_schema_has_an_implementation():
    assert [s["name"] for s in TOOL_SCHEMAS] == list(TOOL_FUNCTIONS)
    for schema in TOOL_SCHEMAS:
        assert len(schema["description"]) > 80
        assert schema["input_schema"]["type"] == "object"


def test_policy_topic_enum_matches_policies():
    schema = next(s for s in TOOL_SCHEMAS if s["name"] == "get_policy")
    assert schema["input_schema"]["properties"]["topic"]["enum"] == list(TOPICS)


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("search_products", {"category": "violoes", "max_price": 1000}),
        ("get_product", {"name": "Takamine GD20"}),
        ("get_product", {"name": "Kala"}),
        ("get_order_status", {"order_id": 5, "verification": ORDER_5_EMAIL}),
        ("get_policy", {"topic": "returns"}),
        ("get_store_status", {}),
        ("escalate_to_human", {"reason": "complaint", "summary": "x"}),
    ],
)
def test_run_tool_results_are_json_serializable(ctx, name, arguments):
    json.dumps(run_tool(name, arguments, ctx))


def test_run_tool_turns_failures_into_errors(ctx):
    assert run_tool("drop_tables", {}, ctx)["error"] == "unknown_tool"
    assert run_tool("get_policy", {"wrong_arg": 1}, ctx)["error"] == "invalid_arguments"
    assert run_tool("get_product", {"product_id": "abc"}, ctx)["error"] == "tool_failed"
