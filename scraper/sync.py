"""Entry point: scrape a trade-in source and update the Google Sheet.

    python -m scraper.sync --dry-run          # scrape, write nothing, show results
    python -m scraper.sync                    # scrape and update the sheet
    python -m scraper.sync --source pelephone # use the fallback source
    python -m scraper.sync --capture          # save raw HTML for selector work
    python -m scraper.sync --only-at-hour 6 --tz Asia/Jerusalem   # scheduled run

Exit codes, so a scheduler can tell what happened:

    0  success (or a dry run that worked)
    1  could not fetch the page -- site down, blocked, or network failure
    2  fetched but could not parse devices, or too few to trust
    3  could not read or write the Google Sheet
    4  bad configuration or arguments

On any non-zero exit the sheet is left exactly as it was. Nothing is ever
cleared before a scrape succeeds.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import logging
import os
import sys

# Allow both "python -m scraper.sync" and "python scraper/sync.py".
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config          # noqa: E402
import notify         # noqa: E402
import sheets          # noqa: E402
from fetch import FetchError, Fetcher   # noqa: E402
from sources import ParseError, get_source   # noqa: E402

log = logging.getLogger("sync")

EXIT_OK, EXIT_FETCH, EXIT_PARSE, EXIT_SHEET, EXIT_CONFIG = 0, 1, 2, 3, 4


def main(argv=None):
    """Guard the hour, run the sync, then notify. Returns the sync's exit code."""
    args = _parse_args(argv)
    _setup_logging(args.verbose)

    if args.only_at_hour is not None and not _is_scheduled_hour(args.only_at_hour, args.tz):
        # Coolify has no per-task timezone, so the task is scheduled at both
        # candidate UTC hours and this decides which one does the work. A skip
        # is a success: it must not look like a failure or send email.
        log.info("not the %02d:00 hour in %s (it is %s) -- nothing to do",
                 args.only_at_hour, args.tz, _local_clock(args.tz))
        return EXIT_OK

    outcome = {}
    code = _run(args, outcome)
    _notify(code, args, outcome)
    return code


def _notify(code, args, outcome):
    """Email the result. Never changes the exit code."""
    if args.dry_run:
        log.info("dry run -- no email sent")
        return
    if not notify.is_configured():
        log.info("email is not configured (SMTP_HOST / NOTIFY_TO) -- no email sent")
        return

    if code == EXIT_OK:
        notify.send_success(outcome.get("counts", {}),
                            spreadsheet_id=config.SPREADSHEET_ID,
                            source=outcome.get("source", "KSP"))
    else:
        notify.send_failure(code, outcome.get("error", ""),
                            source=outcome.get("source", "KSP"))


