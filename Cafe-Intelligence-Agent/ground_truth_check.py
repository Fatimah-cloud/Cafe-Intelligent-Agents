import pandas as pd
from load_real_data import load_source_for_week, week_id_to_end_date
from content.waste_analysis import compute_waste_report

we = week_id_to_end_date('2026-W29')

# Claim 1: best seller avg weekly revenue
pos = load_source_for_week('clean_data', 'pos', we, 12)
pos = pos[pos['is_refund'] == False]
menu = pd.read_csv('clean_data/menu_items.csv')
menu['launch_date'] = pd.to_datetime(menu['launch_date'], errors='coerce')
menu['retire_date'] = pd.to_datetime(menu['retire_date'], errors='coerce')
merged = pos.merge(menu[['sku', 'item_en', 'launch_date', 'retire_date']], on='sku', how='left')
merged['timestamp'] = pd.to_datetime(merged['timestamp'])
data_start, data_end = merged['timestamp'].min(), merged['timestamp'].max()

def weeks_active(item_en):
    row = menu[menu['item_en'] == item_en]
    launch, retire = row['launch_date'].iloc[0], row['retire_date'].iloc[0]
    start = max(launch, data_start) if pd.notna(launch) else data_start
    end = min(retire, data_end) if pd.notna(retire) else data_end
    return max((end - start).days, 7) / 7

total = merged.groupby('item_en')['line_total_sar'].sum()
avg_weekly = {item: total[item] / weeks_active(item) for item in total.index}
print("Claim 1 - best seller avg weekly revenue:")
print(pd.Series(avg_weekly).sort_values(ascending=False).head(3))
print()

# Claim 2: conversion rate
traffic = load_source_for_week('clean_data', 'traffic', we, 12)
good = traffic[~traffic.get('sensor_dead', False)]
daily_traffic = good.groupby(pd.to_datetime(good['date']).dt.date)['door_count'].sum()
pos2 = pos.copy()
pos2['date'] = pd.to_datetime(pos2['timestamp']).dt.date
daily_tx = pos2.groupby('date')['transaction_id'].nunique()
common = daily_traffic.index.intersection(daily_tx.index)
conv = daily_tx.loc[common].sum() / daily_traffic.loc[common].sum() * 100
print(f"Claim 2 - conversion rate: {conv:.1f}%")
print()

# Claim 3: average rating
reviews = load_source_for_week('clean_data', 'reviews', we, 12)
print(f"Claim 3 - avg rating: {reviews['rating'].mean():.2f}, n={len(reviews)}")
print()

# Claim 4: naive margin
merged2 = pos.merge(menu[['sku', 'unit_cost_sar']], on='sku', how='left')
merged2['cost_total'] = merged2['quantity'].abs() * merged2['unit_cost_sar']
revenue = merged2['line_total_sar'].sum()
cost = merged2['cost_total'].sum()
print(f"Claim 4 - naive margin: {(revenue - cost) / revenue * 100:.1f}% (revenue={revenue:.0f}, cost={cost:.0f})")
print()

# Claim 5: total monthly waste
lines = compute_waste_report('clean_data', we)
print(f"Claim 5 - total monthly waste: SAR {sum(l.monthly_waste_cost_sar for l in lines):.2f}")