"""
Operations Analyst.

Questions this agent answers:
- Conversion rate (footfall vs transactions)
- Staffing vs demand by hour
- Waste vs orders

Design notes (from Person 1's actual cleaned_data):
- foot_traffic has a "sensor_dead" boolean column for the 3 dead-sensor
  days (2026-06-08 -> 06-10). Those days MUST be excluded from conversion
  calculations, not treated as 0% conversion.
- inventory_weekly has a "waste_recorded" boolean column. units_wasted is
  NaN when not recorded -- must not be summed as if it were 0.
- staff_shifts flags an employee (EMP-01) with no shifts after 2026-03-15;
  that's real (departed), not a data error, but skews any "average staff
  on floor" number computed across the full 6 months if not noted.
"""
from typing import TYPE_CHECKING
import pandas as pd

from agents._code_runner import run_self_correcting_code
from load_real_data import load_source_for_state

if TYPE_CHECKING:
    from state import CafeState


OPERATIONS_CODE_TEMPLATE = '''
import pandas as pd
import json

pos = pd.read_json("{pos_path}")
traffic = pd.read_json("{traffic_path}")
staff = pd.read_json("{staff_path}")
inventory = pd.read_json("{inventory_path}")

pos["timestamp"] = pd.to_datetime(pos["timestamp"])
traffic["date"] = pd.to_datetime(traffic["date"])
if "date" in staff.columns:
    staff["date"] = pd.to_datetime(staff["date"])

# --- Conversion rate: transactions vs footfall, excluding dead-sensor days ---
if "sensor_dead" in traffic.columns:
    good_traffic = traffic[~traffic["sensor_dead"]]
else:
    good_traffic = traffic

daily_traffic = good_traffic.groupby(good_traffic["date"].dt.date)["door_count"].sum()

pos["date"] = pos["timestamp"].dt.date
daily_transactions = pos.groupby("date")["transaction_id"].nunique()

common_dates = daily_traffic.index.intersection(daily_transactions.index)
total_traffic = daily_traffic.loc[common_dates].sum()
total_transactions = daily_transactions.loc[common_dates].sum()
conversion_pct = (total_transactions / total_traffic * 100) if total_traffic else None

excluded_days = int(traffic["sensor_dead"].sum()) if "sensor_dead" in traffic.columns else 0

# --- Staffing vs demand by hour ---
pos["hour"] = pos["timestamp"].dt.hour
demand_by_hour = pos.groupby("hour")["transaction_id"].nunique()
peak_hour = int(demand_by_hour.idxmax()) if len(demand_by_hour) else None
peak_hour_transactions = int(demand_by_hour.max()) if len(demand_by_hour) else None

stale_employees = []
if "employee_id" in staff.columns and "date" in staff.columns and len(staff) > 0:
    active_staff_dates = staff["date"].max()
    last_shift = staff.groupby("employee_id")["date"].max()
    stale = last_shift[last_shift < active_staff_dates - pd.Timedelta(days=30)]
    stale_employees = list(stale.index)

# --- Waste vs orders, respecting waste_recorded flag ---
if "waste_recorded" in inventory.columns:
    recorded = inventory[inventory["waste_recorded"]]
else:
    recorded = inventory[inventory["units_wasted"].notna()]

total_ordered = inventory["units_ordered"].sum()
total_wasted_recorded = recorded["units_wasted"].sum()
n_unrecorded = int((~inventory.get("waste_recorded", inventory["units_wasted"].notna())).sum())
waste_pct_of_orders = (total_wasted_recorded / total_ordered * 100) if total_ordered else None

result = {{
    "conversion_pct": round(conversion_pct, 1) if conversion_pct is not None else None,
    "dead_sensor_days_excluded": excluded_days,
    "peak_hour": peak_hour,
    "peak_hour_transactions": peak_hour_transactions,
    "stale_employees": stale_employees,
    "waste_pct_of_orders": round(waste_pct_of_orders, 1) if waste_pct_of_orders is not None else None,
    "weeks_with_unrecorded_waste": n_unrecorded,
}}
print(json.dumps(result))
'''


def operations_agent(state: "CafeState") -> dict:
    """Runs operations analysis via subprocess, respecting Person 1's
    sensor_dead and waste_recorded flags instead of re-deriving them."""
    pos: pd.DataFrame = load_source_for_state(state, "pos")
    traffic: pd.DataFrame = load_source_for_state(state, "traffic")
    staff: pd.DataFrame = load_source_for_state(state, "staff")
    inventory: pd.DataFrame = load_source_for_state(state, "inventory")

    # Cross-platform temp path (works on Windows too, unlike hardcoded /tmp)
    import tempfile
    import os
    tmp_dir = tempfile.gettempdir()
    paths = {
        "pos_path": os.path.join(tmp_dir, "_ops_pos.json").replace("\\", "/"),
        "traffic_path": os.path.join(tmp_dir, "_ops_traffic.json").replace("\\", "/"),
        "staff_path": os.path.join(tmp_dir, "_ops_staff.json").replace("\\", "/"),
        "inventory_path": os.path.join(tmp_dir, "_ops_inventory.json").replace("\\", "/"),
    }
    pos.to_json(paths["pos_path"], orient="records")
    traffic.to_json(paths["traffic_path"], orient="records")
    staff.to_json(paths["staff_path"], orient="records")
    inventory.to_json(paths["inventory_path"], orient="records")

    code = OPERATIONS_CODE_TEMPLATE.format(**paths)

    result = run_self_correcting_code(code, max_fix_attempts=3)

    if not result["ok"]:
        return {
            "findings": [{
                "agent": "operations",
                "claim": "Operations analysis failed after self-correction attempts",
                "number": None,
                "evidence": f"final error: {result['error']} | attempts: {result['attempts_log']}",
            }]
        }

    data = result["data"]
    findings = []

    if data["conversion_pct"] is not None:
        findings.append({
            "agent": "operations",
            "claim": "Overall conversion rate (transactions / footfall)",
            "number": data["conversion_pct"],
            "evidence": f"excludes {data['dead_sensor_days_excluded']} dead-sensor day(s) from the denominator",
        })

    if data["peak_hour"] is not None:
        findings.append({
            "agent": "operations",
            "claim": f"Peak demand hour is {data['peak_hour']}:00",
            "number": data["peak_hour_transactions"],
            "evidence": "count of distinct transactions grouped by hour of day",
        })

    if data["stale_employees"]:
        findings.append({
            "agent": "operations",
            "claim": f"{len(data['stale_employees'])} employee(s) have no shifts in the last 30 days of the dataset",
            "number": len(data["stale_employees"]),
            "evidence": f"employee_id(s): {data['stale_employees']} — likely departed, affects average staffing calculations if not excluded",
        })

    if data["waste_pct_of_orders"] is not None:
        findings.append({
            "agent": "operations",
            "claim": "Waste as a percentage of units ordered",
            "number": data["waste_pct_of_orders"],
            "evidence": f"only counts weeks where waste was actually recorded; {data['weeks_with_unrecorded_waste']} week/SKU rows had no recorded value and were excluded, not treated as zero",
        })

    return {"findings": findings}


if __name__ == "__main__":
    from mock_data.mock_cleaned_data import write_mock_clean_data_dir

    fake_state = {"clean_data_dir": write_mock_clean_data_dir()}
    output = operations_agent(fake_state)
    for f in output["findings"]:
        print(f)
        print()