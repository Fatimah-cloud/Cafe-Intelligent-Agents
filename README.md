# Cafe Intelligence Agent - Qahwa Saihat

A weekly, autonomous, multi-agent system that reads a cafe's raw data across
six sources, cleans it, runs five parallel analysts with a critic gatekeeping
every claim, turns the verified findings into TikTok/Instagram ideas grounded
in the data + local context + the calendar, and delivers a bilingual
WhatsApp summary and full HTML report pausing for the owner's approval
before anything goes out, and remembering every week that came before.


# Run it - one command

```bash
git clone <this repo>
cd cafe-intelligence-agent-main
python3 -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # then fill in whichever keys you have — see below
python3 run_full_pipeline.py --real
```



```bash
python3 run_full_pipeline.py --real --decision REJECT
python3 run_full_pipeline.py --real --decision "EDIT: custom text"
python3 run_full_pipeline.py --real --week-id 2026-W12   # test a specific week (e.g. Ramadan)
```


## Architecture

```
START ──▶ [sales_agent, margin_agent, operations_agent, reviews_agent, anomaly_agent]  (parallel)
              │
              ▼
         critic_agent ──(revision needed)──▶ back to the flagged analyst (max 3 loops)
              │ (satisfied)
              ▼
         content_agent  ── 3 TikTok/IG ideas, grounded in margin/trend/stock +
              │            calendar + local search, each citing a real number
              ▼
         report_node    ── WhatsApp summary + full bilingual HTML report 
              │             
              ▼
         human_approval ── PAUSES here (langgraph interrupt()) — owner approves,
              │              edits, or rejects via WhatsApp
              ▼
         memory_save    ── records this week + the approval decision for next week
              │
              ▼
             END
```

