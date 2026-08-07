"""
Mock cleaned_data for testing analysts before Person 1 delivers real data.
Shape matches Person 1's ACTUAL delivered format:
    cleaned_data = {
        "pos": DataFrame,
        "menu": DataFrame,
        "traffic": DataFrame,
        "staff": DataFrame,
        "inventory": DataFrame,
        "supplier_emails": DataFrame,  # raw text: record_id, date, from, subject, body
        "reviews": DataFrame,
    }
This is intentionally small (a handful of rows) — just enough to exercise
each analyst's logic. Swap for real cleaned_data (via load_real_data.py)
once available; the analyst code should not need to change if it only
relies on column names.
"""
import pandas as pd


def get_mock_cleaned_data() -> dict:
    pos = pd.DataFrame([
        # A full baseline week (06-01 -> 06-07) with modest, stable daily
        # revenue (~18-36 SAR/day), so 06-08 -- the Matcha launch day, with
        # a big revenue jump -- becomes a genuine statistical spike the
        # anomaly detector should catch, not a hand-picked example.
        {"transaction_id": "TXN-1", "timestamp": "2026-06-01 08:00:00", "sku": "HOT-001", "quantity": 1, "unit_price_sar": 18.0, "line_total_sar": 18.0, "channel": "dine_in"},
        {"transaction_id": "TXN-2", "timestamp": "2026-06-01 09:00:00", "sku": "ICE-003", "quantity": 2, "unit_price_sar": 18.0, "line_total_sar": 36.0, "channel": "takeaway"},
        {"transaction_id": "TXN-3", "timestamp": "2026-06-02 08:30:00", "sku": "HOT-001", "quantity": 1, "unit_price_sar": 18.0, "line_total_sar": 18.0, "channel": "dine_in"},
        {"transaction_id": "TXN-7", "timestamp": "2026-06-03 08:00:00", "sku": "HOT-001", "quantity": 1, "unit_price_sar": 18.0, "line_total_sar": 18.0, "channel": "dine_in"},
        {"transaction_id": "TXN-8", "timestamp": "2026-06-04 08:15:00", "sku": "ICE-003", "quantity": 1, "unit_price_sar": 18.0, "line_total_sar": 18.0, "channel": "takeaway"},
        {"transaction_id": "TXN-9", "timestamp": "2026-06-05 08:30:00", "sku": "HOT-001", "quantity": 1, "unit_price_sar": 18.0, "line_total_sar": 18.0, "channel": "dine_in"},
        {"transaction_id": "TXN-10", "timestamp": "2026-06-06 08:00:00", "sku": "ICE-003", "quantity": 1, "unit_price_sar": 18.0, "line_total_sar": 18.0, "channel": "takeaway"},
        {"transaction_id": "TXN-11", "timestamp": "2026-06-07 08:20:00", "sku": "HOT-001", "quantity": 1, "unit_price_sar": 18.0, "line_total_sar": 18.0, "channel": "dine_in"},
        {"transaction_id": "TXN-4", "timestamp": "2026-06-08 08:00:00", "sku": "HOT-001", "quantity": 3, "unit_price_sar": 18.0, "line_total_sar": 54.0, "channel": "dine_in"},
        {"transaction_id": "TXN-5", "timestamp": "2026-06-08 09:15:00", "sku": "ICE-003", "quantity": 1, "unit_price_sar": 18.0, "line_total_sar": 18.0, "channel": "takeaway"},
        # Matcha only sold on its one active day (launched 06-08) — low RAW
        # total, but should NOT be flagged "worst seller" once normalized
        # by weeks actually on the menu. This same day's total revenue
        # (172 SAR vs. a ~22 SAR baseline) is what the anomaly agent
        # should flag as a spike.
        {"transaction_id": "TXN-6", "timestamp": "2026-06-08 10:00:00", "sku": "MAT-001", "quantity": 5, "unit_price_sar": 20.0, "line_total_sar": 100.0, "channel": "dine_in"},
    ])

    menu = pd.DataFrame([
        {"sku": "HOT-001", "item_en": "Spanish Latte", "price_sar": 18.0, "unit_cost_sar": 5.2, "category": "hot_coffee", "launch_date": None, "retire_date": None},
        {"sku": "ICE-003", "item_en": "Iced Latte", "price_sar": 18.0, "unit_cost_sar": 5.0, "category": "iced_coffee", "launch_date": None, "retire_date": None},
        {"sku": "MAT-001", "item_en": "Matcha Latte", "price_sar": 20.0, "unit_cost_sar": 6.5, "category": "iced_coffee", "launch_date": "2026-06-08", "retire_date": None},
    ])

    traffic = pd.DataFrame([
        {"date": "2026-06-01", "hour": 8, "door_count": 20, "sensor_dead": False},
        {"date": "2026-06-01", "hour": 9, "door_count": 35, "sensor_dead": False},
        {"date": "2026-06-08", "hour": 8, "door_count": 40, "sensor_dead": False},
        {"date": "2026-06-08", "hour": 9, "door_count": 30, "sensor_dead": False},
        # A dead-sensor day: zeros here are NOT real footfall and must be
        # excluded from conversion calculations, per Person 1's flag.
        {"date": "2026-06-09", "hour": 8, "door_count": 0, "sensor_dead": True},
        {"date": "2026-06-09", "hour": 9, "door_count": 0, "sensor_dead": True},
    ])

    staff = pd.DataFrame([
        {"date": "2026-06-01", "employee_id": "EMP-01", "role": "barista", "shift_start": "07:00", "shift_end": "15:00", "hours": 8, "hourly_rate_sar": 24},
        {"date": "2026-06-08", "employee_id": "EMP-01", "role": "barista", "shift_start": "07:00", "shift_end": "15:00", "hours": 8, "hourly_rate_sar": 24},
        # A stale employee: no shifts near the end of the dataset window.
        {"date": "2026-04-01", "employee_id": "EMP-02", "role": "cashier", "shift_start": "08:00", "shift_end": "16:00", "hours": 8, "hourly_rate_sar": 20},
    ])

    inventory = pd.DataFrame([
        {"week_starting": "2026-06-01", "sku": "HOT-001", "units_ordered": 50, "units_sold": 40, "units_wasted": 5, "unit_cost_sar": 5.2, "waste_recorded": True},
        {"week_starting": "2026-06-08", "sku": "HOT-001", "units_ordered": 55, "units_sold": 45, "units_wasted": None, "unit_cost_sar": 5.2, "waste_recorded": False},
    ])

    # Matches Person 1's ACTUAL output from parsers/email_parser.py: raw
    # text fields only (record_id, date, from, subject, body). Extracting
    # price-change info from this text is this agent's own job, not
    # something Person 1 pre-parses.
    supplier_emails = pd.DataFrame([
        {
            "record_id": "2026-04-01_05.txt",
            "date": "2026-04-01",
            "from": "sales@easternbeans.sa",
            "subject": "Price notice — Q2 green coffee",
            "body": (
                "Due to shipping costs, roasted price moves from SAR 88/kg "
                "to SAR 96/kg effective 15 April.\nHouse blend moves from "
                "SAR 62/kg to SAR 67/kg."
            ),
        },
        {
            "record_id": "2026-05-04_07.txt",
            "date": "2026-05-04",
            "from": "orders@qatifdairy.com",
            "subject": "IMPORTANT: price increase effective 1 May",
            "body": (
                "Due to feed and transport costs, full-fat milk moves from "
                "SAR 7.10/L to SAR 8.40/L effective 1 May 2026.\nBarista oat "
                "moves from SAR 15.40/L to SAR 17.90/L."
            ),
        },
        {
            "record_id": "2026-06-09_10.txt",
            "date": "2026-06-09",
            "from": "orders@qatifdairy.com",
            "subject": "Delivery delay — 8 to 10 June",
            "body": (
                "Apologies, our refrigerated vehicle is under repair. "
                "Deliveries on 8-10 June will arrive late afternoon instead "
                "of morning."
            ),
        },
    ])

    # Matches Person 1's ACTUAL columns from parsers/json_parser.py:
    # record_id, date, source_platform, rating, text, language (ar/en)
    reviews = pd.DataFrame([
        {"record_id": "REV-1", "date": "2026-06-02", "source_platform": "google", "rating": 5, "text": "Best Spanish latte in Saihat", "language": "en"},
        {"record_id": "REV-2", "date": "2026-06-05", "source_platform": "talabat", "rating": 2, "text": "Slow service today, waited 20 minutes", "language": "en"},
        {"record_id": "REV-3", "date": "2026-06-06", "source_platform": "google", "rating": 4, "text": "الجو حلو بس الموسيقى صوتها عالي", "language": "ar"},
        {"record_id": "REV-4", "date": "2026-06-07", "source_platform": "instagram", "rating": 1, "text": "الخدمة بطيئة جدا اليوم، انتظرت طويل", "language": "ar"},
    ])

    return {
        "pos": pos,
        "menu": menu,
        "traffic": traffic,
        "staff": staff,
        "inventory": inventory,
        "supplier_emails": supplier_emails,
        "reviews": reviews,
    }


def write_mock_clean_data_dir() -> str:
    """
    Writes the mock DataFrames to CSV files in a temp directory, using the
    exact same filenames Person 1's real clean_data/ folder uses. This
    lets graph.py treat mock and real data identically -- both are just a
    clean_data_dir path -- instead of testing needing a separate in-memory
    code path from production.
    """
    import tempfile
    import os

    data = get_mock_cleaned_data()
    tmp_dir = os.path.join(tempfile.gettempdir(), "mock_clean_data")
    os.makedirs(tmp_dir, exist_ok=True)

    filenames = {
        "menu": "menu_items.csv",
        "pos": "pos_transactions.csv",
        "traffic": "foot_traffic.csv",
        "staff": "staff_shifts.csv",
        "inventory": "inventory_weekly.csv",
        "supplier_emails": "supplier_emails.csv",
        "reviews": "customer_reviews.csv",
    }
    for key, filename in filenames.items():
        data[key].to_csv(os.path.join(tmp_dir, filename), index=False)

    return tmp_dir