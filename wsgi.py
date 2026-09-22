"""WSGI entry point for gunicorn / Passenger.

    gunicorn --bind 0.0.0.0:5000 --workers 2 wsgi:application

The device cache is warmed at import so the first customer request is not the slow one.
A failure here is logged and ignored: the app still starts and fills its caches
on demand.
"""

import logging

from api.app import app as application
from api import sheets_query

log = logging.getLogger("wsgi")

try:
    sheets_query.warm()
    log.info("device cache warmed at startup")
except Exception as err:  # noqa: BLE001
    log.warning("could not warm the device cache at startup: %s", err)
