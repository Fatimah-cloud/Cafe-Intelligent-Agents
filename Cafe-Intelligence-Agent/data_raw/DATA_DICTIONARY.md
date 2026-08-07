# Qahwa Saihat — Multi-Source Dataset

Six months of operating data from a neighbourhood specialty cafe in Saihat, Qatif, Eastern Province.
**5 Jan 2026 → 26 Jul 2026.** Currency SAR. Timezone Asia/Riyadh. Weekend is Friday–Saturday.

This is what a real cafe's data actually looks like: several systems that don't talk to each other,
inconsistent naming, and nobody cleaning anything. Your agent ingests all of it.

---

## Files

### `pos_transactions.csv` — ~66,000 rows
One row per **line item**, not per transaction. Multiple rows share a `transaction_id`.

| column | notes |
|---|---|
| `transaction_id` | `TXN-######`. Not unique across rows — one per item in the basket. |
| `timestamp` | Mostly `YYYY-MM-DD HH:MM:SS`. **~1.5% use `DD-Mon-YYYY HH:MM`.** |
| `sku` | Join key to `menu_items.csv`. Always present and always correct. |
| `item_name` | **Unreliable.** ~2.5% null, ~3% in Arabic, ~4% uppercased with trailing space. |
| `quantity` | **Negative = refund.** |
| `unit_price_sar`, `discount_sar`, `line_total_sar` | |
| `payment_method` | mada / apple_pay / cash / visa |
| `channel` | dine_in / takeaway / delivery |
| `cashier_id` | ~8% blank. Joins to `staff_shifts.csv`. |

**Known issue:** ~1% of transactions were double-swiped — the *entire* transaction appears twice,
every line duplicated. Deduplicate before you compute revenue or you will overstate it.

### `menu_items.csv` — 19 rows
`sku`, `item_en`, `item_ar`, `category`, `price_sar`, `unit_cost_sar`, `is_iced`, `launch_date`, `retire_date`.
Use this to repair `item_name` and to compute margin. One item has a launch date mid-period.

### `foot_traffic.csv`
Hourly door counter: `date`, `hour`, `door_count`. Compare against transactions to get conversion rate.
**The sensor was dead for three days in June and logged zeros** — that is not zero footfall.

### `staff_shifts.csv`
`date`, `employee_id`, `name`, `role`, `shift_start`, `shift_end`, `hours`, `hourly_rate_sar`.
Gives you labour cost per hour and staff-on-floor per hour. One employee's last shift is mid-March.

### `inventory_weekly.xlsx`
Sheet `weekly_counts`: `week_starting`, `sku`, `item`, `units_ordered`, `units_sold`, `units_wasted`, `unit_cost_sar`.
**`week_starting` is written in two different date formats.** Blank `units_wasted` means not recorded,
which is not the same as zero. Second sheet is a human note.

### `supplier_emails/` — 13 `.txt` files
Plain-text emails: `From`, `Date`, `Subject`, body. Milk and coffee price changes, delivery delays,
packaging quotes, and two local event announcements. Some are noise. **At least one materially
changes your unit economics** — find it and quantify the effect on margin.

### `customer_reviews.json` — 520 reviews
`review_id`, `date`, `source` (google / instagram / talabat), `rating` 1–5, `text` in Arabic or English.
Your agent must handle both languages.

### `cafe_profile.json`
Name, coordinates, opening hours (including different Ramadan hours), social handles, seats.
Use this as the config that makes your system generic — **another cafe should be onboarded by
swapping this file, not by editing your code.**

---

## Calendar context you'll need

| | dates |
|---|---|
| Ramadan | ~17 Feb → 19 Mar 2026 (opening hours change to 14:00–01:00) |
| Eid al-Fitr | ~20–23 Mar 2026 |
| Founding Day | 22 Feb 2026 |
| Eid al-Adha | ~27–30 May 2026 |
| Summer heat | June–July, daytime 40°C+ |

---

## What's in here

There are **at least eight** real, verifiable findings buried in this data. Some need only one file.
**At least three require joining two or more sources** — a POS trend that is only explained by
the staff roster, the inventory sheet, or an email.

We are not listing them. Finding them is the job. But calibrate your ambition:
a good agent surfaces four or five with correct numbers attached. A bad one invents twelve.

**Every claim your agent makes must trace back to a number it actually computed.**
