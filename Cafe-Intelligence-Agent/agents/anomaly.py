"""
Anomaly Detection Analyst.

Question this agent answers:
- Anything statistically odd in the current week that nobody asked about

Design note: unlike the other analysts, this one has no fixed question --
it scans multiple signals (daily revenue, item-level sales, foot traffic)
for statistical outliers using a simple, explainable method (z-score vs.
a trailing baseline), not a fixed threshold guessed in advance. Every
flagged anomaly carries the actual numbers that triggered it, so the
Critic can verify the claim instead of taking "something looks weird" on
faith.

Multiple-comparisons note: testing every item x every day (thousands of
z-tests) at a single threshold produces many outliers by chance alone --
roughly 5% of normal points cross z=2.0 just from statistical noise.
Reporting every point that crosses the threshold would flood the report
with noise, so this agent ranks all candidates by |z-score| and keeps
only the TOP_N most extreme, matching the assignment's calibration
guidance ("a good agent surfaces four or five findings... a bad one
invents twelve").
"""
from typing import TYPE_CHECKING
import pandas as pd

from agents._code_runner import run_self_correcting_code
from load_real_data import load_source_for_state

if TYPE_CHECKING:
    from state import CafeState


TOP_N_ANOMALIES = 8


ANOMALY_CODE_TEMPLATE = '''
import pandas as pd
import json

pos = pd.read_json("{pos_path}")
menu = pd.read_json("{menu_path}")
traffic = pd.read_json("{traffic_path}")

pos["timestamp"] = pd.to_datetime(pos["timestamp"])
pos["date"] = pos["timestamp"].dt.date

merged = pos.merge(menu[["sku", "item_en"]], on="sku", how="left")

anomalies = []
Z_THRESHOLD = {z_threshold}

# --- Signal 1: daily revenue outliers (z-score vs. the full period) ---
daily_revenue = merged.groupby("date")["line_total_sar"].sum()
if len(daily_revenue) >= 3:
    mean_rev = daily_revenue.mean()
    std_rev = daily_revenue.std()
    if std_rev and std_rev > 0:
        z_scores = (daily_revenue - mean_rev) / std_rev
        outlier_days = z_scores[z_scores.abs() >= Z_THRESHOLD]
        for date, z in outlier_days.items():
            anomalies.append({{
                "signal": "daily_revenue",
                "date": str(date),
                "value": float(daily_revenue[date]),
                "baseline_mean": round(float(mean_rev), 1),
                "z_score": round(float(z), 2),
                "direction": "spike" if z > 0 else "drop",
            }})

# --- Signal 2: per-item daily sales outliers (e.g. "coffee sales dropped 40%") ---
item_daily = merged.groupby(["date", "item_en"])["quantity"].sum().reset_index()
for item in item_daily["item_en"].dropna().unique():
    series = item_daily[item_daily["item_en"] == item].set_index("date")["quantity"]
    if len(series) >= 3:
        mean_q = series.mean()
        std_q = series.std()
        if std_q and std_q > 0 and mean_q > 0:
            z_scores = (series - mean_q) / std_q
            outliers = z_scores[z_scores.abs() >= Z_THRESHOLD]
            for date, z in outliers.items():
                pct_change = ((series[date] - mean_q) / mean_q * 100)
                anomalies.append({{
                    "signal": "item_daily_quantity",
                    "item": item,
                    "date": str(date),
                    "value": float(series[date]),
                    "baseline_mean": round(float(mean_q), 1),
                    "pct_change_from_baseline": round(float(pct_change), 1),
                    "z_score": round(float(z), 2),
                    "direction": "spike" if z > 0 else "drop",
                }})

# --- Signal 3: foot traffic outliers (excluding known dead-sensor days) ---
if "sensor_dead" in traffic.columns:
    good_traffic = traffic[~traffic["sensor_dead"]]
else:
    good_traffic = traffic
traffic["date"] = pd.to_datetime(traffic["date"])
good_traffic = good_traffic.copy()
good_traffic["date"] = pd.to_datetime(good_traffic["date"])
daily_traffic = good_traffic.groupby(good_traffic["date"].dt.date)["door_count"].sum()
if len(daily_traffic) >= 3:
    mean_t = daily_traffic.mean()
    std_t = daily_traffic.std()
    if std_t and std_t > 0:
        z_scores = (daily_traffic - mean_t) / std_t
        outlier_days = z_scores[z_scores.abs() >= Z_THRESHOLD]
        for date, z in outlier_days.items():
            anomalies.append({{
                "signal": "foot_traffic",
                "date": str(date),
                "value": float(daily_traffic[date]),
                "baseline_mean": round(float(mean_t), 1),
                "z_score": round(float(z), 2),
                "direction": "spike" if z > 0 else "drop",
            }})

result = {{
    "anomalies": sorted(anomalies, key=lambda a: abs(a["z_score"]), reverse=True)[:{top_n}],
    "total_anomalies_before_top_n": len(anomalies),
    "z_threshold_used": Z_THRESHOLD,
}}
print(json.dumps(result))
'''


