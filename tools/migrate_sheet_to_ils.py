"""Move the Devices tab from euro storage to native shekels.

Renames two headers and reformats two columns as shekels. It does not touch a
single price -- the sync does that on its next run, because every row's value
changes anyway and the sync already knows how to write them.

    python tools/migrate_sheet_to_ils.py          # preview
    python tools/migrate_sheet_to_ils.py --yes    # apply

Safe to re-run: a sheet already migrated reports that and changes nothing.
"""

from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scraper"))

import config      # noqa: E402
import sheets      # noqa: E402

SHEKEL_FORMAT = "₪#,##0"

RENAMES = {
    sheets.COL_BASE_PRICE: ("base_price_eur", "base_price_ils"),
    sheets.COL_FINAL_PRICE: ("final_price_eur", "final_price_ils"),
}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python tools/migrate_sheet_to_ils.py")
    parser.add_argument("--yes", action="store_true", help="apply the changes")
    args = parser.parse_args(argv)

    if not config.SPREADSHEET_ID:
        print("No spreadsheet ID configured.")
        return 4

    writer = sheets.SheetWriter(config.SPREADSHEET_ID, config.CREDENTIALS_PATH)
    try:
        spreadsheet = writer.connect()
        worksheet = spreadsheet.worksheet(config.DEVICES_TAB)
    except Exception as err:
        print(f"Could not open the {config.DEVICES_TAB} tab: {err}")
        return 3

    header = worksheet.row_values(1)
    print(f"spreadsheet : {spreadsheet.title}")
    print(f"header now  : {header}")
    print()

    todo = []
    for column, (old_name, new_name) in RENAMES.items():
        current = header[column - 1] if len(header) >= column else ""
        if current.strip() == new_name:
            print(f"  column {_letter(column)}: already {new_name}")
        elif current.strip() == old_name:
            print(f"  column {_letter(column)}: {old_name} -> {new_name}")
            todo.append((column, new_name))
        else:
            print(f"  column {_letter(column)}: unexpected header {current!r} -- "
                  f"expected {old_name!r} or {new_name!r}")
            print("\nREFUSING: the sheet is not in a shape this migration understands.")
            return 1

    print(f"  columns {_letter(sheets.COL_BASE_PRICE)} and "
          f"{_letter(sheets.COL_FINAL_PRICE)}: number format -> {SHEKEL_FORMAT}")
    print()

    if not args.yes:
        print("PREVIEW ONLY. Re-run with --yes to apply.")
        print("No prices are touched here; the next sync rewrites them in shekels.")
        return 0

    for column, new_name in todo:
        worksheet.update_cell(1, column, new_name)
        print(f"renamed column {_letter(column)} -> {new_name}")

    requests = [{
        "repeatCell": {
            "range": {"sheetId": worksheet.id, "startRowIndex": 1,
                      "startColumnIndex": column - 1, "endColumnIndex": column},
            "cell": {"userEnteredFormat": {
                "numberFormat": {"type": "CURRENCY", "pattern": SHEKEL_FORMAT}}},
            "fields": "userEnteredFormat.numberFormat",
        }
    } for column in (sheets.COL_BASE_PRICE, sheets.COL_FINAL_PRICE)]

    try:
        spreadsheet.batch_update({"requests": requests})
        print("number formats set to shekels")
    except Exception as err:
        print(f"number formatting skipped ({err}) -- the data is unaffected")

    print()
    print("Now run:  python -m scraper.sync --from-file scraper/fixtures/ksp_api_devices.json --dry-run")
    return 0


def _letter(column):
    return chr(ord("A") + column - 1)


if __name__ == "__main__":
    sys.exit(main())
