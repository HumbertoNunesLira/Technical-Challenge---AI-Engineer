"""The six agent tools: JSON schemas for the model and plain Python implementations.

The model decides WHICH tool to call and with which arguments. Everything that must be exact
(filtering, prices, installments, dates, identity checks) happens here, in Python.

Every result is a JSON-serializable dict of data, never prose for the model to obey.
Errors are {"error": code, "message": text}. Money is formatted as pt-BR strings
("R$ 1.299,00") and dates as dd/mm/yyyy, so the model can quote them without doing math.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz

from emporio.data import get_data, normalize_text
from emporio.money import (
    final_price,
    format_brl,
    installment_value,
    max_installments,
    pix_price,
)
from emporio.policies import TOPICS
from emporio.policies import get_policy as read_policy
from emporio.returns import return_windows
from emporio.store_hours import TIMEZONE, WEEKDAYS_PT, store_status, to_local

OUTBOX_PATH = Path(__file__).resolve().parents[2] / "data" / "outbox" / "escalations.jsonl"

MAX_VERIFICATION_FAILURES = 3
SEARCH_DEFAULT_LIMIT = 5
SEARCH_MAX_LIMIT = 10
MAX_ALTERNATIVES = 3
MAX_CANDIDATES = 5

# Fuzzy matching: a candidate needs MATCH_MIN_SCORE; if others are within MATCH_MARGIN of the
# best score, the name is ambiguous and the customer must choose.
MATCH_MIN_SCORE = 80
MATCH_MARGIN = 5
# Words that describe the kind of instrument, not the model: dropped before matching.
GENERIC_WORDS = {
    "violao", "violoes", "ukulele", "ukuleles", "guitarra", "guitarras", "baixo", "baixos",
    "contrabaixo", "teclado", "teclados", "bateria", "baterias", "o", "a", "os", "as", "de",
    "do", "da", "um", "uma", "modelo",
}

# Enum slug -> category_id in categories.csv.
CATEGORIES = {
    "guitarras": 1,
    "baixos": 2,
    "baterias_percussao": 3,
    "teclados_pianos": 4,
    "violoes": 5,
    "sopro_madeiras": 6,
    "sopro_metais": 7,
    "cordas_orquestrais": 8,
    "ukuleles": 9,
}

ESCALATION_REASONS = (
    "complaint",
    "delayed_order",
    "return_or_exchange",
    "defect",
    "product_not_in_catalog",
    "cannot_resolve",
    "other",
)

PAYMENT_METHODS = {
    "pix": "PIX",
    "boleto": "Boleto bancário",
    "debit": "Cartão de débito",
}

ORDER_STATUS_MEANING = {
    "pending": "Order received, not processed or dispatched yet. No tracking code yet.",
    "confirmed": "Order confirmed, not dispatched yet. No tracking code yet.",
    "shipped": "Dispatched and in transit. Tracking code available.",
    "delivered": "Delivered to the customer.",
    "cancelled": "Order cancelled. The reason is in notes.",
}


@dataclass
class ToolContext:
    """Per-session state the tools need: the clock, failed verifications and the outbox."""

    session_id: str = "default"
    now: datetime | None = None  # fixed clock (tests, AGENT_NOW); None means real time
    failed_verifications: int = 0
    outbox_path: Path = OUTBOX_PATH

    def current_time(self) -> datetime:
        return to_local(self.now) if self.now else datetime.now(TIMEZONE)


# --- Formatting helpers -------------------------------------------------------------------


def _date(value: date | None) -> str | None:
    return value.strftime("%d/%m/%Y") if value else None


def _datetime(value: datetime | None) -> str | None:
    return value.strftime("%d/%m/%Y %H:%M") if value else None


def _availability(product: pd.Series) -> str:
    if product["status"] == "discontinued":
        return "discontinued"
    if product["status"] == "coming_soon":
        return "coming_soon"
    return "available" if product["available"] else "out_of_stock"


def _category_name(category_id: int) -> str:
    categories = get_data().categories.set_index("category_id")
    return str(categories.loc[category_id, "name"])


def _promotion(product: pd.Series) -> dict | None:
    promo = product["promotion"]
    if not promo:
        return None
    return {"name": promo["name"], "discount_percent": promo["discount_percent"]}


def _product_summary(product: pd.Series) -> dict:
    """Compact view used in search results, candidates and alternatives."""
    return {
        "product_id": int(product["product_id"]),
        "name": product["name"],
        "category": _category_name(int(product["category_id"])),
        "list_price": format_brl(product["price_brl"]),
        "promotion": _promotion(product),
        "final_price": format_brl(final_price(product)),
        "availability": _availability(product),
        "stock": int(product["stock_quantity"]),
    }


def _product_detail(product: pd.Series) -> dict:
    """Everything needed to answer price, payment and availability questions."""
    price = final_price(product)
    max_n = max_installments(price)
    pix = pix_price(product)
    detail = _product_summary(product)
    detail.update(
        {
            "pix_price": format_brl(pix) if pix is not None else None,
            "pix_note": (
                "PIX 5% discount applies to the list price."
                if pix is not None
                else "No PIX discount: it does not stack with the active promotion."
            ),
            "max_installments": max_n,
            "installment_options": {
                str(n): format_brl(installment_value(price, n)) for n in range(1, max_n + 1)
            },
            "specs": product["specs"],
        }
    )
    if product["description_reliable"]:
        detail["description"] = product["description"]
    else:
        detail["description_note"] = (
            "Description withheld: it contradicts the product name or specs."
        )
    return detail


# --- search_products ----------------------------------------------------------------------


def _matches_query(product: pd.Series, query_tokens: list[str]) -> bool:
    category = normalize_text(_category_name(int(product["category_id"])))
    haystack = f"{product['search_name']} {category}"
    return all(token in haystack for token in query_tokens)


def search_products(
    ctx: ToolContext,
    category: str | None = None,
    string_type: str | None = None,
    electronics: bool | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    query: str | None = None,
    include_unavailable: bool = False,
    limit: int = SEARCH_DEFAULT_LIMIT,
) -> dict:
    products = get_data().products
    mask = pd.Series(True, index=products.index)

    if category is not None:
        if category not in CATEGORIES:
            return {
                "error": "unknown_category",
                "message": f"Unknown category '{category}'. Valid: {', '.join(CATEGORIES)}.",
            }
        mask &= products["category_id"] == CATEGORIES[category]
    if string_type is not None:
        mask &= products["specs"].map(lambda s: s.get("strings") == string_type)
    if electronics is not None:
        wanted = "yes" if electronics else "no"
        mask &= products["specs"].map(lambda s: s.get("electronics") == wanted)
    if query:
        tokens = normalize_text(query).split()
        mask &= products.apply(lambda p: _matches_query(p, tokens), axis=1)

    # Price filters use the final price: "até R$ 1000" means what the customer would pay.
    prices = pd.Series(
        [final_price(p) for _, p in products.iterrows()], index=products.index, dtype=object
    )
    if min_price is not None:
        mask &= prices >= Decimal(str(min_price))
    if max_price is not None:
        mask &= prices <= Decimal(str(max_price))

    matching = products[mask]
    available = matching[matching["available"]]
    shown = matching if include_unavailable else available
    order = sorted(shown.index, key=lambda i: prices[i])
    limit = max(1, min(int(limit), SEARCH_MAX_LIMIT))

    result = {
        "total_matches": len(shown),
        "unavailable_matches_hidden": 0 if include_unavailable else len(matching) - len(available),
        "items": [_product_summary(products.loc[i]) for i in order[:limit]],
        "sorted_by": "final_price ascending",
    }
    if not len(shown):
        result["note"] = (
            "Nothing in the catalog matches. Do not invent products; say so and offer "
            "human follow-up."
        )
    return result


# --- get_product --------------------------------------------------------------------------


def _match_tokens(text: str) -> list[str]:
    cleaned = re.sub(r"[^a-z0-9 ]", " ", normalize_text(text))
    return [word for word in cleaned.split() if word not in GENERIC_WORDS]


def _match_score(query_tokens: list[str], name: str) -> float:
    """Best of two views: squashed strings ('gd 20' == 'gd20') and token sets (word order)."""
    name_tokens = _match_tokens(name)
    squashed_query = "".join(query_tokens)
    partial = (
        fuzz.partial_ratio(squashed_query, "".join(name_tokens))
        if len(squashed_query) >= 3
        else 0
    )
    token_set = fuzz.token_set_ratio(" ".join(query_tokens), " ".join(name_tokens))
    return max(partial, token_set)


def _is_seven_string(product: pd.Series) -> bool:
    return "7 cordas" in product["search_name"]


# Features an alternative should share, in order of importance. Each one narrows the pool
# only if some in-stock product still matches, so there is always something to offer.
SIMILARITY_FEATURES: tuple[Callable[[pd.Series], object], ...] = (
    lambda p: p["specs"].get("strings"),  # nylon / steel / string count
    _is_seven_string,
    lambda p: p["specs"].get("electronics"),
)


def _alternatives(product: pd.Series) -> list[dict]:
    """In-stock products of the same category, as similar as possible, closest price first."""
    products = get_data().products
    pool = products[
        products["available"]
        & (products["category_id"] == product["category_id"])
        & (products["product_id"] != product["product_id"])
    ]
    for feature in SIMILARITY_FEATURES:
        wanted = feature(product)
        similar = pool[pool.apply(lambda p, f=feature, w=wanted: f(p) == w, axis=1)]
        if len(similar):
            pool = similar
    target = final_price(product)
    ranked = sorted(pool.index, key=lambda i: abs(final_price(pool.loc[i]) - target))
    return [_product_summary(pool.loc[i]) for i in ranked[:MAX_ALTERNATIVES]]


def get_product(ctx: ToolContext, name: str | None = None, product_id: int | None = None) -> dict:
    products = get_data().products.set_index("product_id", drop=False)

    if product_id is not None:
        if int(product_id) not in products.index:
            return {"error": "not_found", "message": f"No product with id {product_id}."}
        product = products.loc[int(product_id)]
    elif name:
        query_tokens = _match_tokens(name)
        if not query_tokens:
            return {"error": "not_found", "message": "Ask the customer for the product name."}
        scored = sorted(
            (
                (_match_score(query_tokens, row.name), row.product_id)
                for row in products.itertuples()
            ),
            reverse=True,
        )
        best_score = scored[0][0]
        if best_score < MATCH_MIN_SCORE:
            return {
                "error": "not_found",
                "message": f"No product in the catalog matches '{name}'. Do not invent one.",
            }
        close = [pid for score, pid in scored if score >= best_score - MATCH_MARGIN]
        if len(close) > 1:
            return {
                "match": "ambiguous",
                "message": "Several products match. Ask the customer which one they mean.",
                "candidates": [
                    _product_summary(products.loc[pid]) for pid in close[:MAX_CANDIDATES]
                ],
                "total_candidates": len(close),
            }
        product = products.loc[close[0]]
    else:
        return {"error": "missing_argument", "message": "Provide name or product_id."}

    detail = {"match": "exact" if product_id is not None else "fuzzy", **_product_detail(product)}
    if not product["available"]:
        detail["alternatives"] = _alternatives(product)
        detail["availability_note"] = {
            "discontinued": "No longer part of the catalog. Never offer it; suggest alternatives.",
            "coming_soon": "Not on sale yet. Never sell it; suggest alternatives.",
            "out_of_stock": "Temporarily unavailable. Never confirm availability.",
        }[_availability(product)]
    return detail


# --- get_order_status ---------------------------------------------------------------------


def _normalize_contact(value: str) -> str:
    """E-mail: lowercase and trimmed. Phone: digits only, without the +55 country code."""
    value = value.strip().lower()
    if "@" in value:
        return value
    digits = re.sub(r"\D", "", value)
    if digits.startswith("55") and len(digits) in (12, 13):
        digits = digits[2:]
    return digits


def _payment_method(code: str) -> str:
    if code in PAYMENT_METHODS:
        return PAYMENT_METHODS[code]
    installments = re.fullmatch(r"credit_(\d+)x", code)
    if installments:
        return f"Cartão de crédito em {installments.group(1)}x"
    return code


def _verification_failed(ctx: ToolContext) -> dict:
    ctx.failed_verifications += 1
    left = max(0, MAX_VERIFICATION_FAILURES - ctx.failed_verifications)
    return {
        "error": "verification_failed",
        "message": (
            "Could not verify this order with the details provided. Ask the customer to check "
            "the order number and the e-mail or phone used in the purchase."
        ),
        "attempts_left": left,
    }


def get_order_status(ctx: ToolContext, order_id: int, verification: str) -> dict:
    if ctx.failed_verifications >= MAX_VERIFICATION_FAILURES:
        return {
            "error": "verification_locked",
            "message": (
                "Too many failed verification attempts in this conversation. Do not try "
                "again; offer human follow-up."
            ),
        }

    data = get_data()
    orders = data.orders.set_index("order_id", drop=False)
    try:
        order_id = int(order_id)
    except (TypeError, ValueError):
        return _verification_failed(ctx)
    if order_id not in orders.index or not verification:
        return _verification_failed(ctx)

    order = orders.loc[order_id]
    customer = data.customers.set_index("customer_id").loc[int(order["customer_id"])]
    given = _normalize_contact(str(verification))
    on_file = {_normalize_contact(customer["email"]), _normalize_contact(customer["phone"])}
    if given not in on_file:
        return _verification_failed(ctx)

    now = ctx.current_time()
    products = data.products.set_index("product_id")
    items = data.order_items[data.order_items["order_id"] == order_id]
    estimated = order["estimated_delivery"]
    windows = asdict(return_windows(order, now))
    for key, value in windows.items():
        if isinstance(value, date):
            windows[key] = _date(value)

    return {
        "verified": True,
        "order_id": order_id,
        "customer_first_name": customer["name"].split()[0],
        "status": order["status"],
        "status_meaning": ORDER_STATUS_MEANING.get(order["status"], ""),
        "order_date": _date(order["order_date"]),
        "items": [
            {"product_id": int(item.product_id), "name": products.loc[item.product_id, "name"],
             "quantity": int(item.quantity)}
            for item in items.itertuples()
        ],
        "total": format_brl(order["total_brl"]),
        "payment_method": _payment_method(order["payment_method"]),
        "tracking_code": order["tracking_code"],
        "estimated_delivery": _date(estimated),
        "delivery_overdue": bool(
            order["status"] == "shipped" and estimated is not None and now.date() > estimated
        ),
        "notes": order["notes"],
        "return_windows": windows,
        "today": _date(now.date()),
    }


# --- get_policy, get_store_status ---------------------------------------------------------


def get_policy(ctx: ToolContext, topic: str) -> dict:
    return read_policy(topic)


def get_store_status(ctx: ToolContext) -> dict:
    status = store_status(ctx.current_time())
    return {
        "is_open": status.is_open,
        "now": _datetime(status.now),
        "weekday": WEEKDAYS_PT[status.now.weekday()],
        "closes_at": _datetime(status.closes_at),
        "next_opening": _datetime(status.next_opening),
        "holiday": status.holiday,
        "summary_pt": status.summary_pt,
    }


# --- escalate_to_human --------------------------------------------------------------------


def escalate_to_human(
    ctx: ToolContext,
    reason: str,
    summary: str,
    order_id: int | None = None,
    contact: str | None = None,
) -> dict:
    if reason not in ESCALATION_REASONS:
        reason = "other"
    now = ctx.current_time()
    protocol = f"EMP-{now:%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"
    record = {
        "protocol": protocol,
        "created_at": now.isoformat(),
        "session_id": ctx.session_id,
        "reason": reason,
        "summary": summary,
        "order_id": order_id,
        "contact": contact,
    }
    ctx.outbox_path.parent.mkdir(parents=True, exist_ok=True)
    with ctx.outbox_path.open("a", encoding="utf-8") as outbox:
        outbox.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {
        "protocol": protocol,
        "response_time": "até 24 horas úteis",
        "message": "Registered for the team. Give the customer the protocol and response time.",
    }


# --- Registry -----------------------------------------------------------------------------

TOOL_FUNCTIONS: dict[str, Callable[..., dict]] = {
    "search_products": search_products,
    "get_product": get_product,
    "get_order_status": get_order_status,
    "get_policy": get_policy,
    "get_store_status": get_store_status,
    "escalate_to_human": escalate_to_human,
}

TOOL_SCHEMAS: list[dict] = [
    {
        "name": "search_products",
        "description": (
            "Browse the catalog when the customer wants options: by category, string type, "
            "electronics, price range or free text (e.g. 'violões até R$ 1000', 'ukulele "
            "elétrico', 'tem saxofone?'). Returns only available products by default, "
            "cheapest first, plus the total match count. Do NOT use it for one specific "
            "product the customer names (use get_product) or for policy questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": list(CATEGORIES)},
                "string_type": {
                    "type": "string",
                    "enum": ["nylon", "steel"],
                    "description": "Guitars (violões) and ukuleles only. 'steel' = aço.",
                },
                "electronics": {
                    "type": "boolean",
                    "description": "True for electro-acoustic (elétrico) guitars/ukuleles.",
                },
                "min_price": {"type": "number", "description": "In BRL, final price."},
                "max_price": {"type": "number", "description": "In BRL, final price."},
                "query": {
                    "type": "string",
                    "description": "Words that must appear in the product name or category, "
                    "e.g. 'dreadnought', '7 cordas', 'yamaha', 'saxofone'.",
                },
                "include_unavailable": {
                    "type": "boolean",
                    "description": "Also list out-of-stock, discontinued and coming-soon "
                    "items. Default false.",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": SEARCH_MAX_LIMIT},
            },
        },
    },
    {
        "name": "get_product",
        "description": (
            "Look up ONE specific product the customer names: price, active promotion, final "
            "price, PIX price, installment options, stock and availability, with in-stock "
            "alternatives when it is unavailable. Accepts loose names ('gd20', 'takamine gd "
            "20'). If the result is 'ambiguous', ask the customer which candidate they mean. "
            "Do NOT use it to browse a category (use search_products)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Product name as the customer said it."},
                "product_id": {
                    "type": "integer",
                    "description": "Only when known from a previous tool result.",
                },
            },
        },
    },
    {
        "name": "get_order_status",
        "description": (
            "Get an order's status, items, total, payment method, tracking code, estimated "
            "delivery, notes and which return/warranty windows still apply. Requires the order "
            "number AND the e-mail or phone used in the purchase, both given by the customer "
            "in this conversation: ask for them first, never guess. A failed check returns a "
            "generic error; after 3 failures lookups are locked. Do NOT use it for general "
            "questions about returns or shipping (use get_policy)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "integer"},
                "verification": {
                    "type": "string",
                    "description": "The customer's e-mail or phone, exactly as they typed it.",
                },
            },
            "required": ["order_id", "verification"],
        },
    },
    {
        "name": "get_policy",
        "description": (
            "Read the store's rules for one topic: store_info (address, contacts, what the "
            "store sells), hours, payment (methods, installment rules), returns (regret, "
            "defects, exchanges), shipping, promotions (rules), warranty, privacy, "
            "scope_and_tone (service guidelines). Use it before stating any rule. Do NOT use "
            "it for a product's price or stock (use get_product) or for whether the store is "
            "open right now (use get_store_status)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"topic": {"type": "string", "enum": list(TOPICS)}},
            "required": ["topic"],
        },
    },
    {
        "name": "get_store_status",
        "description": (
            "Whether the store is open right now and when it next opens, using the store's "
            "time zone and Brazilian/MS holidays. Use it for 'vocês estão abertos?' or 'que "
            "horas abre amanhã?'. For the general weekly schedule, get_policy('hours') is "
            "enough."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "escalate_to_human",
        "description": (
            "Hand the case to the human team and get a protocol number. Use it for complaints, "
            "delayed orders, defects, borderline return cases, items not in the catalog the "
            "customer wants followed up, or anything you cannot resolve. Tell the customer the "
            "protocol and that the team replies within 24 business hours. Do NOT use it for "
            "questions a tool can answer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "enum": list(ESCALATION_REASONS)},
                "summary": {
                    "type": "string",
                    "description": "One or two sentences for the team: what happened and "
                    "what the customer wants.",
                },
                "order_id": {"type": "integer"},
                "contact": {
                    "type": "string",
                    "description": "How to reach the customer, if they gave it.",
                },
            },
            "required": ["reason", "summary"],
        },
    },
]


def run_tool(name: str, arguments: dict, ctx: ToolContext) -> dict:
    """Execute a tool call from the model. Never raises: failures come back as error dicts."""
    function = TOOL_FUNCTIONS.get(name)
    if function is None:
        return {"error": "unknown_tool", "message": f"No tool named '{name}'."}
    try:
        return function(ctx, **(arguments or {}))
    except TypeError as exc:
        return {"error": "invalid_arguments", "message": str(exc)}
    except Exception as exc:  # noqa: BLE001 - the model gets the error as data, not a crash
        return {"error": "tool_failed", "message": f"{type(exc).__name__}: {exc}"}