def anomaly_agent(state: "CafeState", z_threshold: float = 2.0) -> dict:
    """
    Runs statistical outlier detection via subprocess. z_threshold=2.0
    means ~95% of normal variation is expected to fall within range;
    anything beyond gets flagged. Lower threshold = more sensitive (more
    findings, more false positives); this is a tunable parameter worth
    documenting in the report rather than a magic number.
    """
    pos: pd.DataFrame = load_source_for_state(state, "pos")
    menu: pd.DataFrame = load_source_for_state(state, "menu")
    traffic: pd.DataFrame = load_source_for_state(state, "traffic")

    import tempfile
    import os
    tmp_dir = tempfile.gettempdir()
    paths = {
        "pos_path": os.path.join(tmp_dir, "_anomaly_pos.json").replace("\\", "/"),
        "menu_path": os.path.join(tmp_dir, "_anomaly_menu.json").replace("\\", "/"),
        "traffic_path": os.path.join(tmp_dir, "_anomaly_traffic.json").replace("\\", "/"),
    }
    pos.to_json(paths["pos_path"], orient="records")
    menu.to_json(paths["menu_path"], orient="records")
    traffic.to_json(paths["traffic_path"], orient="records")

    code = ANOMALY_CODE_TEMPLATE.format(z_threshold=z_threshold, top_n=TOP_N_ANOMALIES, **paths)

    result = run_self_correcting_code(code, max_fix_attempts=3)

    if not result["ok"]:
        return {
            "findings": [{
                "agent": "anomaly",
                "claim": "Anomaly detection failed after self-correction attempts",
                "number": None,
                "evidence": f"final error: {result['error']} | attempts: {result['attempts_log']}",
            }]
        }

    data = result["data"]
    total_before = data.get("total_anomalies_before_top_n", len(data["anomalies"]))
    if not data["anomalies"]:
        return {
            "findings": [{
                "agent": "anomaly",
                "claim": "No statistically significant anomalies detected this period",
                "number": 0,
                "evidence": f"z-score threshold {data['z_threshold_used']} across revenue, per-item sales, and foot traffic signals",
            }]
        }

    findings = []
    for a in data["anomalies"]:
        if a["signal"] == "daily_revenue":
            claim = f"Daily revenue {a['direction']} on {a['date']}"
            evidence = f"SAR {a['value']} vs baseline mean SAR {a['baseline_mean']} (z={a['z_score']})"
        elif a["signal"] == "item_daily_quantity":
            claim = f"{a['item']} sales {a['direction']} on {a['date']} ({a['pct_change_from_baseline']}% vs baseline)"
            evidence = f"{a['value']} units vs baseline mean {a['baseline_mean']} units (z={a['z_score']})"
        else:  # foot_traffic
            claim = f"Foot traffic {a['direction']} on {a['date']}"
            evidence = f"{a['value']} visitors vs baseline mean {a['baseline_mean']} (z={a['z_score']}); dead-sensor days already excluded"

        if total_before > TOP_N_ANOMALIES:
            evidence += (
                f"; showing top {TOP_N_ANOMALIES} of {total_before} points that crossed "
                f"the z={z_threshold} threshold (ranked by |z-score| to avoid "
                f"over-reporting from testing many items x days at once)"
            )

        findings.append({
            "agent": "anomaly",
            "claim": claim,
            "number": a["z_score"],
            "evidence": evidence,
        })

    return {"findings": findings}


if __name__ == "__main__":
    from mock_data.mock_cleaned_data import write_mock_clean_data_dir

    fake_state = {"clean_data_dir": write_mock_clean_data_dir()}
    output = anomaly_agent(fake_state)
    for f in output["findings"]:
        print(f)
        print()