def _run(args, outcome):
    """The sync itself. Fills the outcome dict for the notification email."""

    try:
        settings = config.source_config(args.source)
    except KeyError as err:
        log.error("%s", err)
        outcome["error"] = str(err)
        return EXIT_CONFIG

    source_name = args.source or config.SOURCE
    outcome["source"] = settings.get("label", source_name)
    log.info("source: %s (%s)", settings["label"], settings["url"])

    fetcher = Fetcher(
        user_agent=config.USER_AGENT,
        delay_seconds=config.REQUEST_DELAY_SECONDS,
        timeout=config.REQUEST_TIMEOUT,
        max_retries=config.MAX_RETRIES,
        proxy_url=config.KSP_PROXY_URL,
        backoff_seconds=config.BACKOFF_SECONDS,
    )
    source = get_source(source_name, settings)

    # ---------------------------------------------------------------- fetch
    try:
        if args.html:
            log.info("reading saved response from %s", args.html)
            with open(args.html, encoding="utf-8") as fh:
                html = fh.read()
        else:
            html = fetcher.get(settings["url"])
    except FetchError as err:
        log.error("FETCH FAILED: %s", err)
        log.error("The sheet has not been touched.")
        outcome["error"] = str(err)
        _log_failure(source_name, "fetch_failed", str(err), args)
        return EXIT_FETCH
    except OSError as err:
        log.error("could not read %s: %s", args.html, err)
        return EXIT_CONFIG

    if args.capture:
        path = _save_capture(html, source_name, _capture_suffix(settings))
        log.info("saved raw HTML to %s (%d bytes)", path, len(html))

    # ---------------------------------------------------------------- parse
    try:
        devices = source.parse(html)
    except ParseError as err:
        log.error("PARSE FAILED: %s", err)
        log.error("The sheet has not been touched.")
        outcome["error"] = str(err)
        if not args.capture:
            path = _save_capture(html, source_name, _capture_suffix(settings))
            log.error("Raw HTML saved to %s so the selectors can be fixed.", path)
        _log_failure(source_name, "parse_failed", str(err), args)
        return EXIT_PARSE

    log.info("parsed %d devices via %s", len(devices), source.strategy)

    if len(devices) < config.MIN_DEVICES_EXPECTED:
        message = (f"only {len(devices)} devices found, expected at least "
                   f"{config.MIN_DEVICES_EXPECTED}. Treating this as a broken scrape "
                   f"rather than writing it to the sheet.")
        log.error("PARSE SUSPECT: %s", message)
        outcome["error"] = message
        _log_failure(source_name, "too_few_devices", message, args)
        return EXIT_PARSE

    # No currency conversion anywhere: KSP quotes shekels, the sheet stores
    # shekels, customers are quoted shekels. The euro round trip this replaced
    # cost up to 3 ILS of accuracy and rewrote dozens of rows whenever the
    # exchange rate twitched, which buried real price changes in the log.
    source_currency = settings.get("currency", "ILS")
    if source_currency != config.SHEET_CURRENCY:
        message = (f"{settings['label']} quotes {source_currency} but the sheet "
                   f"stores {config.SHEET_CURRENCY}")
        log.error("CURRENCY MISMATCH: %s. Add a conversion step before using "
                  "this source.", message)
        outcome["error"] = message
        return EXIT_CONFIG

    # -------------------------------------------------------------- output
    if args.output:
        _write_output(devices, args.output)
        log.info("wrote %d devices to %s", len(devices), args.output)

    _print_sample(devices)

    # --------------------------------------------------------------- sheet
    # A dry run still reads the sheet and works out the plan -- that is the
    # useful part -- it just never writes.
    writer = sheets.SheetWriter(
        config.SPREADSHEET_ID, config.CREDENTIALS_PATH,
        devices_tab=config.DEVICES_TAB, conditions_tab=config.CONDITIONS_TAB,
        sync_log_tab=config.SYNC_LOG_TAB,
    )
    try:
        writer.connect()
        existing = writer.read_devices()
        conditions = writer.read_conditions()
        plan = sheets.plan_changes(
            existing, devices, conditions,
            deactivate_missing=not args.no_deactivate,
            max_deactivation_ratio=config.MAX_DEACTIVATION_RATIO,
        )
        _print_plan(plan, existing)

        if args.dry_run:
            log.info("DRY RUN -- the sheet was not touched")
            return EXIT_OK

        outcome["counts"] = _counts(devices, plan)

        if plan.is_empty:
            log.info("nothing to change")
            writer.log_run(status="success", source=source_name,
                           devices_found=len(devices), plan=plan, message="no changes")
            return EXIT_OK

        result = writer.apply(plan, conditions, len(existing))
        writer.log_run(status="success", source=source_name, devices_found=len(devices),
                       plan=plan, message=plan.summary())
        log.info("done: %s (%d cells updated, %d rows appended)",
                 plan.summary(), result["cells_updated"], result["rows_appended"])
    except sheets.SheetError as err:
        if args.dry_run:
            log.warning("could not read the sheet, so no plan was built: %s", err)
            log.info("DRY RUN -- the sheet was not touched")
            return EXIT_OK
        log.error("SHEET FAILED: %s", err)
        outcome["error"] = str(err)
        return EXIT_SHEET

    return EXIT_OK


# --------------------------------------------------------------------- helpers

def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="python -m scraper.sync",
        description="Scrape device trade-in prices and update the Google Sheet.",
    )
    parser.add_argument("--source", choices=sorted(config.SOURCES),
                        help=f"which site to scrape (default: {config.SOURCE})")
    parser.add_argument("--dry-run", action="store_true",
                        help="scrape and report, but do not touch the sheet")
    parser.add_argument("--capture", action="store_true",
                        help="save the raw HTML to captures/ for selector work")
    parser.add_argument("--html", "--from-file", dest="html", metavar="PATH",
                        help="parse a saved response (HTML or JSON) instead of fetching")
    parser.add_argument("--output", metavar="PATH",
                        help="also write the scraped devices to a .json or .csv file")
    parser.add_argument("--no-deactivate", action="store_true",
                        help="do not deactivate sheet devices missing from the scrape")
    parser.add_argument("--only-at-hour", type=int, metavar="HOUR",
                        help="exit 0 without doing anything unless the local hour "
                             "in --tz is HOUR. Lets one cron entry per candidate "
                             "UTC hour cover a timezone that shifts with DST.")
    parser.add_argument("--tz", default="Asia/Jerusalem", metavar="ZONE",
                        help="timezone for --only-at-hour (default: Asia/Jerusalem)")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = parser.parse_args(argv)
    if args.only_at_hour is not None and not 0 <= args.only_at_hour <= 23:
        parser.error("--only-at-hour must be between 0 and 23")
    return args


def _zone(tz_name):
    """The named timezone. Raises if the image has no tzdata."""
    from zoneinfo import ZoneInfo
    return ZoneInfo(tz_name)


def _is_scheduled_hour(hour, tz_name, now=None):
    """True when it is that hour in that timezone.

    `now` may be any aware datetime, which is what makes this testable across a
    DST boundary without waiting for October.

    If the timezone cannot be resolved -- a slim image without tzdata -- the run
    goes ahead rather than being skipped. Syncing at the wrong hour is a much
    smaller problem than never syncing at all, and the log says why.
    """
    try:
        zone = _zone(tz_name)
    except Exception as err:      # noqa: BLE001
        log.warning("cannot resolve timezone %s (%s); running regardless. "
                    "Install tzdata in the image to make --only-at-hour work.",
                    tz_name, err)
        return True

    local = now.astimezone(zone) if now is not None else dt.datetime.now(zone)
    return local.hour == hour


