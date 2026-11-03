# Empório da Música Support Agent

A Python text-message agent that answers customers of Empório da Música, a fictional musical
instruments store in Campo Grande/MS, using the store's data (CSVs) and its policy manual.

> Work in progress. This README grows with each step; the full version (quick start,
> technical decisions, limitations and AI assistant usage) comes in the final step.

## Progress

- [x] Project skeleton and raw data
- [x] Data loading and data quality report
- [ ] Business rules: pricing, PIX, installments, store hours, return windows
- [ ] Policy sections and policy lookup
- [ ] Agent tools with order verification
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
