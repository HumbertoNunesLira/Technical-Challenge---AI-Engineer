# Empório da Música Support Agent

A Python text-message agent that answers customers of Empório da Música, a fictional musical
instruments store in Campo Grande/MS, using the store's data (CSVs) and its policy manual.

> Work in progress. This README grows with each step; the full version (quick start,
> technical decisions, limitations and AI assistant usage) comes in the final step.

## Progress

- [x] Project skeleton and raw data
- [x] Data loading and data quality report
- [x] Business rules: pricing, PIX, installments, store hours, return windows
- [x] Policy sections and policy lookup
- [x] Agent tools with order verification
- [ ] System prompt, LLM adapter and agent loop
- [ ] CLI chat with persistent sessions
- [ ] Scenario suite
- [ ] Example conversations
- [ ] (Optional) FastAPI endpoint
- [ ] (Optional) Model comparison
- [ ] Final README

## Commands

Run from the `emporio-agent/` folder, with Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows (on macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt   # also installs the project in editable mode

pytest -q                                  # unit tests
ruff check .                               # lint
python scripts/data_quality_report.py      # every data issue detected in the CSVs
```

## Assumptions and data issues (draft)

The source files in `data/raw/` are kept exactly as provided. The rules below are applied in
code (`src/emporio/data.py`) and every detectable issue is printed by the data quality report.

| Finding in the data | What the agent does |
|---|---|
| Product 96 (Giannini GF-3D) is `active` with stock 0 | A product is **available** only when `status == "active"` and stock > 0. |
| Products 113 and 136 are `discontinued`; 130 is `coming_soon` | Never offered for sale. |
| Product 130 (`coming_soon`) appears in orders 3 and 14 | Assumed to be a pre-sale; those orders stay valid and are reported as recorded. |
| Six descriptions (products 135, 137, 139, 140, 142, 144) name another brand, or a string or key count that contradicts the specs | Flagged as unreliable. Name, price, stock, status, category and specs are the source of truth; the description is never relayed. |
| `specs` is a JSON string whose keys differ per category | Parsed into a dict at load time; missing keys are tolerated. |
| Only 4 of 25 promotions are active (products 127, 121, 90, 94) | Only rows with `is_active = 1` count. If a product ever had several active promotions, the biggest discount would win (not the case today). |
| Products 93, 96 and 99 have two promotion rows each, all inactive | Ignored, since none is active. |
| Order totals differ from the sum of items in order 3 (3,450 vs 3,498) and order 20 (1,400 vs 1,488) | The recorded `total_brl` is always reported, never recomputed. The item sum uses current list prices because the data has no price history; 18 of 20 orders match, so prices look unchanged. |
| Tracking codes exist only for shipped and delivered orders | Missing values are `None`; all existing codes follow the `BR` + 9 alphanumerics + `BR` format. |
| Categories 6, 7 and 8 (woodwinds, brass, orchestral strings) have no products | The agent will say the item is not in the catalog right now. |
| Customers 11 and 43 share the e-mail `bruno.martins@jmail.com` | Harmless: order verification compares against the customer of that specific order. |
| Prices are stored as floats in the CSVs | Read straight from the CSV text into `Decimal`, never through `float`. |

Known limit of the description check: it compares brands and string/key counts only. For
example, products 139 and 140 also name a different shell wood than their specs; they are
already flagged by brand, but a wood-only contradiction would not be caught.

### Business rules

Implemented as pure, table-tested functions in `money.py`, `store_hours.py` and `returns.py`.
The model never computes a number the customer sees.

| Rule or gap in the manual | Interpretation in code |
|---|---|
| Installments: the payment table says "up to 12x, minimum R$ 100", while section 3.1 has tiers | Section 3.1 prevails because it is more specific: minimum installment R$ 50 up to 3x, R$ 80 from 4x to 6x, R$ 100 from 7x to 12x. |
| Section 3.1: "no minimum installment (except below R$ 50)" for up to 3x | Read as a R$ 50 minimum installment up to 3x. A purchase below R$ 50 is paid in a single installment. |
| Installments on promoted items | Computed on the final (promotional) price. |
| PIX 5% discount does not stack with promotions | PIX price = 5% off the list price, only for products without an active promotion. |
| Rounding | All amounts are rounded to cents with ROUND_HALF_UP. |
| Store closed on "Domingo e Feriados", but the manual has no holiday list | National public holidays plus Mato Grosso do Sul state holidays from the `holidays` package (`Brazil(subdiv="MS")`). Optional days (Carnival, Corpus Christi) and Campo Grande municipal holidays are not covered. Extended hours on special dates are not modeled. |
| Opening hours boundaries | Open from the opening time up to, but excluding, the closing time: at 18:00 sharp on a weekday the store is closed. Times use the America/Campo_Grande time zone (UTC-4). |
| No real delivery date in the data | For delivered orders, the receipt date is `estimated_delivery`; the agent tells the customer it is an estimate. |
| Regret window, "7 days after receipt" | Counted in calendar days and inclusive: on day 7 the window is still open. Applies to every order, since all orders are deliveries (online purchases). |
| Defect window, "30 days after the purchase" | Counted from `order_date`, inclusive. |
| Legal warranty, "90 days from receipt" | Counted from the estimated receipt date, inclusive. |
| Orders not delivered yet (pending, confirmed, shipped, cancelled) | No window is open; windows start at receipt. |

### Policies

The policy PDF stays untouched in `data/raw/`. `data/policies/` holds a faithful markdown copy
split by topic, and `get_policy(topic)` returns one topic at a time, so the model reads only
the section a question needs. Wording is kept as in the manual; only formatting changed, and
section numbers were dropped from headings so the agent does not cite "section 3" to customers.

| Topic | Manual sections |
|---|---|
| `store_info` | Cover (slogan, address, contacts) and 1. About, mission, company data |
| `hours` | 2. Opening hours |
| `payment` | 3. Payment methods and installment rules |
| `returns` | 4. Exchanges and returns |
| `shipping` | 5. Shipping and delivery |
| `promotions` | 6. Promotions and discounts |
| `warranty` | 8. Warranty |
| `privacy` | 9. Privacy and data protection (LGPD) |
| `scope_and_tone` | 7. WhatsApp service guidelines, standard flow, special situations, and 10. Final provisions |

Where the manual contradicts itself or could mislead the agent, a clearly marked
**Editorial note (not part of the original manual)** at the top of the file says what prevails:

| File | Editorial note |
|---|---|
| `payment.md` | Section 3.1 prevails over the summary table; minimum installment R$ 50 up to 3x; PIX does not stack with promotions. |
| `store_info.md` | Use phone (67) 3341-4444 and `contato@emporiodamusica.com.br`: both appear on the manual's cover, and the phone also in the company table; the accented e-mail domain is treated as a typo. "Over 300 instruments" is not the catalog: availability comes from the data. |
| `scope_and_tone.md` | The WhatsApp number (67) 3321-4500 in section 7 conflicts with the company data; use (67) 3341-4444. No partner stores are listed, so the agent never names one. |
| `promotions.md` | The campaign calendar is not a promise: only promotions active in the data apply; never announce future or expired discounts. |

Section 10 (final provisions) was placed in `scope_and_tone` because its rule, "ask the
management when in doubt", is what the agent turns into a human handoff.

### Agent tools

Six tools in `src/emporio/tools.py`. The model picks the tool and its arguments; Python does
the filtering, math, date logic and identity checks. Each schema says what the tool is for
**and when not to use it**. Results are structured data (money as `R$ 1.299,00`, dates as
dd/mm/yyyy, so the model quotes instead of computing); errors are
`{"error": code, "message": text}` and never crash the agent.

| Tool | Use it for | Behavior |
|---|---|---|
| `search_products` | Browsing by category, string type, electronics, price range or text | Available items only by default, cheapest first, at most 5 (max 10), with the total match count and how many unavailable matches were hidden |
| `get_product` | One named product: price, promotion, PIX, installments, stock | Fuzzy name match; returns candidates when ambiguous; flags discontinued, coming soon and out of stock, with up to 3 in-stock alternatives |
| `get_order_status` | Status, items, total, payment, tracking, delivery, return windows | Needs the order id plus the e-mail or phone on file; one generic error on failure; locked after 3 failures |
| `get_policy` | Store rules by topic | Topic is a closed enum of the nine policy files |
| `get_store_status` | Open right now? Next opening? | Store time zone, the agent clock and holidays |
| `escalate_to_human` | Complaints, delays, defects, anything unresolved | Appends to `data/outbox/escalations.jsonl` and returns a protocol id with the 24 business hours promise |

| Decision | Rule in code |
|---|---|
| Price filters ("até R$ 1000") | Applied to the **final** price, which is what the customer pays. |
| Loose product names ("gd20", "takamine gd 20") | Names are normalized (accents, case, punctuation) and generic words such as "violão" are dropped. The score is the best of a squashed-string partial match and a token-set match (rapidfuzz). Below 80 means not found. If several products score within 5 points of the best, the customer is asked to choose (e.g. "Martin D-28" matches both the D-28 and the D-28E). |
| Alternatives for unavailable products | In-stock products of the same category, narrowed step by step to the same string type, the same 7-string format and the same electronics when such products exist, then closest price first. |
| Unreliable descriptions | Withheld from tool results, with a note. |
| Installments | `get_product` returns every allowed option (1x to the maximum) with its value, so the model never divides. |
| Order verification | The e-mail is compared case-insensitively; the phone by digits only, ignoring punctuation and the +55 country code. A wrong contact and a nonexistent order get the same message, so the tool never reveals whether an order exists. Three failures lock order lookups for the conversation. |
| Customer data in results | Only the customer's first name (to greet them) and the order's own data. Never e-mail, phone, full name or other customers. |
| Delayed orders | `delivery_overdue` is true for a shipped order past its estimated delivery (order 8). |
| Escalation | Protocol `EMP-YYYYMMDD-XXXXXX`; the record (reason, summary, order id, contact, session) goes to a local JSONL outbox that is gitignored. |