def _local_clock(tz_name):
    try:
        return dt.datetime.now(_zone(tz_name)).strftime("%H:%M %Z")
    except Exception:             # noqa: BLE001
        return dt.datetime.now().strftime("%H:%M (server time)")


def _counts(devices, plan):
    """The numbers the notification email reports."""
    return {
        "devices_found": len(devices),
        "new_devices": len(plan.new_devices),
        "prices_changed": len(plan.price_updates),
        "deactivated": len(plan.deactivations),
    }


def _setup_logging(verbose):
    handlers = [logging.StreamHandler(sys.stdout)]
    try:
        os.makedirs(config.LOG_DIR, exist_ok=True)
        handlers.append(
            logging.FileHandler(os.path.join(config.LOG_DIR, "sync.log"), encoding="utf-8"))
    except OSError:
        # Read-only or non-writable filesystem, as in a container. Console
        # logging is enough -- Coolify captures stdout.
        pass
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
    )


def _save_capture(html, source_name, suffix="html"):
    os.makedirs(config.CAPTURE_DIR, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(config.CAPTURE_DIR, f"{source_name}-{stamp}.{suffix}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return path


def _capture_suffix(settings):
    return "json" if settings.get("kind") == "api" else "html"


def _write_output(devices, path):
    rows = [device.as_dict() for device in devices]
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    if path.lower().endswith(".csv"):
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    else:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({
                "scraped_at": dt.datetime.now().isoformat(timespec="seconds"),
                "count": len(rows),
                "devices": rows,
            }, fh, indent=2, ensure_ascii=False)


def _print_sample(devices, limit=8):
    """Show a readable slice, with every condition price when the source has them."""
    conditions = []
    for device in devices:
        if device.condition_prices:
            conditions = list(device.condition_prices)
            break

    log.info("sample of what was scraped (prices in %s):", devices[0].currency)
    if conditions:
        log.info("  %-9s %-22s %-7s %s", "BRAND", "MODEL", "STORAGE",
                 " ".join(f"{c:>9s}" for c in conditions))
    for device in devices[:limit]:
        if device.condition_prices:
            values = " ".join(f"{int(round(device.condition_prices.get(c, 0))):>9d}"
                              for c in conditions)
        else:
            values = f"{int(round(device.price)):>9d}"
        log.info("  %-9s %-22s %-7s %s",
                 device.manufacturer, device.model[:22], device.storage, values)
    if len(devices) > limit:
        log.info("  ... and %d more", len(devices) - limit)


def _print_plan(plan, existing_rows):
    """Spell out what the sync would do, before it does any of it."""
    data_rows = len([r for r in existing_rows[1:] if r and str(r[0]).strip()])
    after = data_rows + len(plan.new_devices) * 4

    log.info("plan against the sheet:")
    log.info("  sheet now           : %d data rows (%d devices)", data_rows, data_rows // 4)
    log.info("  new devices         : %d  -> %d rows appended",
             len(plan.new_devices), len(plan.new_devices) * 4)
    log.info("  prices repriced     : %d rows", len(plan.price_updates))
    log.info("  multipliers changed : %d rows", len(plan.multiplier_updates))
    log.info("  reactivated         : %d rows", len(plan.reactivations))
    log.info("  deactivated         : %d rows", len(plan.deactivations))
    log.info("  unchanged           : %d rows", plan.unchanged)
    log.info("  sheet after         : %d data rows (%d devices)", after, after // 4)

    if plan.new_devices:
        log.info("  first few new devices:")
        for device_id, device in plan.new_devices[:5]:
            log.info("     %-22s %-9s %-22s %s",
                     device_id, device.manufacturer, device.model[:22], device.storage)
        if len(plan.new_devices) > 5:
            log.info("     ... and %d more", len(plan.new_devices) - 5)

    if plan.price_updates:
        log.info("  first few reprices:")
        seen = set()
        for _row, device_id, old_price, new_price in plan.price_updates:
            if device_id in seen:
                continue
            seen.add(device_id)
            log.info("     %-22s ILS %s -> %s", device_id, old_price, new_price)
            if len(seen) >= 5:
                break


def _log_failure(source_name, status, message, args):
    """Record a failed run in the sheet's Sync Log, if the sheet is reachable."""
    if args.dry_run or not config.SPREADSHEET_ID:
        return
    try:
        writer = sheets.SheetWriter(config.SPREADSHEET_ID, config.CREDENTIALS_PATH,
                                    sync_log_tab=config.SYNC_LOG_TAB)
        writer.log_run(status=status, source=source_name, devices_found=0,
                       plan=None, message=message)
    except Exception as err:
        log.debug("could not record the failure in the sheet: %s", err)


if __name__ == "__main__":
    sys.exit(main())
