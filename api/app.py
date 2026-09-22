"""Device trade-in pricing API.

    POST /api/device-price   -- prices for one device, in shekels, per condition
    GET  /health             -- liveness plus cache state
    GET  /api/conditions     -- the four conditions in Hebrew and English

Prices are shekels from end to end: KSP quotes shekels, the sync stores
shekels, and the API returns them untouched. There is no currency conversion
anywhere. The device index is cached, so a warm request touches no network.
"""

from __future__ import annotations

import logging
import os
import time

from flask import Flask, jsonify, request

from . import conditions as conditions_module
from . import settings, sheets_query

log = logging.getLogger("api")

REQUIRED_FIELDS = ("manufacturer", "model", "storage_gb")


def create_app():
    app = Flask(__name__)
    # Hebrew goes out as Hebrew, not as backslash-u escape sequences.
    app.json.ensure_ascii = False

    _configure_logging()

    @app.post("/api/device-price")
    def device_price():
        started = time.perf_counter()

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return _error("Invalid request body", 400,
                          detail="Send a JSON object with Content-Type: application/json.")

        missing = [f for f in REQUIRED_FIELDS if _blank(payload.get(f))]
        if missing:
            return _error("Missing required field" + ("s" if len(missing) > 1 else ""),
                          400, missing_fields=missing,
                          required_fields=list(REQUIRED_FIELDS))

        storage_gb = sheets_query.storage_to_gb(payload.get("storage_gb"))
        if storage_gb is None:
            return _error("Invalid storage_gb", 400,
                          detail="Give a size in GB, for example 256, \"256GB\" or \"1TB\".")

        # `condition` is optional, and validated rather than used as a filter:
        # the response always carries all four prices, so the bot can show the
        # whole ladder and let the customer place their own device. A condition
        # that is sent is echoed back as `selected_condition`.
        requested_condition = payload.get("condition")
        if not _blank(requested_condition):
            try:
                requested_condition = conditions_module.to_english(requested_condition)
            except conditions_module.UnknownCondition:
                return _error("Invalid condition", 400,
                              valid_conditions=conditions_module.HEBREW_NAMES,
                              valid_conditions_english=conditions_module.ENGLISH_NAMES)
        else:
            requested_condition = None

        try:
            device = sheets_query.find_device(
                payload["manufacturer"], payload["model"], storage_gb)
        except sheets_query.DeviceNotFound:
            log.info("404 %s %s %sGB", payload.get("manufacturer"),
                     payload.get("model"), storage_gb)
            return _error("Device not found", 404)
        except sheets_query.SheetUnavailable as err:
            log.error("sheet unavailable: %s", err)
            return _error("Unable to fetch pricing", 500)
        except Exception as err:  # noqa: BLE001 - never leak a stack trace
            log.exception("unexpected lookup failure: %s", err)
            return _error("Unable to fetch pricing", 500)

        priced = []
        for entry in conditions_module.CONDITIONS:
            english = entry["english"]
            price = device["prices_ils"].get(english)
            if price is None:
                continue
            priced.append({
                "hebrew": entry["hebrew"],
                "english": english,
                "price_ils": int(round(price)),
                "currency": "ILS",
            })

        if not priced:
            log.error("device %s has no usable prices", device["model"])
            return _error("Unable to fetch pricing", 500)

        response = {
            "device": f"{device['manufacturer']} {device['model']}",
            "manufacturer": device["manufacturer"],
            "model": device["model"],
            "storage": device["storage"],
            "storage_gb": device["storage_gb"],
            "conditions": priced,
            "currency": "ILS",
        }
        if requested_condition:
            response["selected_condition"] = conditions_module.both(requested_condition)
        if device.get("release_year"):
            response["release_year"] = device["release_year"]

        log.info("200 %s %s in %.0fms", response["device"], device["storage"],
                 (time.perf_counter() - started) * 1000)
        return jsonify(response), 200

    @app.get("/api/conditions")
    def list_conditions():
        return jsonify({"conditions": conditions_module.CONDITIONS}), 200

    @app.get("/health")
    def health():
        return jsonify({
            "status": "ok",
            "currency": "ILS",
            "devices": sheets_query.status(),
        }), 200

    @app.errorhandler(404)
    def not_found(_err):
        return _error("Not found", 404,
                      detail="POST /api/device-price is the pricing endpoint.")

    @app.errorhandler(405)
    def method_not_allowed(_err):
        return _error("Method not allowed", 405, detail="This endpoint expects POST.")

    @app.errorhandler(Exception)
    def unhandled(err):
        log.exception("unhandled error: %s", err)
        return _error("Unable to fetch pricing", 500)

    return app


def _blank(value):
    return value is None or (isinstance(value, str) and not value.strip())


def _error(message, status, **extra):
    body = {"error": message, "status": status}
    body.update(extra)
    return jsonify(body), status


def _configure_logging():
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=logging.DEBUG if settings.debug() else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


app = create_app()


if __name__ == "__main__":
    # Warm the caches so the first customer request is not the slow one.
    try:
        sheets_query.warm()
    except Exception as err:  # noqa: BLE001
        log.warning("could not warm the device cache: %s -- starting anyway", err)

    app.run(host=os.environ.get("HOST", "0.0.0.0"),
            port=settings.port(), debug=settings.debug())
