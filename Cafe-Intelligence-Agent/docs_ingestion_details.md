# Data Engineer: Ingestion + Cleaning

## Run it

```bash
pip install -r requirements.txt   
python clean_data.py
```

Output lands in `clean_data/`:
- `pos_transactions.csv`, `menu_items.csv`, `foot_traffic.csv`, `staff_shifts.csv`,
  `inventory_weekly.csv`, `supplier_emails.csv`, `customer_reviews.csv` — clean, ready for the analysts
- `quality_report.md` / `.json` — what was cleaned and why, with counts

## Structure

```
config/sources_config.json   # source registry — onboarding a 2nd cafe = edit this file only
parsers/base.py              # NormalizedRecord / ParseResult — the one shared schema
parsers/csv_parser.py        # pos_transactions, menu_items, foot_traffic, staff_shifts
parsers/excel_parser.py      # inventory_weekly.xlsx
parsers/email_parser.py      # supplier_emails/*.txt
parsers/json_parser.py       # customer_reviews.json
ingestion_graph.py           # LangGraph StateGraph, Send fan-out, one node per source
cleaning/cleaner.py          # all cleaning logic + SourceQualityReport
clean_data.py                # orchestrator: ingestion -> cleaning -> clean_data/
```

## Design decisions (for the "why" writeup)

**Ingestion never cleans.** Parsers only read + normalize structure (dates split into
date/time, numbers cast to float/int). Every source-specific mess (double-swipes, bad
item names, two date formats, dead sensor days) is left untouched at this stage and
fixed explicitly in `cleaning/cleaner.py`, one function per source, each returning a
`SourceQualityReport` — so every fix is countable and traceable, nothing is silent.

**Why one node per source, not one big ingestion function.** `parse_source_node` in
`ingestion_graph.py` never raises — any failure becomes `ParseResult.fatal_error`
instead of an exception, so a corrupt `inventory_weekly.xlsx` can't take down the other
five sources. `fan_out_sources` uses the Send API so all sources parse in parallel.

**Double-swipe dedup is conservative on purpose.** A "double swipe" is defined as an
*exact* duplicate row (same `transaction_id`, `sku`, `quantity`, prices, payment,
channel, cashier, timestamp) — not "similar transaction close in time." Two different
customers ordering the same combo seconds apart would look similar but isn't a
duplicate; only byte-for-byte repeats get dropped. On this dataset that's **98 rows /
90 transactions (0.15% of rows)** — lower than the doc's approximate "~1%", which is
expected from a precision-over-recall heuristic. Worth flagging to the group: if the
critic's ground-truth check disagrees, this is the parameter to revisit.

**item_name is always replaced from `menu_items` via `sku`**, never patched
conditionally, because `sku` is documented as "always present and always correct" —
so there's no case where trusting the original `item_name` is safer.

**Dead sensor days are flagged, not dropped or zero-filled.** `foot_traffic` rows for
those 3 days stay in the data with `sensor_dead=True` so the Operations analyst can
explicitly exclude them from conversion-rate calculations instead of silently
computing a 0% conversion day.

**`units_wasted` blank stays `NaN`**, with a `waste_recorded` boolean column, so
"not recorded" can never be summed as if it were 0 waste.

## LangGraph vs. fallback

`ingestion_graph.py` is the actual Module-1 deliverable (`StateGraph` + `Send` fan-out,
graded requirement). `clean_data.py` tries to import and run it first; if `langgraph`
isn't installed in whatever machine runs this, it falls back to calling the same
parser functions in a plain loop with the same "no source failure kills the others"
contract, so the pipeline is always runnable. Once the course venv (Week 6 setup) is
active, `pip install langgraph` and the graph path runs — output is identical either
way since both paths call the exact same `parsers/*.py` functions.

## Verified against the real dataset (Jan–Jul 2026, 66,195 POS rows)

| source | rows in | rows out | notes |
|---|---|---|---|
| pos_transactions | 66,195 | 66,097 | 98 double-swipe rows dropped, 3,586 item_names repaired (1,601 were null), 530 refund rows flagged, 993 alt-format timestamps reconciled |
| menu_items | 19 | 19 | 1 item (Matcha Latte) has a mid-period launch_date — flagged for analysts |
| foot_traffic | 3,296 | 3,296 | 3 dead-sensor days flagged (2026-06-08 → 06-10) |
| staff_shifts | 1,036 | 1,036 | 1 employee (EMP-01) has no shifts after 2026-03-15 |
| inventory_weekly | 174 | 174 | 120 alt-format dates reconciled, 5 unrecorded waste values kept as null |
| supplier_emails | 13 | 13 | passthrough — flagging *which* email matters is the Margin analyst's job |
| customer_reviews | 520 | 520 | passthrough, language-tagged ar/en |

## Open question for Person 2

The normalized ingestion schema is `NormalizedRecord(source, record_id, date, time,
attrs, raw)` — a generic wrapper, not per-source columns — so the graph's overall
`OverallState` can hold `raw_records: dict[str, list[NormalizedRecord]]` cleanly. But
the **clean_data/*.csv outputs are plain pandas-shaped tables**, one per source, which
is what the analyst nodes will actually read. Confirm this matches what you're
building `state.py` around before you wire the analysts in — happy to adjust the
output format if you need something else (e.g. one merged DataFrame, or Parquet
instead of CSV).
