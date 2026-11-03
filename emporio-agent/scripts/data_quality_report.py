"""Print every data issue that can be detected from the store's CSVs.

Run: python scripts/data_quality_report.py

Each section maps to a finding in Section 3 of the build plan; the README explains the rule
the agent follows for each one.
"""

from __future__ import annotations

import re

from emporio.data import StoreData, get_data

TRACKING_CODE = re.compile(r"BR[A-Z0-9]{9}BR")


def _title(text: str) -> None:
    print(f"\n## {text}")


def _product_line(row) -> str:
    return f"  - {row.product_id} {row.name} (status={row.status}, stock={row.stock_quantity})"


def report_stock_and_status(data: StoreData) -> None:
    products = data.products

    _title("Active products with zero stock (unavailable)")
    active_no_stock = (products["status"] == "active") & (products["stock_quantity"] == 0)
    for row in products[active_no_stock].itertuples():
        print(_product_line(row))

    _title("Products that cannot be sold (discontinued or coming soon)")
    not_for_sale = products[products["status"] != "active"]
    for row in not_for_sale.itertuples():
        print(_product_line(row))

    _title("Orders containing products that are not active")
    items = data.order_items.merge(not_for_sale[["product_id", "status"]], on="product_id")
    for row in items.itertuples():
        print(f"  - order {row.order_id}: product {row.product_id} ({row.status}) x{row.quantity}")


def report_descriptions(data: StoreData) -> None:
    _title("Unreliable descriptions (name, price, stock, status and specs are the source of truth)")
    unreliable = data.products[~data.products["description_reliable"]]
    for row in unreliable.itertuples():
        print(f"  - {row.product_id} {row.name}")
        for issue in row.description_issues:
            print(f"      {issue}")


def report_orders(data: StoreData) -> None:
    orders = data.orders

    _title("Order totals that differ from the sum of items (at current list prices)")
    for row in orders[~orders["total_matches_items"]].itertuples():
        diff = row.items_total - row.total_brl
        print(
            f"  - order {row.order_id}: total_brl={row.total_brl}, "
            f"items={row.items_total}, diff={diff}"
        )

    _title("Orders without tracking code, by status")
    no_tracking = orders[orders["tracking_code"].isna()]
    for status, group in no_tracking.groupby("status"):
        print(f"  - {status}: orders {sorted(group['order_id'].tolist())}")

    _title("Tracking codes outside the BR + 9 alphanumerics + BR format")
    bad = [c for c in orders["tracking_code"].dropna() if not TRACKING_CODE.fullmatch(c)]
    print(f"  - {bad if bad else 'none'}")

    _title("Cancelled orders and their reason")
    for row in orders[orders["status"] == "cancelled"].itertuples():
        print(f"  - order {row.order_id}: {row.notes}")


def report_catalog(data: StoreData) -> None:
    _title("Categories without products")
    used = set(data.products["category_id"])
    for row in data.categories[~data.categories["category_id"].isin(used)].itertuples():
        print(f"  - {row.category_id} {row.name}")


def report_promotions(data: StoreData) -> None:
    promotions = data.promotions

    _title("Active promotions (is_active = 1)")
    active = promotions[promotions["is_active"] == 1].merge(
        data.products[["product_id", "name", "price_brl"]], on="product_id"
    )
    for row in active.itertuples():
        print(
            f"  - promotion {row.promotion_id}: product {row.product_id} {row.name}, "
            f"{row.discount_percent}% off ({row.description}), list price {row.price_brl}"
        )

    _title("Products with more than one promotion row")
    counts = promotions.groupby("product_id")["promotion_id"].apply(list)
    for product_id, promo_ids in counts[counts.str.len() > 1].items():
        print(f"  - product {product_id}: promotions {promo_ids}")


def report_customers(data: StoreData) -> None:
    customers = data.customers

    _title("Customers sharing the same e-mail")
    dup = customers[customers["email"].duplicated(keep=False)]
    for email, group in dup.groupby("email"):
        print(f"  - {email}: customers {sorted(group['customer_id'].tolist())}")


def main() -> None:
    data = get_data()
    print("# Data quality report")
    report_stock_and_status(data)
    report_descriptions(data)
    report_orders(data)
    report_catalog(data)
    report_promotions(data)
    report_customers(data)


if __name__ == "__main__":
    main()
