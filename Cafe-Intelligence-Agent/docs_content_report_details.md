# Person 3: Content Agent + Report + Delivery/Memory/Control

Built against Person 1's real `clean_data.py`/`cleaner.py` and Person 2's real
`agents/*.py` + `graph.py` + `memory/store.py` + `state.py` — not the mock schema
an earlier draft of this module was built against (see "What changed" below if
you're comparing against that version).

## Run it

```bash
pip install -r requirements.txt
python clean_data.py                 # Person 1
python run_full_pipeline.py --real   # Person 2's graph + Person 3's content/report/approval/memory
```

This triggers a real run, walks the graph to the human-approval breakpoint, prints
the WhatsApp summary, shows one content idea traced back to the exact verified
finding that motivated it, simulates the owner replying, resumes the graph, and
confirms the outcome was saved to long-term memory. Full HTML report + WhatsApp
text land in `output/`.

```bash
python run_full_pipeline.py --real --decision REJECT
python run_full_pipeline.py --real --decision "EDIT: custom text the owner typed"
python run_full_pipeline.py --real --week-id 2026-W21   # pick a specific week_id for memory testing
```

### Sandbox note — read this before assuming something's wrong

`agents/margin.py` and `agents/reviews.py` always call an LLM (Gemini, via
`GOOGLE_API_KEY`) to extract price-change events and complaint themes from free
text — that's real analysis, not an optional retry path, so it can't be skipped.
The environment this was built in has no network route to
`generativelanguage.googleapis.com`, so `run_full_pipeline.py` auto-detects a
missing `GOOGLE_API_KEY`/`ANTHROPIC_API_KEY` and stubs **only those two calls**
(plus the critic's soft contradiction-check call) with the exact values verified
by hand against `clean_data/` — see "Provenance of the stubbed LLM values" below.
Every other line of every agent's code — all the pandas, all the subprocess
execution, the self-correction loop, the critic's deterministic checks, the
z-score anomaly detection — runs for real, unmodified, against the real
66,097-row dataset. **Set `GOOGLE_API_KEY` and this stubbing never activates.**

```bash
GOOGLE_API_KEY=... python run_full_pipeline.py --real
```

## What's new here vs. a plain content/report module

**Per-finding calendar grounding, not a single "this week" label.** Person 2's
real anomaly agent found genuine statistical spikes clustered on **2026-03-20/21
(Eid al-Fitr)** and **2026-05-29** (just before Eid al-Adha) — not hand-picked,
actual z-score outliers in the real data. `content/content_agent.py` checks each
finding's OWN date (parsed from its claim/evidence text) against the KSA calendar,
so an idea built from the March spike correctly says "during Eid al-Fitr" even
though the run's nominal `week_id` might be a completely different week. This is
the literal ask in the brief — "your agent should notice the cafe behaves like a
completely different business" — done from the data, not from today's date:

```
Idea: Why Flat White orders jumped this week (during Eid al-Fitr)
Cites: ['Flat White sales spike on 2026-03-21 (214.8% vs baseline)']
 -> traced to verified finding: [anomaly] ... (number=4.83)
```

**Item resolution from free text, not a `sku` field.** Person 2's real
`verified_findings` shape is flat — `{agent, claim, number, evidence}`, no `sku`,
no `claim_ar`. `content/item_matcher.py` resolves which menu item a claim is
about by matching `menu_items.csv`'s `item_en` names against the claim text
(longest name first, so "Iced Spanish Latte" doesn't get shadowed by "Latte").
Used for chart selection, the correct posting-time category, and the Arabic item
name.

**Arabic is generated, not pre-supplied.** Since findings have no `claim_ar`,
`content/translate.py` does one batched LLM call (same "all N at once, not one
call per item" pattern as `agents/margin.py`/`agents/reviews.py`) to translate
every finding's claim, with a deterministic non-LLM template fallback
(`_template_translate`) so the bilingual report is never blocked on a missing key
— see it run in the demo output above with zero keys set.

**Memory is genuinely wired in, not just available.** `content/content_node.py`
calls `memory.store.WeeklyMemoryStore.find_matching_past_idea()` for every
generated idea and `find_streak()` per finding; `memory/memory_node.py` saves the
week's outcome (including the owner's actual approve/reject decision) after the
human-approval node resolves. Verified across 3 real consecutive `--week-id` runs
in this repo — by the 3rd week, ideas correctly carry
`"This is the 2nd week in a row this pattern has shown up"` and
`"Similar idea proposed in 2026-W21 (approved=True)"`.

**The human breakpoint is a real LangGraph `interrupt()`, not a placeholder
string.** `report/approval_node.py` pauses the graph with `interrupt()`;
`full_graph.py` adds a `MemorySaver` checkpointer (Person 2's `graph.py`
deliberately doesn't need one — no pause there) so the paused state survives
between the `invoke()` that hits the interrupt and the later `invoke()` that
resumes it with `Command(resume=<owner's reply>)`. Three tested paths: APPROVE,
REJECT, `EDIT: <replacement text>` (see `tests/test_person3_integration.py`).

## Structure

```
content/
  item_matcher.py    # resolves a menu item from a claim's free text
  local_context.py   # Ramadan/Eid/summer calendar (offline) + optional Tavily search
  posting_time.py     # real best-day/hour-to-post per menu category, from clean_data
  translate.py        # batched Arabic translation of findings, LLM + template fallback
  content_agent.py    # 3 grounded ideas: per-finding calendar tie-in, memory-aware, validated
  content_node.py     # CafeState graph node wrapper — attaches after critic_agent
report/
  charts.py           # real matplotlib charts, item resolved from claim text
  report_generator.py # WhatsApp summary (bilingual, diverse-agent selection) + HTML report
  report_node.py       # CafeState graph node wrapper
  approval_node.py     # human breakpoint via langgraph.types.interrupt()
  templates/report.html.j2
memory/
  memory_node.py       # CafeState graph node wrapper around Person 2's WeeklyMemoryStore
full_graph.py           # assembles Person 2's graph.py nodes + Person 3's nodes, +checkpointer
run_full_pipeline.py     # the 10-minute demo: trigger, interrupt, trace-back, resume, memory
tests/test_person3_integration.py   # 8 tests against the real schema, incl. interrupt/resume
```

## Provenance of the stubbed LLM values

Used only when no `GOOGLE_API_KEY`/`ANTHROPIC_API_KEY` is set (see sandbox note
above). Both are hand-verified against the actual raw source files, same standard
as the rest of this project's numbers:

- **Margin price events** — read directly from `data_raw/supplier_emails/2026-04-01_05.txt`
  ("roasted price moves from SAR 88/kg to SAR 96/kg effective 15 April") and
  `2026-05-04_07.txt` ("full-fat milk moves from SAR 7.10/L to SAR 8.40/L effective
  1 May 2026"). These are exactly what `extract_price_change_events()` is supposed
  to extract from those two emails.
- **Review themes** — counted by hand against `clean_data/customer_reviews.csv`:
  `rating <= 2` rows containing "V60"/"filter" (30), "wait"/"crowd" (10), and
  "expensive"/"price" (4), out of 54 low-rated reviews total. Reproduce with:
  ```python
  import pandas as pd
  low = pd.read_csv("clean_data/customer_reviews.csv").query("rating <= 2")
  low.text.str.contains("v60|filter", case=False).sum()  # -> 30
  ```

## Known integration observations (not my code to fix, flagging for the group)

- `agents/operations.py`'s `dead_sensor_days_excluded` counts **hourly rows**
  flagged `sensor_dead`, not distinct days — on the real data it reports "51
  dead-sensor day(s) excluded" where Person 1's quality report says 3 actual days
  (51 ≈ 3 days × ~17 open hours/day). The conversion-rate math itself is still
  correct (it filters by the boolean per-row, which is right), just the count in
  the evidence string is mislabeled. Worth a one-line fix in `operations.py` if
  anyone reads that evidence string closely during grading.
- `state.py`'s `week_id` isn't tied to the dataset's own date range — it's
  whatever ISO week `datetime.now()` resolves to when the scheduler fires, which
  will drift from the historical Jan–Jul 2026 dataset once this runs on a real
  clock. `content_agent.py` deliberately does NOT trust `week_id` for calendar
  reasoning because of this — it re-derives context per finding from dates found
  in the finding's own text instead (see "per-finding calendar grounding" above).
  Worth confirming with Person 2 whether the scheduler should instead pass the
  actual data window's week once this moves off the historical demo dataset.

## Testing

```bash
python -m pytest tests/test_person3_integration.py -v
```

8 tests: content-idea citation validation against real verified findings, the
Eid-al-Fitr calendar tie-in (proven against the finding's own date, not the
run's nominal week), empty-findings degrade-not-crash, no-hardcoded-cafe-name
guard, the diverse-agent WhatsApp-selection regression test, HTML-report
Jinja-leak/None-leak check, and two full `full_graph.py` runs through the real
`interrupt()`/`Command(resume=...)` roundtrip (APPROVE path and REJECT path).
All pass with zero API keys configured (LLM stubbing per the sandbox note).

## Next steps once a real GOOGLE_API_KEY is available

1. Re-run `run_full_pipeline.py --real` with the key set and confirm the LLM
   paths (margin extraction, review themes, critic's soft check, translation,
   optionally LLM-mode content ideas) produce output at least as good as the
   stubbed/template fallbacks — they should, since the fallbacks were built to
   the same shape.
2. Run the assignment's full 10-weekly-cycle test matrix (including a real
   Ramadan week and the peak-summer week) through `run_full_pipeline.py --real
   --week-id <...>` and record pass/partial/fail per cycle.
3. Swap `MemorySaver` for a persistent checkpointer (`SqliteSaver` at minimum) in
   `full_graph.py` before this runs unattended on `scheduler/full_scheduler.py`'s
   weekly cron — an in-memory checkpointer loses a pending approval if the
   process restarts between the interrupt and the owner's reply.

## v3 fixes — gaps found against a direct requirements re-check

A line-by-line check against the assignment brief surfaced several real gaps.
All are now fixed and tested; documented here rather than silently folded in,
since a couple represent genuine design decisions worth flagging to the group.

### Content agent grounding fixes

- **"Push the high-margin item that's trending up; don't promote the thing
  you're about to run out of"** was not implemented at all before this pass —
  content ideas were picked by revenue (sales_agent's ranking) or anomaly
  z-score, never by margin, with zero stockout check. `content/item_selector.py`
  now computes margin %, week-over-week trend, and stockout risk (from
  `inventory_weekly.csv`'s ordered-vs-sold ratio) directly from `clean_data/`,
  and idea #1 is always this scorer's pick — verified on the real dataset to
  correctly choose **Karak Tea** (80% margin, genuinely trending, no stockout
  risk) over the highest-revenue item (Matcha Latte), which is exactly the
  distinction the brief draws.
  - **Coverage caveat, stated plainly**: `inventory_weekly.csv` only tracks
    bakery/food SKUs — drink SKUs have no ordered/sold/wasted data at all, so
    stockout risk can never be checked for a drink. That's a property of the
    source data, not a bug in the scorer.
- **Calendar tie-in was being guessed by the LLM, and guessed wrong.** A real
  anomaly-detected spike on 2026-03-20/21 (Eid al-Fitr) was being labeled
  "March 21st" in LLM-mode output — the model was never told the date's
  significance, just handed a raw ISO date and asked to infer a Hijri
  holiday from it. Fixed by computing calendar context explicitly in Python
  (`_calendar_context_for_finding`) and handing the answer to the model as
  data, not a question. Verified fixed on a real run: idea now reads
  *"Why is everyone ordering the Flat White this Eid?"*
- **Invented product/finding pairings.** Caught in a real run: an idea about
  "4.33 average rating across 520 reviews" was paired with "Iced Spanish
  Latte" and a summer-heat angle — a plausible-sounding connection the LLM
  invented, since the rating finding says nothing about that specific drink.
  `_validate_product_grounding()` now checks that any named product is
  actually mentioned in the claim being cited; if not, the product tie-in is
  stripped (verified: the idea correctly falls back to a general trust
  message with no product claimed, rather than a false one).
- **Invented posting times.** Caught in the same run: "Friday 9 AM" was
  shown for a category whose real busiest hour is Friday 9 PM — the LLM's
  own arithmetic, not the real posting-time data. Fixed by never trusting the
  model's best_day/best_time at all: `_overwrite_posting_time()`
  unconditionally recomputes both from the same deterministic
  `posting_time.py` data the template path already uses, discarding
  whatever the model returned.
- **Bilingual rationale.** `rationale_ar` is now a real, separately-written
  field — previously the Arabic report block was silently showing the
  English rationale text verbatim, which isn't bilingual, it's broken.
- **`🧠 None` literal string bug** and **raw `21` instead of `9 PM`** — both
  fixed (`_clean_memory_note`, `_format_time`).
- **"The product it features" isn't always shown** — by design, now: when no
  real data supports naming a specific product (e.g. an overall-rating
  finding), the idea correctly stays general rather than inventing one. The
  report's "Featured product" line simply doesn't appear for that idea. If a
  strict reading of the brief requires every idea to name *something*, that's
  a judgement call worth a second look — the alternative (forcing a
  plausible-but-unsupported pick every time) is the exact failure mode this
  whole pass was fixing.

### Delivery, memory & control fixes

- **Scheduler was only proving the analysis half runs on its own.** Person
  2's `scheduler/scheduler.py` fires `graph.py` (5 analysts + critic) — it
  never touches content generation, the report, the human breakpoint, or
  memory saving. `scheduler/full_scheduler.py` is Person 3's fix: fires
  `full_graph.py` on the same APScheduler cron pattern, runs unattended up to
  the human-approval `interrupt()`, writes the pending decision to
  `pending_approvals.json`, and exposes `resume_pending_approval(week_id,
  decision)` for whatever receives the owner's actual WhatsApp reply to call
  later. Verified end-to-end: `python -m scheduler.full_scheduler --now`
  triggers a real run that pauses correctly, and `--resume <week_id>
  --decision APPROVE` completes it. This is the honest shape of "scheduled +
  human breakpoint" together — the trigger is unattended, the approval
  genuinely isn't.
- **Cost caps** — `content/cost_tracker.py` adds a per-call-site USD budget
  check (default $0.50, override via `CONTENT_AGENT_COST_CAP_USD`) before
  each of Person 3's own LLM calls (content idea generation, translation).
  **Scope caveat, stated plainly**: this only covers Person 3's call sites —
  Person 2's `margin.py`/`reviews.py`/`critic.py` make their own separate LLM
  calls with no equivalent tracker; a true whole-pipeline budget would need
  the same pattern added there too.
- **LangSmith tracing** — `run_full_pipeline.py` now prints an explicit
  ENABLED/NOT-enabled status line at startup (checking
  `LANGCHAIN_TRACING_V2`/`LANGCHAIN_API_KEY`) so this is never silently
  unverified. **Never actually exercised with a real LangSmith key in this
  environment** — the deliverable's "full trace of every run" requirement
  needs someone with real credentials to run it once and confirm.
- **Tavily local search** — the code path (`content/local_context.py`'s
  `search_local_events`) has existed since the first content-agent build and
  degrades cleanly, but **has never actually returned a real search result**
  in any run so far, since `TAVILY_API_KEY` has never been set in this
  environment. The "grounded in what's happening locally" requirement is
  code-complete but empirically unverified until someone runs it with a real
  key.

### Two bonus features added (brief requires at least 2)

- **Waste-to-Riyals** (`content/waste_analysis.py`) — quantifies real
  monthly waste cost per bakery/food item from `inventory_weekly.csv` and
  proposes a reduced weekly order quantity (actual sell-through + 10% safety
  margin). On the real dataset: **SAR 4,468/month** total waste cost,
  **~SAR 2,350/month** recoverable by ordering closer to demand. Surfaced in
  both the WhatsApp summary (top offender + total) and the full HTML report
  (per-item breakdown), bilingual.
- **Menu Engineering** (`content/menu_engineering.py`) — classic
  popularity-x-margin four-quadrant classification (star / plowhorse /
  puzzle / dog) for every menu item, reusing `item_selector.py`'s margin
  computation. Flags concrete cut candidates with the actual numbers behind
  each recommendation (e.g. "Date Cake: popularity 0.9x average, margin
  70%"). Also in both the WhatsApp summary and full report.

### Testing

`tests/test_person3_integration.py` grew from 8 to 14 tests — added: the
featured-idea margin/trend grounding check, waste-analysis sanity checks,
menu-engineering quadrant coverage, cost-tracker budget enforcement, and a
full scheduler pause/resume regression test. All 14 pass with zero API keys
(LLM stubbing per the sandbox note above).

