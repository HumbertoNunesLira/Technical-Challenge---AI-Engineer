"""Load the store's CSV files into clean, typed tables.

Every rule about what the raw data means lives here, so the tools never touch the CSVs:
- money is Decimal, parsed straight from the CSV text (never through float);
- a product is available only when it is active AND has stock;
- only promotions with is_active = 1 count;
- descriptions that contradict the product name or specs are flagged as unreliable.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import pandas as pd

RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"
FILE_PREFIX = "desafio_tecnico_ai_eng - "

# Brands that appear in the catalog. A description that names one of them while the
# product name does not is describing a different instrument.
KNOWN_BRANDS = (
    "Yamaha", "Fender", "Gibson", "Ibanez", "Music Man", "Roland", "Korg", "Nord",
    "Pearl", "Tama", "Taylor", "Martin", "Takamine", "Crafter", "Tagima", "Giannini",
    "Rozini", "Shelby", "Kala", "Ohana", "Kalani", "PRS",
)

# Numeric counts a description may state, and the specs key that holds the true value.
COUNTED_SPECS = (("cordas", "strings"), ("teclas", "keys"))


@dataclass(frozen=True)
class StoreData:
    """All store tables. Treat the DataFrames as read-only: they are shared via a cache."""

    products: pd.DataFrame
    categories: pd.DataFrame
    customers: pd.DataFrame
    orders: pd.DataFrame
    order_items: pd.DataFrame
    promotions: pd.DataFrame


def normalize_text(text: str) -> str:
    """Lowercase, strip accents and collapse whitespace: 'Aço  Elétrico' -> 'aco eletrico'."""
    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(without_accents.lower().split())


def _mentions(term: str, text: str) -> bool:
    """Whole-word match, so 'kala' does not match inside 'kalani'. Both args normalized."""
    return re.search(rf"\b{re.escape(term)}\b", text) is not None


def description_issues(name: str, description: str, specs: dict) -> list[str]:
    """Reasons why a description cannot be trusted; an empty list means it looks consistent."""
    name_n = normalize_text(name)
    desc_n = normalize_text(description)
    issues = []

    for brand in KNOWN_BRANDS:
        brand_n = normalize_text(brand)
        if _mentions(brand_n, desc_n) and not _mentions(brand_n, name_n):
            issues.append(f"mentions brand '{brand}', which is not in the product name")

    for unit, spec_key in COUNTED_SPECS:
        spec_value = str(specs.get(spec_key, ""))
        if not spec_value.isdigit():
            # Guitars store the string type (nylon/steel) here, not a count: nothing to compare.
            continue
        for stated in re.findall(rf"\b(\d+)\s*{unit}\b", desc_n):
            if stated != spec_value:
                issues.append(f"says {stated} {unit}, but specs say {spec_value}")

    return issues


def _optional_text(series: pd.Series) -> list[str | None]:
    """Empty CSV cells become None instead of NaN."""
    return [value if isinstance(value, str) and value.strip() else None for value in series]


def _optional_date(series: pd.Series) -> list[date | None]:
    return [date.fromisoformat(v) if v else None for v in _optional_text(series)]


def _read_csv(raw_dir: Path, table: str, **kwargs) -> pd.DataFrame:
    return pd.read_csv(raw_dir / f"{FILE_PREFIX}{table}.csv", encoding="utf-8", **kwargs)


def _active_promotions_by_product(promotions: pd.DataFrame) -> dict[int, dict]:
    """One active promotion per product. If several were active, the biggest discount wins."""
    active = promotions[promotions["is_active"] == 1].sort_values(
        "discount_percent", ascending=False
    )
    result: dict[int, dict] = {}
    for row in active.itertuples():
        result.setdefault(
            int(row.product_id),
            {
                "promotion_id": int(row.promotion_id),
                "discount_percent": int(row.discount_percent),
                "name": row.description,
            },
        )
    return result


def _build_products(raw_dir: Path, promotions: pd.DataFrame) -> pd.DataFrame:
    products = _read_csv(raw_dir, "products", converters={"price_brl": Decimal})
    products["specs"] = [json.loads(s) for s in products["specs"]]
    products["created_at"] = _optional_date(products["created_at"])
    products["search_name"] = [normalize_text(n) for n in products["name"]]
    products["available"] = (products["status"] == "active") & (products["stock_quantity"] > 0)

    active_promos = _active_promotions_by_product(promotions)
    products["promotion"] = pd.Series(
        [active_promos.get(int(pid)) for pid in products["product_id"]],
        index=products.index,
        dtype=object,
    )

    issues = [
        description_issues(row.name, row.description, row.specs)
        for row in products.itertuples()
    ]
    products["description_issues"] = pd.Series(issues, index=products.index, dtype=object)
    products["description_reliable"] = [not found for found in issues]

    return products.sort_values("product_id").reset_index(drop=True)


def _build_orders(
    raw_dir: Path, order_items: pd.DataFrame, products: pd.DataFrame
) -> pd.DataFrame:
    orders = _read_csv(raw_dir, "orders", converters={"total_brl": Decimal})
    orders["order_date"] = _optional_date(orders["order_date"])
    orders["estimated_delivery"] = _optional_date(orders["estimated_delivery"])
    orders["tracking_code"] = pd.Series(_optional_text(orders["tracking_code"]), dtype=object)
    orders["notes"] = pd.Series(_optional_text(orders["notes"]), dtype=object)

    # Sum of items at the current list price: the data has no price history.
    price_by_id = dict(zip(products["product_id"], products["price_brl"], strict=True))
    items_total: dict[int, Decimal] = {}
    for item in order_items.itertuples():
        line_total = price_by_id[item.product_id] * item.quantity
        items_total[item.order_id] = items_total.get(item.order_id, Decimal(0)) + line_total

    orders["items_total"] = pd.Series(
        [items_total.get(oid) for oid in orders["order_id"]], dtype=object
    )
    orders["total_matches_items"] = [
        recorded == computed
        for recorded, computed in zip(orders["total_brl"], orders["items_total"], strict=True)
    ]
    return orders


def load_data(raw_dir: Path = RAW_DIR) -> StoreData:
    """Read and clean all six CSVs. Prefer get_data(), which caches the result."""
    promotions = _read_csv(raw_dir, "promotions")
    products = _build_products(raw_dir, promotions)
    order_items = _read_csv(raw_dir, "order_items")
    return StoreData(
        products=products,
        categories=_read_csv(raw_dir, "categories"),
        customers=_read_csv(raw_dir, "customers"),
        orders=_build_orders(raw_dir, order_items, products),
        order_items=order_items,
        promotions=promotions,
    )


@lru_cache(maxsize=1)
def get_data() -> StoreData:
    """Load the data once per process; the CSVs are small and never change at runtime."""
    return load_data()
