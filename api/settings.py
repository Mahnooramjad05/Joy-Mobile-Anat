"""Configuration, from the environment with sensible fallbacks.

Reads .env if python-dotenv is installed, then falls back to scraper/config.py
so the API and the sync cannot drift onto different spreadsheets.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:  # python-dotenv is optional
    pass


def _scraper_config():
    """scraper/config.py, or None if it cannot be imported."""
    scraper_dir = os.path.join(ROOT, "scraper")
    if scraper_dir not in sys.path:
        sys.path.insert(0, scraper_dir)
    try:
        import config
        return config
    except Exception:
        return None


def spreadsheet_id():
    value = os.environ.get("SPREADSHEET_ID") or os.environ.get("TRADEIN_SPREADSHEET_ID")
    if value:
        return value
    config = _scraper_config()
    return getattr(config, "SPREADSHEET_ID", "") if config else ""


def credentials_path():
    value = (os.environ.get("GOOGLE_CREDENTIALS_PATH")
             or os.environ.get("TRADEIN_CREDENTIALS"))
    if value:
        return value if os.path.isabs(value) else os.path.join(ROOT, value)

    config = _scraper_config()
    if config and getattr(config, "CREDENTIALS_PATH", None):
        path = config.CREDENTIALS_PATH
        return path if os.path.isabs(path) else os.path.join(ROOT, path)

    return os.path.join(ROOT, "google-credentials.json")


def port():
    try:
        return int(os.environ.get("PORT", "5000"))
    except ValueError:
        return 5000


def debug():
    return os.environ.get("FLASK_ENV", "production").lower() == "development"
