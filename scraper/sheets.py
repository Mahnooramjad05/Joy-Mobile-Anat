"""Write scraped prices into the Google Sheet, in shekels.

Three rules govern everything here, and they are what keep a bad scrape from
costing you your pricing data:

  1. Rows are never deleted. A device that disappears from the source is
     deactivated (column J) so its history and its device_id survive.
  2. Column I (the final_price formula) and column G (condition) are never
     written on an existing row, so the scraper can never replace a formula
     with a literal or relabel a condition. It writes F (base_price_ils),
     H (price_multiplier), J (active) and K (last_updated).

     Since the ILS migration, F holds the price for that row's own condition
     and H is always 1.0, so column I simply echoes F. KSP quotes every
     condition independently, so storing each quoted price directly is both
     simpler than a base-times-ratio model and exactly accurate -- no rounding
     sits between KSP's number and the sheet.
  3. Devices are matched on normalised manufacturer + model + storage, not on
     device_id. Hand-written IDs from Phase 1 therefore survive, and a device
     already in the sheet never gets a second set of rows.

plan_changes() is a pure function -- it takes rows and returns a plan -- so the
upsert logic is testable without credentials or a network.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

import normalize

log = logging.getLogger(__name__)

# Devices tab column positions (1-indexed), matching the Phase 1 schema.
COL_DEVICE_ID = 1
COL_MANUFACTURER = 2
COL_MODEL = 3
COL_STORAGE = 4
COL_RELEASE_YEAR = 5
COL_BASE_PRICE = 6
COL_CONDITION = 7
COL_MULTIPLIER = 8
COL_FINAL_PRICE = 9
COL_ACTIVE = 10
COL_LAST_UPDATED = 11

# Google's Sheets API rejects very large single requests; these keep one sync
# inside comfortable limits even on a first run that adds the whole catalogue.
UPDATE_CHUNK = 500
APPEND_CHUNK = 500

HEADER = [
    "device_id", "manufacturer", "model_name", "storage", "release_year",
    "base_price_ils", "condition", "price_multiplier", "final_price_ils",
    "active", "last_updated",
]

# The header this replaced. Recognised so a sheet that has not been migrated
# yet produces a clear message instead of a confusing mismatch.
LEGACY_HEADER = [
    "device_id", "manufacturer", "model_name", "storage", "release_year",
    "base_price_eur", "condition", "price_multiplier", "final_price_eur",
    "active", "last_updated",
]

# Every row stores its own condition price, so the multiplier is vestigial.
# It stays in the schema because the sheet formula and the Phase 1 layout
# reference it, and because a future source without per-condition prices could
# use it again.
FIXED_MULTIPLIER = 1.0


class SheetError(RuntimeError):
    """The sheet could not be read or written."""


@dataclass
class Plan:
    """What a sync would do. Produced before anything is written."""
    price_updates: list = field(default_factory=list)       # (row, device_id, old, new)
    multiplier_updates: list = field(default_factory=list)  # (row, device_id, old, new)
    reactivations: list = field(default_factory=list)       # (row, device_id)
    deactivations: list = field(default_factory=list)       # (row, device_id)
    new_devices: list = field(default_factory=list)         # (device_id, ScrapedDevice)
    unchanged: int = 0
    skipped_no_conditions: int = 0

    @property
    def is_empty(self):
        return not (self.price_updates or self.multiplier_updates or self.reactivations
                    or self.deactivations or self.new_devices)

    def summary(self):
        parts = [f"{len(self.new_devices)} new", f"{len(self.price_updates)} repriced"]
        if self.multiplier_updates:
            parts.append(f"{len(self.multiplier_updates)} multipliers changed")
        parts += [f"{len(self.reactivations)} reactivated",
                  f"{len(self.deactivations)} deactivated",
                  f"{self.unchanged} unchanged"]
        return ", ".join(parts)


def plan_changes(existing_rows, devices, conditions, *, today=None,
                 deactivate_missing=True, max_deactivation_ratio=0.30):
    """Work out what to change, without changing anything.

    existing_rows: the Devices tab including its header row, as lists of strings.
    devices:       ScrapedDevice objects, already priced in EUR.
    conditions:    [(condition_name, multiplier), ...] from the Conditions tab.
    """
    today = today or dt.date.today().isoformat()

    if not conditions:
        raise SheetError("The Conditions tab is empty -- cannot build rows for a new device.")

    # Index the sheet: match key -> the rows that share it (one per condition).
    by_key = {}
    taken_ids = set()
    for index, row in enumerate(existing_rows[1:], start=2):
        if not row or not str(row[0]).strip():
            continue
        cells = _padded(row)
        key = normalize.match_key(cells[COL_MANUFACTURER - 1],
                                  cells[COL_MODEL - 1],
                                  cells[COL_STORAGE - 1])
        by_key.setdefault(key, []).append((index, cells))
        taken_ids.add(cells[COL_DEVICE_ID - 1].strip())

    plan = Plan()
    seen_keys = set()

    default_multipliers = dict(conditions)

    for device in devices:
        key = device.key
        seen_keys.add(key)

        if key in by_key:
            for row_number, cells in by_key[key]:
                device_id = cells[COL_DEVICE_ID - 1].strip()
                row_condition = cells[COL_CONDITION - 1].strip()
                touched = False

                new_price = condition_price(device, row_condition, default_multipliers)
                if new_price is None:
                    # The sheet has a condition this source does not price.
                    # Leave the row alone rather than guess at it.
                    plan.unchanged += 1
                    continue

                old_price = _as_int(cells[COL_BASE_PRICE - 1])
                if old_price != new_price:
                    plan.price_updates.append((row_number, device_id, old_price, new_price))
                    touched = True

                old_multiplier = _as_float(cells[COL_MULTIPLIER - 1])
                if old_multiplier is None or abs(old_multiplier - FIXED_MULTIPLIER) > 1e-9:
                    plan.multiplier_updates.append(
                        (row_number, device_id, old_multiplier, FIXED_MULTIPLIER))
                    touched = True

                if not touched:
                    plan.unchanged += 1

                if cells[COL_ACTIVE - 1].strip().upper() == "FALSE":
                    plan.reactivations.append((row_number, device_id))
        else:
            device_id = normalize.make_device_id(
                device.manufacturer, device.model, device.storage, taken=taken_ids)
            taken_ids.add(device_id)
            plan.new_devices.append((device_id, device))

    if deactivate_missing:
        missing = [(key, rows) for key, rows in by_key.items() if key not in seen_keys]
        active_missing = [
            (row_number, cells[COL_DEVICE_ID - 1].strip())
            for _key, rows in missing
            for row_number, cells in rows
            if cells[COL_ACTIVE - 1].strip().upper() != "FALSE"
        ]

        total_active = sum(
            1 for rows in by_key.values() for _row, cells in rows
            if cells[COL_ACTIVE - 1].strip().upper() != "FALSE"
        )
        ratio = (len(active_missing) / total_active) if total_active else 0.0

        if ratio > max_deactivation_ratio:
            log.warning(
                "refusing to deactivate %d of %d active rows (%.0f%%, limit %.0f%%) -- "
                "that looks like a broken scrape, not a shrinking catalogue. "
                "Prices will still be updated; nothing will be deactivated.",
                len(active_missing), total_active, ratio * 100, max_deactivation_ratio * 100)
        else:
            plan.deactivations = active_missing

    return plan


def condition_price(device, condition, default_multipliers=None):
    """The ILS price for one condition of one device.

    A source that quotes each condition (KSP does) supplies it directly. For a
    source that only gives one price, the Conditions tab multiplier is applied
    here instead, so the column always means the same thing: what this device
    in this condition is worth.
    """
    quoted = (device.condition_prices or {}).get(condition)
    if quoted is not None:
        return int(round(quoted))

    multiplier = (default_multipliers or {}).get(condition)
    if multiplier is None:
        return None
    return int(round(device.price * multiplier))


def build_new_rows(device_id, device, conditions, first_row, *, today=None,
                   release_year=None):
    """Build the four sheet rows for a device that is not in the sheet yet."""
    today = today or dt.date.today().isoformat()
    year = release_year if release_year is not None else (device.release_year or "")
    default_multipliers = dict(conditions)

    rows = []
    for offset, (condition, _default_multiplier) in enumerate(conditions):
        row_number = first_row + offset
        price = condition_price(device, condition, default_multipliers)
        if price is None:
            price = int(round(device.price))
        rows.append([
            device_id, device.manufacturer, device.model, device.storage, year,
            price, condition, FIXED_MULTIPLIER,
            f"=ROUND(F{row_number}*H{row_number},0)",
            "TRUE", today,
        ])
    return rows


class SheetWriter:
    """Applies a Plan to a Google Sheet via gspread."""

    def __init__(self, spreadsheet_id, credentials_path, *,
                 devices_tab="Devices", conditions_tab="Conditions",
                 sync_log_tab="Sync Log"):
        self.spreadsheet_id = spreadsheet_id
        self.credentials_path = credentials_path
        self.devices_tab = devices_tab
        self.conditions_tab = conditions_tab
        self.sync_log_tab = sync_log_tab
        self._spreadsheet = None

    def connect(self):
        try:
            import gspread
        except ImportError as err:
            raise SheetError(
                "gspread and google-auth are required to write the sheet. "
                "Install them with: pip install -r requirements.txt"
            ) from err

        import credentials as credentials_module

        if not self.spreadsheet_id:
            raise SheetError(
                "No spreadsheet ID. Set TRADEIN_SPREADSHEET_ID, or SPREADSHEET_ID "
                "in scraper/config.py. It is the long id in the sheet's URL."
            )

        try:
            creds = credentials_module.load(self.credentials_path)
        except credentials_module.CredentialsError as err:
            raise SheetError(f"{err} See docs/scraper.md, 'Google Sheets credentials'.") from err

        try:
            self._spreadsheet = gspread.authorize(creds).open_by_key(self.spreadsheet_id)
        except Exception as err:
            raise SheetError(
                f"Could not open spreadsheet {self.spreadsheet_id}: {err}. "
                f"The usual cause is that the sheet has not been shared with the "
                f"service account's email address as an Editor."
            ) from err

        return self._spreadsheet

    def read_devices(self):
        worksheet = self._worksheet(self.devices_tab)
        rows = worksheet.get_all_values()
        if not rows:
            raise SheetError(f"The {self.devices_tab} tab is empty.")
        header = [c.strip() for c in rows[0][:len(HEADER)]]
        if header != HEADER:
            if header == LEGACY_HEADER:
                raise SheetError(
                    f"The {self.devices_tab} tab still has the euro-era header. "
                    f"Run: python tools/migrate_sheet_to_ils.py --yes"
                )
            raise SheetError(
                f"The {self.devices_tab} tab's header does not match the expected "
                f"schema. Expected: {', '.join(HEADER)}"
            )
        return rows

    def read_conditions(self):
        worksheet = self._worksheet(self.conditions_tab)
        conditions = []
        for row in worksheet.get_all_values()[1:]:
            if row and row[0].strip():
                try:
                    conditions.append((row[0].strip(), float(row[1])))
                except (IndexError, ValueError):
                    log.warning("skipping unreadable Conditions row: %r", row)
        return conditions

    def apply(self, plan, conditions, existing_row_count, *, today=None):
        """Write the plan. Returns a dict of what was written."""
        today = today or dt.date.today().isoformat()
        worksheet = self._worksheet(self.devices_tab)

        updates = []
        for row_number, _device_id, _old, new_price in plan.price_updates:
            updates.append({"range": _a1(COL_BASE_PRICE, row_number), "values": [[new_price]]})
            updates.append({"range": _a1(COL_LAST_UPDATED, row_number), "values": [[today]]})
        for row_number, _device_id, _old, new_multiplier in plan.multiplier_updates:
            updates.append({"range": _a1(COL_MULTIPLIER, row_number), "values": [[new_multiplier]]})
            updates.append({"range": _a1(COL_LAST_UPDATED, row_number), "values": [[today]]})
        for row_number, _device_id in plan.reactivations:
            updates.append({"range": _a1(COL_ACTIVE, row_number), "values": [["TRUE"]]})
        for row_number, _device_id in plan.deactivations:
            updates.append({"range": _a1(COL_ACTIVE, row_number), "values": [["FALSE"]]})
            updates.append({"range": _a1(COL_LAST_UPDATED, row_number), "values": [[today]]})

        # Chunked: a full first sync can touch thousands of cells, and one
        # enormous request is both slow and more likely to be rejected.
        for start in range(0, len(updates), UPDATE_CHUNK):
            worksheet.batch_update(updates[start:start + UPDATE_CHUNK],
                                   value_input_option="USER_ENTERED")
        if updates:
            log.info("wrote %d cell updates", len(updates))

        appended = 0
        if plan.new_devices:
            next_row = existing_row_count + 1
            rows = []
            for device_id, device in plan.new_devices:
                rows.extend(build_new_rows(device_id, device, conditions,
                                           next_row + len(rows), today=today))
            for start in range(0, len(rows), APPEND_CHUNK):
                worksheet.append_rows(rows[start:start + APPEND_CHUNK],
                                      value_input_option="USER_ENTERED",
                                      table_range=f"A{existing_row_count}")
            appended = len(rows)
            log.info("appended %d rows for %d new devices", appended, len(plan.new_devices))

        return {"cells_updated": len(updates), "rows_appended": appended}

    def log_run(self, *, status, source, devices_found, plan, message=""):
        """Append one row to the Sync Log tab, creating the tab if needed."""
        try:
            spreadsheet = self._spreadsheet or self.connect()
            try:
                worksheet = spreadsheet.worksheet(self.sync_log_tab)
            except Exception:
                worksheet = spreadsheet.add_worksheet(self.sync_log_tab, rows=1000, cols=8)
                worksheet.append_row([
                    "timestamp", "source", "status", "devices_found", "new_devices",
                    "prices_changed", "multipliers_changed", "deactivated", "message",
                ], value_input_option="USER_ENTERED")

            worksheet.append_row([
                dt.datetime.now().isoformat(timespec="seconds"),
                source, status, devices_found,
                len(plan.new_devices) if plan else 0,
                len(plan.price_updates) if plan else 0,
                len(plan.multiplier_updates) if plan else 0,
                len(plan.deactivations) if plan else 0,
                message[:500],
            ], value_input_option="USER_ENTERED")
        except Exception as err:
            # A logging failure must never mask the real outcome of the run.
            log.warning("could not write to the %s tab: %s", self.sync_log_tab, err)

    def _worksheet(self, title):
        spreadsheet = self._spreadsheet or self.connect()
        try:
            return spreadsheet.worksheet(title)
        except Exception as err:
            raise SheetError(f"No tab named {title!r} in the spreadsheet.") from err


# --------------------------------------------------------------------- helpers

def _padded(row, width=len(HEADER)):
    cells = [str(c) for c in row]
    return cells + [""] * (width - len(cells))


# Google renders a currency-formatted cell with its symbol, so the symbol has to
# come off before the number can be read. Both are stripped: a sheet migrated
# from euros to shekels can briefly hold either.
_CURRENCY_SYMBOLS = ("₪", "€", "ILS", "EUR")


def _strip_currency(text):
    cleaned = str(text).replace(",", "")
    for symbol in _CURRENCY_SYMBOLS:
        cleaned = cleaned.replace(symbol, "")
    return cleaned.strip()


def _as_float(text):
    try:
        return float(_strip_currency(text))
    except (ValueError, AttributeError):
        return None


def _as_int(text):
    try:
        return int(round(float(_strip_currency(text))))
    except (ValueError, AttributeError):
        return None


def _a1(column, row):
    letter = chr(ord("A") + column - 1)
    return f"{letter}{row}"
