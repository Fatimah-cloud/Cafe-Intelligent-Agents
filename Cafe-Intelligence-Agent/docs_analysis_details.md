# Person 2 — Analysis, Critic, Memory & Scheduler — Status

## What's done

### Analysis layer (5 parallel analysts + critic)
- `state.py` — shared `CafeState` schema everyone codes against
- `agents/sales.py` — best/worst seller by **average weekly revenue** (not raw total, so items launched mid-period like Matcha Latte aren't unfairly flagged), week-over-week trend
- `agents/margin.py` — extracts real supplier price changes from your raw `supplier_emails.csv` (subject+body, one LLM call for all 13 emails), computes margin pre/post each change instead of using the static `menu_items.csv` cost
- `agents/operations.py` — conversion rate (respects `sensor_dead`), peak hour, stale employees, waste % (respects `waste_recorded`)
- `agents/reviews.py` — rating stats by language/platform + top 3 complaint themes (bilingual, one batched LLM call)
- `agents/anomaly.py` — z-score outlier detection across revenue/item-sales/traffic; caps output at the **top 8** most extreme results (ranked by |z-score|) to avoid flooding the report — real data run currently returns 8 clean findings, mostly clustered around the Ramadan/Eid week (Mar 20-21)
- `agents/critic.py` — cuts unevidenced claims automatically, **plus deterministic sanity-range checks** (e.g. a conversion rate can't be >100%), routes a failure back to the specific analyst, capped at 3 revisions
- `agents/_code_runner.py` — shared subprocess runner: every analyst's generated code runs in a subprocess (never `exec()` in-process) with a timeout; on failure, the exact error is sent to the model to produce a fixed version (real self-correction, up to 3 attempts)

### Graph
- `graph.py` — wires all 5 analysts in parallel fan-out → critic → loop-back or done. State only carries `clean_data_dir` (a path string), not raw DataFrames, so LangSmith traces stay small. `python graph.py --real` runs it against your `clean_data/`; `python graph.py` uses mock data.

### Data loading
- `load_real_data.py` — `load_source(clean_data_dir, key)` is what every analyst calls to read exactly one CSV. Missing files return an empty DataFrame, not a crash.

### Long-term memory
- `memory/store.py` — JSON file on disk (survives the scheduler's weekly process restarts). `find_streak()` detects "Nth week in a row" patterns; `find_matching_past_idea()` catches repeated content ideas via word-overlap matching (not exact text match).

### Scheduler
- `scheduler/scheduler.py` — APScheduler, fires the graph automatically on a weekly cron (no manual trigger). `python scheduler.py --now` runs one cycle immediately for testing/demo.

## Tested against real data
Ran end-to-end on the full `clean_data/` (66,097 POS rows): **27 verified findings**, 0 critic revisions needed (all findings passed evidence + sanity checks on this run). Confirmed the anomaly cap, the sensor_dead/waste_recorded handling, and the margin pre/post-price-change split all work against your actual cleaned output.

## What I need from you two
- **From Person 1**: nothing pending — `clean_data_dir` interface is stable, all 7 sources load correctly.
- **For Person 3**: `graph.py`'s `END` is where your `content_agent` node should attach. `final_state["verified_findings"]` is the list to build content ideas from (each item: `{agent, claim, number, evidence}`). `memory/store.py`'s `WeeklyMemoryStore` is available for your report/approval flow too if useful — `save_content_idea()` / `find_matching_past_idea()` are there for the "you approved this last month" requirement.

## Known limitations (documented, not blocking)
- Margin's price-change extraction currently maps `roasted_coffee`/`full_fat_milk` price changes to the `hot_coffee`/`iced_coffee` categories broadly (no per-recipe ingredient breakdown exists in the dataset), so the "before/after" margin split is directional, not exact.
- No checkpointer on this graph (state isn't step-by-step persisted) since it runs start-to-finish in one call with no human-in-the-loop pause at this layer.