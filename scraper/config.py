"""Every knob the scraper has, in one file.

When the site layout changes, the fix is almost always in SOURCES below and
nowhere else. See docs/scraper.md, "When scraping breaks".
"""

import os

# Which source to scrape. Override per run with: python -m scraper.sync --source pelephone
SOURCE = os.environ.get("TRADEIN_SOURCE", "ksp")

# --------------------------------------------------------------------- sheet
# The "Trade In Database" sheet. Not a secret -- access is controlled by sharing
# it with the service account. Override per run with TRADEIN_SPREADSHEET_ID.
SPREADSHEET_ID = os.environ.get(
    "TRADEIN_SPREADSHEET_ID", "1KdcjtNqLkmJo4XYiGM2oX5lo9NjqfDKpGc8HCteFmGk")


def _find_credentials():
    """The service account key, from the env var or the usual filenames."""
    explicit = os.environ.get("TRADEIN_CREDENTIALS")
    if explicit:
        return explicit
    for candidate in ("google-credentials.json", "credentials.json"):
        if os.path.exists(candidate):
            return candidate
    return "credentials.json"


CREDENTIALS_PATH = _find_credentials()
DEVICES_TAB = "Devices"
CONDITIONS_TAB = "Conditions"
SYNC_LOG_TAB = "Sync Log"

# ------------------------------------------------------------------ fetching
USER_AGENT = os.environ.get(
    "TRADEIN_USER_AGENT",
    # Identifies the bot and gives the site owner a way to get in touch, which is
    # what a site operator wants to see in their logs. Swap in a real address.
    "TradeInSyncBot/1.0 (+contact: your-email@example.com) python-requests",
)
# An HTTP(S) proxy for the KSP fetch ONLY. ksp.co.il refuses non-Israeli
# addresses, so a run from outside Israel needs an exit node there. This is
# applied to the source session alone -- Google Sheets and SMTP must not go
# through it, which is why there is no reliance on a global HTTPS_PROXY.
# Example: http://user:pass@proxy.example.co.il:8080
KSP_PROXY_URL = os.environ.get("KSP_PROXY_URL", "").strip()

REQUEST_DELAY_SECONDS = 2.0   # minimum gap between requests
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
BACKOFF_SECONDS = 5.0

# ------------------------------------------------------------------ currency
# The sheet stores shekels, the currency KSP quotes, so nothing is converted
# anywhere in the pipeline. A source quoting another currency is rejected
# rather than silently converted.
SHEET_CURRENCY = "ILS"

# ------------------------------------------------------------------- safety
# If a scrape returns fewer than this many devices, treat the run as suspect:
# prices are not written and nothing is deactivated. Guards against a layout
# change that makes the parser silently return two devices instead of two hundred.
MIN_DEVICES_EXPECTED = 5

# A device in the sheet but missing from the scrape is deactivated, never deleted.
# If more than this fraction would be deactivated in one run, stop instead —
# that pattern means the scrape broke, not that the catalogue emptied.
MAX_DEACTIVATION_RATIO = 0.30

# --------------------------------------------------------------------- paths
CAPTURE_DIR = "captures"
LOG_DIR = "logs"

# -------------------------------------------------------------------- sources
#
#   KSP is a real, verified API endpoint (see docs/ksp-api.md).
#   PelePhone is still unverified -- its selectors are placeholders, because
#   that site refused every request from the network this was built on.
#
SOURCES = {
    "ksp": {
        "kind": "api",
        "label": "KSP",
        # The catalogue endpoint the trade-in calculator itself calls. One GET,
        # no query parameters, no authentication. Verified against a real
        # browser capture on 2026-09-21.
        "url": "https://ksp.co.il/snif/TradeIn/INNER_new/api/public/devices",
        "referer": "https://ksp.co.il/kspTradeIn/",
        "currency": "ILS",
        # KSP's catalogue covers phones and tablets. The Phase 1 sheet is a
        # phone catalogue, so tablets are left out by default; empty the list
        # to bring them in (adds roughly 167 devices).
        "exclude_types": ["Tablet"],
        # KSP lists Samsung models without the "Galaxy" prefix ("S23", "A54 (5G)").
        # Customers say "Galaxy S23", and so does the Phase 1 sheet.
        "prefix_samsung_galaxy": True,
    },
    "pelephone": {
        "label": "PelePhone",
        "url": "https://www.pelephone.co.il/ds/heb/eshop/trade-in/",
        "currency": "ILS",
        "json_paths": ["data.devices", "devices", "items", "results"],
        "json_fields": {
            "manufacturer": ["manufacturer", "brand", "vendor"],
            "model": ["model", "name", "title", "deviceName"],
            "storage": ["storage", "capacity", "size"],
            "price": ["price", "value", "amount", "tradeInValue"],
            "condition": ["condition", "grade", "state"],
        },
        "selectors": {
            "device_row": ".device-item, .trade-in-device, [data-device-id]",
            "manufacturer": ".manufacturer, .brand",
            "model": ".device-name, .model, h3",
            "storage": ".storage, .capacity",
            "price": ".price, .trade-in-price",
        },
    },
}


def source_config(name=None):
    name = name or SOURCE
    if name not in SOURCES:
        raise KeyError(f"Unknown source {name!r}. Known sources: {', '.join(SOURCES)}")
    return SOURCES[name]
