"""Create the Devices and Conditions tabs the sync expects, in an empty sheet.

Use this when the Google Sheet was created fresh rather than by uploading
device-trade-in-pricing.xlsx. It builds the same structure that workbook would
have: the Phase 1 column header, the four conditions, a frozen bold header row,
euro number formats and a condition dropdown.

    python tools/init_sheet_tabs.py            # preview
    python tools/init_sheet_tabs.py --yes      # create

Safe to re-run: it never touches a tab that already has data rows.
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

CONDITIONS = [
    ("Like New", 1.00, "No visible wear, screen and body flawless, fully functional."),
    ("Intact", 0.85, "Light scratches or scuffs, no cracks, fully functional."),
    ("Cracked", 0.60, "Cracked screen or back glass, device still powers on and works."),
    ("Faulty", 0.20, "Does not power on, or a major fault (battery, board, water damage)."),
]

HEADER_BLUE = {"red": 0.122, "green": 0.212, "blue": 0.392}
WHITE = {"red": 1.0, "green": 1.0, "blue": 1.0}


def ensure_tab(spreadsheet, title, first_tab_fallback=None):
    """Return (worksheet, existing_data_rows)."""
    try:
        worksheet = spreadsheet.worksheet(title)
    except Exception:
        if first_tab_fallback is not None:
            # Reuse the default empty tab rather than leaving it lying around.
            worksheet = first_tab_fallback
            worksheet.update_title(title)
        else:
            worksheet = spreadsheet.add_worksheet(title, rows=2000, cols=12)
    values = worksheet.get_all_values()
    data_rows = [r for r in values[1:] if r and any(str(c).strip() for c in r)]
    return worksheet, len(data_rows)


def style_requests(sheet_id, columns, conditions_sheet_id=None, condition_column=None):
    """Frozen bold header, euro formats and a condition dropdown."""
    requests = [
        {"updateSheetProperties": {
            "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
            "fields": "gridProperties.frozenRowCount"}},
        {"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {"userEnteredFormat": {
                "backgroundColor": HEADER_BLUE,
                "horizontalAlignment": "CENTER",
                "textFormat": {"foregroundColor": WHITE, "bold": True,
                               "fontFamily": "Arial", "fontSize": 11}}},
            "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,textFormat)"}},
        {"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                      "startIndex": 0, "endIndex": columns},
            "properties": {"pixelSize": 140}, "fields": "pixelSize"}},
    ]

    # Euro format on base_price_eur (F) and final_price_eur (I).
    for index in (5, 8):
        requests.append({"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 1,
                      "startColumnIndex": index, "endColumnIndex": index + 1},
            "cell": {"userEnteredFormat": {
                "numberFormat": {"type": "CURRENCY", "pattern": "€#,##0"}}},
            "fields": "userEnteredFormat.numberFormat"}})

    # Four decimals on price_multiplier (H) -- KSP's real ratios need them.
    requests.append({"repeatCell": {
        "range": {"sheetId": sheet_id, "startRowIndex": 1,
                  "startColumnIndex": 7, "endColumnIndex": 8},
        "cell": {"userEnteredFormat": {
            "numberFormat": {"type": "NUMBER", "pattern": "0.0000"}}},
        "fields": "userEnteredFormat.numberFormat"}})

    if conditions_sheet_id is not None and condition_column is not None:
        requests.append({"setDataValidation": {
            "range": {"sheetId": sheet_id, "startRowIndex": 1,
                      "startColumnIndex": condition_column,
                      "endColumnIndex": condition_column + 1},
            "rule": {
                "condition": {"type": "ONE_OF_RANGE", "values": [
                    {"userEnteredValue": f"=Conditions!$A$2:$A${len(CONDITIONS) + 1}"}]},
                "strict": True, "showCustomUi": True,
                "inputMessage": "One of the four conditions on the Conditions tab."}}})

    return requests


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python tools/init_sheet_tabs.py")
    parser.add_argument("--yes", action="store_true", help="create the tabs")
    args = parser.parse_args(argv)

    if not config.SPREADSHEET_ID:
        print("No spreadsheet ID. Set TRADEIN_SPREADSHEET_ID or SPREADSHEET_ID "
              "in scraper/config.py.")
        return 4

    writer = sheets.SheetWriter(config.SPREADSHEET_ID, config.CREDENTIALS_PATH)
    try:
        spreadsheet = writer.connect()
    except sheets.SheetError as err:
        print(f"Could not open the spreadsheet: {err}")
        return 3

    existing = [ws.title for ws in spreadsheet.worksheets()]
    print(f"spreadsheet : {spreadsheet.title}")
    print(f"tabs now    : {existing}")
    print(f"will ensure : {config.DEVICES_TAB!r}, {config.CONDITIONS_TAB!r}")
    print()

    if not args.yes:
        print("PREVIEW ONLY. Re-run with --yes to create them.")
        print("Nothing with data rows is ever modified.")
        return 0

    default_tab = None
    if len(existing) == 1 and existing[0] not in (config.DEVICES_TAB, config.CONDITIONS_TAB):
        candidate = spreadsheet.worksheet(existing[0])
        if not [r for r in candidate.get_all_values() if any(str(c).strip() for c in r)]:
            default_tab = candidate
            print(f"reusing the empty default tab {existing[0]!r} as "
                  f"{config.DEVICES_TAB!r}")

    devices, devices_rows = ensure_tab(spreadsheet, config.DEVICES_TAB, default_tab)
    conditions, conditions_rows = ensure_tab(spreadsheet, config.CONDITIONS_TAB)

    if devices_rows:
        print(f"{config.DEVICES_TAB}: {devices_rows} data rows already -- left alone")
    else:
        devices.update([sheets.HEADER], "A1", value_input_option="USER_ENTERED")
        print(f"{config.DEVICES_TAB}: header written ({len(sheets.HEADER)} columns)")

    if conditions_rows:
        print(f"{config.CONDITIONS_TAB}: {conditions_rows} data rows already -- left alone")
    else:
        conditions.update(
            [["condition", "price_multiplier", "description"]]
            + [[name, multiplier, description] for name, multiplier, description in CONDITIONS],
            "A1", value_input_option="USER_ENTERED")
        print(f"{config.CONDITIONS_TAB}: header + {len(CONDITIONS)} conditions written")

    requests = style_requests(devices.id, len(sheets.HEADER),
                              conditions_sheet_id=conditions.id,
                              condition_column=sheets.COL_CONDITION - 1)
    requests += style_requests(conditions.id, 3)
    try:
        spreadsheet.batch_update({"requests": requests})
        print("formatting applied: frozen header, euro formats, condition dropdown")
    except Exception as err:
        print(f"formatting skipped ({type(err).__name__}: {err}) -- the data is fine")

    print()
    print("Next:  python -m scraper.sync --dry-run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
