"""Generate the CSV files that seed the trade-in Google Sheet.

Writes two files that map 1:1 onto the two tabs of the sheet:

    data/devices.csv     -> "Devices" tab
    data/conditions.csv  -> "Conditions" tab

The Devices tab holds one row per (device_id, condition) pair, so a chatbot
lookup of manufacturer + model + storage + condition always resolves to exactly
one row. final_price_eur is written as a spreadsheet formula, not a literal, so
the sheet keeps recalculating after anyone edits a base price by hand.

Run:  python tools/generate_sample_data.py
"""

import csv
import os

# Condition -> multiplier. Keep in sync with the Conditions tab.
CONDITIONS = [
    ("Like New", 1.00, "No visible wear, screen and body flawless, fully functional."),
    ("Intact",   0.85, "Light scratches or scuffs, no cracks, fully functional."),
    ("Cracked",  0.60, "Cracked screen or back glass, device still powers on and works."),
    ("Faulty",   0.20, "Does not power on, or a major fault (battery, board, water damage)."),
]

# device_id, manufacturer, model_name, storage, release_year, base_price_eur
# base_price_eur is the "Like New" trade-in value in EUR.
DEVICES = [
    ("APL-IP15P-128",  "Apple",    "iPhone 15 Pro",       "128GB", 2023, 520),
    ("APL-IP15P-256",  "Apple",    "iPhone 15 Pro",       "256GB", 2023, 590),
    ("APL-IP14P-128",  "Apple",    "iPhone 14 Pro",       "128GB", 2022, 400),
    ("APL-IP14-256",   "Apple",    "iPhone 14",           "256GB", 2022, 330),
    ("APL-IP13-128",   "Apple",    "iPhone 13",           "128GB", 2021, 240),
    ("APL-IPSE3-64",   "Apple",    "iPhone SE (3rd gen)", "64GB",  2022, 95),
    ("SAM-S24U-256",   "Samsung",  "Galaxy S24 Ultra",    "256GB", 2024, 480),
    ("SAM-S23-128",    "Samsung",  "Galaxy S23",          "128GB", 2023, 250),
    ("SAM-S23-256",    "Samsung",  "Galaxy S23",          "256GB", 2023, 285),
    ("SAM-ZFLIP5-256", "Samsung",  "Galaxy Z Flip5",      "256GB", 2023, 260),
    ("SAM-A54-128",    "Samsung",  "Galaxy A54",          "128GB", 2023, 110),
    ("GOO-P8P-128",    "Google",   "Pixel 8 Pro",         "128GB", 2023, 300),
    ("GOO-P7A-128",    "Google",   "Pixel 7a",            "128GB", 2023, 130),
    ("ONE-OP12-256",   "OnePlus",  "OnePlus 12",          "256GB", 2024, 320),
    ("ONE-NORD3-128",  "OnePlus",  "Nord 3",              "128GB", 2023, 140),
]

DEVICE_HEADER = [
    "device_id", "manufacturer", "model_name", "storage", "release_year",
    "base_price_eur", "condition", "price_multiplier", "final_price_eur",
    "active", "last_updated",
]

LAST_UPDATED = "2026-09-17"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def write_devices(path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(DEVICE_HEADER)
        row_number = 1  # header occupies row 1
        for device_id, manufacturer, model, storage, year, base in DEVICES:
            for condition, multiplier, _desc in CONDITIONS:
                row_number += 1
                writer.writerow([
                    device_id, manufacturer, model, storage, year,
                    base, condition, f"{multiplier:.2f}",
                    # Column F = base_price_eur, column H = price_multiplier.
                    f"=ROUND(F{row_number}*H{row_number},0)",
                    "TRUE", LAST_UPDATED,
                ])
    return row_number - 1


def write_conditions(path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["condition", "price_multiplier", "description"])
        for condition, multiplier, desc in CONDITIONS:
            writer.writerow([condition, f"{multiplier:.2f}", desc])


if __name__ == "__main__":
    devices_path = os.path.join(ROOT, "data", "devices.csv")
    conditions_path = os.path.join(ROOT, "data", "conditions.csv")
    count = write_devices(devices_path)
    write_conditions(conditions_path)
    print(f"Wrote {count} rows to {devices_path}")
    print(f"Wrote {len(CONDITIONS)} rows to {conditions_path}")
