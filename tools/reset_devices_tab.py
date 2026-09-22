"""Clear the data rows from the Devices tab, keeping the header and formatting.

Deliberately NOT part of `scraper.sync`. The sync never deletes anything, and
that guarantee is what makes it safe to run unattended. Wiping rows is a
one-off, human-initiated act -- normally done once, to clear the Phase 1 sample
devices before the first real sync -- so it lives in its own script that has to
be asked for by name.

    python tools/reset_devices_tab.py                 # show what would go
    python tools/reset_devices_tab.py --yes           # actually delete
    python tools/reset_devices_tab.py --yes --force   # delete unrecognised rows too

By default it refuses to run if the tab holds anything other than the known
Phase 1 sample devices, so it cannot quietly destroy real pricing data later.
`--force` overrides that, and says so loudly.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scraper"))

import config      # noqa: E402
import sheets      # noqa: E402

SAMPLE_CSV = os.path.join(ROOT, "data", "devices.csv")


def sample_device_ids():
    """The device_ids of the generated Phase 1 sample data."""
    try:
        with open(SAMPLE_CSV, newline="", encoding="utf-8") as fh:
            return {row["device_id"].strip() for row in csv.DictReader(fh)}
    except (OSError, KeyError):
        return set()


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python tools/reset_devices_tab.py",
        description="Clear the Devices tab's data rows, keeping the header row.",
    )
    parser.add_argument("--yes", action="store_true",
                        help="actually delete; without it this is a preview")
    parser.add_argument("--force", action="store_true",
                        help="delete even rows that are not Phase 1 sample data")
    args = parser.parse_args(argv)

    if not config.SPREADSHEET_ID:
        print("No spreadsheet ID. Set TRADEIN_SPREADSHEET_ID, or SPREADSHEET_ID in\n"
              "scraper/config.py. It is the long string in the sheet's URL:\n"
              "  docs.google.com/spreadsheets/d/THIS_PART/edit")
        return 4

    writer = sheets.SheetWriter(config.SPREADSHEET_ID, config.CREDENTIALS_PATH,
                                devices_tab=config.DEVICES_TAB)
    try:
        writer.connect()
        rows = writer.read_devices()
    except sheets.SheetError as err:
        print(f"Could not read the sheet: {err}")
        return 3

    data_rows = [r for r in rows[1:] if r and str(r[0]).strip()]
    if not data_rows:
        print(f"The {config.DEVICES_TAB} tab already has no data rows. Nothing to do.")
        return 0

    known = sample_device_ids()
    present = {str(r[0]).strip() for r in data_rows}
    unknown = present - known

    devices = sorted({(str(r[1]).strip(), str(r[2]).strip(), str(r[3]).strip())
                      for r in data_rows if len(r) > 3})

    print(f"{config.DEVICES_TAB} tab: {len(data_rows)} data rows, "
          f"{len(present)} device ids, {len(devices)} device variants")
    print()
    for manufacturer, model, storage in devices:
        print(f"   {manufacturer:10s} {model:24s} {storage}")
    print()

    if unknown and not args.force:
        print(f"REFUSING: {len(unknown)} device id(s) here are not Phase 1 sample data:")
        for device_id in sorted(unknown)[:10]:
            print(f"   {device_id}")
        if len(unknown) > 10:
            print(f"   ... and {len(unknown) - 10} more")
        print()
        print("That looks like real pricing data, not sample rows. Re-run with --force\n"
              "if you genuinely mean to delete it.")
        return 1

    if not args.yes:
        print(f"PREVIEW ONLY. Re-run with --yes to delete these {len(data_rows)} rows.")
        print("The header row, column formatting and the condition dropdown are kept.")
        return 0

    if unknown:
        print(f"--force given: deleting {len(unknown)} unrecognised device id(s) as well.")

    last_row = len(rows)
    worksheet = writer._worksheet(config.DEVICES_TAB)
    # Clear values rather than deleting rows, so column formatting, the
    # protected-range warning and the data validation dropdown all survive.
    worksheet.batch_clear([f"A2:K{last_row}"])
    print(f"Cleared A2:K{last_row}. The header row is untouched.")
    print("Now run:  python -m scraper.sync --dry-run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
