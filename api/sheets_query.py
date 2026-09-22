"""Device price lookups against the Devices tab.

Prices are shekels throughout: the sheet stores what KSP quotes, so nothing is
converted on the way out.

The whole tab is read once and indexed in memory, rather than querying per
request: it is ~1,300 rows, Google rate-limits per-call reads, and a chatbot
asks about one device at a time. One read every 30 minutes serves everything,
and a cached lookup is a dictionary hit.

Matching is exact on manufacturer + model + storage. Case, spacing and
punctuation are folded away first, because a chatbot will send "iphone 15 pro"
for the sheet's "iPhone 15 Pro" -- that is the same device, not a fuzzy guess.
No similarity matching happens anywhere: an unknown device is a 404.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import unicodedata

log = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 30 * 60
DEVICES_TAB = os.environ.get("DEVICES_TAB", "Devices")

# Devices tab columns (0-indexed), matching the Phase 1 / Phase 2 schema.
COL_MANUFACTURER = 1
COL_MODEL = 2
COL_STORAGE = 3
COL_RELEASE_YEAR = 4
COL_CONDITION = 6
COL_FINAL_PRICE = 8   # final_price_ils
COL_ACTIVE = 9

_lock = threading.Lock()
_cache = {"index": None, "loaded_at": 0.0, "rows": 0, "devices": 0}


class SheetUnavailable(RuntimeError):
    """The sheet could not be read."""


class DeviceNotFound(LookupError):
    """No such device in the catalogue."""


# --------------------------------------------------------------------- keys

def _fold(text):
    """Lowercase alphanumerics only, with unicode marks stripped."""
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = "".join(ch for ch in text if unicodedata.category(ch) not in ("Mn", "Cf"))
    return "".join(ch for ch in text.lower() if ch.isalnum())


def storage_to_gb(value):
    """Normalise any spelling of a storage size to a number of GB.

    256 -> 256      "256GB" -> 256      "1 TB" -> 1024      1024 -> 1024

    Returns None if it cannot be read, so the caller can answer 400 rather than
    silently looking up the wrong device.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        gb = float(value)
        return int(gb) if gb > 0 else None

    text = str(value).strip().lower().replace(",", "")
    match = re.search(r"(\d+(?:\.\d+)?)\s*(tb|gb|mb)?", text)
    if not match:
        return None

    amount = float(match.group(1))
    unit = match.group(2)
    if unit == "tb":
        amount *= 1024
    elif unit == "mb":
        amount /= 1024
    return int(amount) if amount > 0 else None


def device_key(manufacturer, model, storage_gb):
    return f"{_fold(manufacturer)}|{_fold(model)}|{int(storage_gb)}"


# ------------------------------------------------------------------- loading

def _open_worksheet():
    """Open the Devices tab. Imports are local so tests can run without gspread."""
    import gspread
    from google.oauth2.service_account import Credentials

    from . import settings

    credentials_path = settings.credentials_path()
    spreadsheet_id = settings.spreadsheet_id()

    if not spreadsheet_id:
        raise SheetUnavailable("No spreadsheet id configured (SPREADSHEET_ID).")
    if not os.path.exists(credentials_path):
        raise SheetUnavailable(f"Service account key not found at {credentials_path}.")

    creds = Credentials.from_service_account_file(
        credentials_path,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    spreadsheet = gspread.authorize(creds).open_by_key(spreadsheet_id)
    return spreadsheet.worksheet(DEVICES_TAB)


def build_index(rows):
    """Index sheet rows by device, each holding its conditions and prices.

    Inactive rows are left out, so a device deactivated by the sync stops being
    quoted without anything being deleted.
    """
    index = {}
    skipped = 0

    for row in rows[1:]:
        if not row or not str(row[0]).strip():
            continue
        cells = list(row) + [""] * (11 - len(row))

        if str(cells[COL_ACTIVE]).strip().upper() == "FALSE":
            continue

        gb = storage_to_gb(cells[COL_STORAGE])
        price = _price(cells[COL_FINAL_PRICE])
        condition = str(cells[COL_CONDITION]).strip()
        if gb is None or price is None or not condition:
            skipped += 1
            continue

        key = device_key(cells[COL_MANUFACTURER], cells[COL_MODEL], gb)
        entry = index.setdefault(key, {
            "manufacturer": str(cells[COL_MANUFACTURER]).strip(),
            "model": str(cells[COL_MODEL]).strip(),
            "storage": str(cells[COL_STORAGE]).strip(),
            "storage_gb": gb,
            "release_year": str(cells[COL_RELEASE_YEAR]).strip() or None,
            "prices_ils": {},
        })
        entry["prices_ils"][condition] = price

    if skipped:
        log.info("skipped %d unusable rows while indexing", skipped)
    return index


def _price(text):
    try:
        return float(str(text).replace(",", "").replace("€", "").replace("₪", "").strip())
    except (ValueError, AttributeError):
        return None


def _load(force=False):
    """The device index, reloading when the cache has expired."""
    with _lock:
        age = time.time() - _cache["loaded_at"]
        if _cache["index"] is not None and not force and age < CACHE_TTL_SECONDS:
            return _cache["index"]

        try:
            rows = _open_worksheet().get_all_values()
        except SheetUnavailable:
            raise
        except Exception as err:
            if _cache["index"] is not None:
                log.warning("sheet reload failed (%s); serving the %.0f-minute-old cache",
                            err, age / 60)
                return _cache["index"]
            raise SheetUnavailable(f"Could not read the {DEVICES_TAB} tab: {err}") from err

        index = build_index(rows)
        if not index:
            if _cache["index"] is not None:
                log.warning("sheet read returned no usable devices; keeping the cache")
                return _cache["index"]
            raise SheetUnavailable(f"The {DEVICES_TAB} tab has no usable device rows.")

        _cache.update(index=index, loaded_at=time.time(),
                      rows=len(rows) - 1, devices=len(index))
        log.info("loaded %d devices from %d sheet rows", len(index), len(rows) - 1)
        return index


# ------------------------------------------------------------------- lookups

def find_device(manufacturer, model, storage_gb):
    """One device and its ILS prices per condition.

    Raises DeviceNotFound for an unknown device, SheetUnavailable if the sheet
    itself cannot be read.
    """
    gb = storage_to_gb(storage_gb)
    if gb is None:
        raise DeviceNotFound(f"unreadable storage size: {storage_gb!r}")

    index = _load()
    entry = index.get(device_key(manufacturer, model, gb))
    if entry is None:
        raise DeviceNotFound(f"{manufacturer} {model} {gb}GB is not in the catalogue")
    return entry


def status():
    with _lock:
        if _cache["index"] is None:
            return {"loaded": False, "devices": 0}
        age = time.time() - _cache["loaded_at"]
        return {
            "loaded": True,
            "devices": _cache["devices"],
            "rows": _cache["rows"],
            "age_seconds": int(age),
            "expires_in_seconds": max(0, int(CACHE_TTL_SECONDS - age)),
        }


def warm():
    """Load the index up front, so the first real request is not the slow one."""
    return _load()


def reset_cache():
    """Drop the cached index. Used by tests."""
    with _lock:
        _cache.update(index=None, loaded_at=0.0, rows=0, devices=0)


def seed_cache(index):
    """Install an index directly, without touching Google. Used by tests."""
    with _lock:
        _cache.update(index=index, loaded_at=time.time(),
                      rows=len(index) * 4, devices=len(index))
