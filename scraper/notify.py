"""Email notification for a sync run.

Configured entirely by environment variables, so no address or password is ever
in the repository:

    SMTP_HOST           smtp.gmail.com
    SMTP_PORT           587
    SMTP_USER           the account that authenticates
    SMTP_PASSWORD       an app password, not the account password
    NOTIFY_FROM         the From: address (defaults to SMTP_USER)
    NOTIFY_TO           who gets the "prices updated" mail
    NOTIFY_FAILURE_TO   who gets the "sync failed" mail (defaults to NOTIFY_TO)

SMTP_PASS, EMAIL_FROM and EMAIL_TO are accepted as aliases for the three most
easily misremembered names.

Gmail with an App Password is the expected setup, but nothing here is specific
to Gmail: any host that speaks SMTP with STARTTLS works, and port 465 switches
to implicit TLS automatically.

**Sending never raises.** A mail problem must not change what happened to the
sheet, so every failure is logged and swallowed, and the caller keeps its own
exit code.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

log = logging.getLogger(__name__)

ISRAEL_TZ = "Asia/Jerusalem"
SHEET_URL_TEMPLATE = "https://docs.google.com/spreadsheets/d/{}/edit"

EXIT_REASONS = {
    1: "the KSP catalogue could not be fetched",
    2: "the catalogue was fetched but could not be read",
    3: "the Google Sheet could not be read or written",
    4: "the sync is misconfigured",
}


# Names people reasonably reach for, accepted as aliases. Half-configured email
# is worse than none, because the run looks fine and nobody hears anything.
ALIASES = {
    "SMTP_PASSWORD": ("SMTP_PASS",),
    "NOTIFY_FROM": ("EMAIL_FROM", "MAIL_FROM"),
    "NOTIFY_TO": ("EMAIL_TO", "MAIL_TO"),
    "NOTIFY_FAILURE_TO": ("EMAIL_FAILURE_TO", "ALERT_TO"),
}


def _env(name, default=""):
    """A setting, by its canonical name or any accepted alias."""
    value = os.environ.get(name, "")
    if value.strip():
        return value.strip()
    for alias in ALIASES.get(name, ()):
        value = os.environ.get(alias, "")
        if value.strip():
            return value.strip()
    return default


def _recipients(name):
    """Addresses from a comma- or semicolon-separated variable."""
    raw = _env(name)
    return [a.strip() for a in raw.replace(";", ",").split(",") if a.strip()]


def is_configured():
    """True when enough is set to attempt a send.

    Says plainly what is missing when the configuration is partial, rather than
    skipping quietly -- a half-set-up mailer is the failure mode that goes
    unnoticed for weeks.
    """
    host = _env("SMTP_HOST")
    recipients = _recipients("NOTIFY_TO")

    if host and recipients:
        return True

    if host or recipients or _env("SMTP_USER"):
        missing = []
        if not host:
            missing.append("SMTP_HOST")
        if not recipients:
            missing.append("NOTIFY_TO (or EMAIL_TO)")
        log.warning("email is only half configured -- missing %s. No mail will "
                    "be sent. See docs/scraper.md, 'Email'.", " and ".join(missing))
    return False


def israel_now():
    """Now, in Israel, whatever the server's clock is set to."""
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo(ISRAEL_TZ))
    except Exception:
        # No tzdata in the image: fall back to UTC rather than fail a send.
        log.warning("no %s timezone data; timestamps will be UTC", ISRAEL_TZ)
        return dt.datetime.now(dt.timezone.utc)


def sheet_url(spreadsheet_id):
    return SHEET_URL_TEMPLATE.format(spreadsheet_id) if spreadsheet_id else ""


# ------------------------------------------------------------------ messages

def build_success(counts, *, spreadsheet_id="", when=None, source="KSP"):
    """(subject, plain, html) for a run that updated the sheet."""
    when = when or israel_now()
    stamp = when.strftime("%d %b %Y")
    clock = when.strftime("%H:%M")
    link = sheet_url(spreadsheet_id)

    subject = f"{source} prices updated - {stamp}"

    rows = [
        ("Run finished", f"{clock} Israel time, {stamp}"),
        ("Devices found", counts.get("devices_found", 0)),
        ("New devices", counts.get("new_devices", 0)),
        ("Prices changed", counts.get("prices_changed", 0)),
        ("Deactivated", counts.get("deactivated", 0)),
    ]

    plain = [f"{source} trade-in prices have been updated in the Google Sheet.", ""]
    plain += [f"  {label}: {value}" for label, value in rows]
    if counts.get("prices_changed", 0) == 0 and counts.get("new_devices", 0) == 0:
        plain += ["", "No prices moved today -- the sheet already matched KSP."]
    if link:
        plain += ["", f"Sheet: {link}"]
    plain += ["", "This is an automated message from the trade-in price sync."]

    table = "".join(
        f'<tr><td style="padding:4px 16px 4px 0;color:#555">{label}</td>'
        f'<td style="padding:4px 0;font-weight:600">{value}</td></tr>'
        for label, value in rows
    )
    button = (f'<p style="margin:20px 0"><a href="{link}" '
              f'style="background:#1f3864;color:#fff;padding:10px 18px;'
              f'border-radius:6px;text-decoration:none">Open the sheet</a></p>'
              if link else "")
    html = f"""<!doctype html>
<html><body style="font-family:Arial,Helvetica,sans-serif;font-size:15px;color:#222">
  <h2 style="margin:0 0 12px;color:#1f3864">{source} prices updated</h2>
  <p style="margin:0 0 16px">The trade-in prices in the Google Sheet are up to date.</p>
  <table style="border-collapse:collapse">{table}</table>
  {button}
  <p style="margin-top:24px;color:#888;font-size:12px">
    Automated message from the trade-in price sync.</p>
</body></html>"""

    return subject, "\n".join(plain), html


def build_failure(exit_code, message, *, when=None, source="KSP"):
    """(subject, plain, html) for a run that failed. The sheet is unchanged."""
    when = when or israel_now()
    stamp = when.strftime("%d %b %Y")
    clock = when.strftime("%H:%M")
    reason = EXIT_REASONS.get(exit_code, "the sync stopped with an unexpected error")

    subject = f"{source} price sync FAILED - {stamp}"

    plain = "\n".join([
        f"The {source} price sync did not complete.",
        "",
        "THE GOOGLE SHEET WAS NOT CHANGED. It still holds the last good prices,",
        "so the chatbot keeps quoting those and nothing is broken for customers.",
        "",
        f"  Reason: {reason}.",
        f"  Exit code: {exit_code}",
        f"  Attempted: {clock} Israel time, {stamp}",
        "",
        f"Detail: {message}" if message else "",
        "",
        "The next scheduled run will try again on its own.",
    ])

    html = f"""<!doctype html>
<html><body style="font-family:Arial,Helvetica,sans-serif;font-size:15px;color:#222">
  <h2 style="margin:0 0 12px;color:#a02020">{source} price sync failed</h2>
  <p style="margin:0 0 8px;padding:12px;background:#f3f6fa;border-left:4px solid #1f3864">
    <strong>The Google Sheet was not changed.</strong> It still holds the last
    good prices, so the chatbot keeps quoting those.</p>
  <table style="border-collapse:collapse;margin-top:12px">
    <tr><td style="padding:4px 16px 4px 0;color:#555">Reason</td>
        <td style="padding:4px 0;font-weight:600">{reason}</td></tr>
    <tr><td style="padding:4px 16px 4px 0;color:#555">Exit code</td>
        <td style="padding:4px 0;font-weight:600">{exit_code}</td></tr>
    <tr><td style="padding:4px 16px 4px 0;color:#555">Attempted</td>
        <td style="padding:4px 0;font-weight:600">{clock} Israel time, {stamp}</td></tr>
  </table>
  <p style="margin-top:16px;color:#444">{message}</p>
  <p style="margin-top:8px">The next scheduled run will try again on its own.</p>
</body></html>"""

    return subject, plain, html


# -------------------------------------------------------------------- sending

def send(subject, plain, html, *, to=None, failure=False):
    """Send one message. Returns True if it went, False otherwise. Never raises."""
    try:
        host = _env("SMTP_HOST")
        if not host:
            log.info("SMTP_HOST is not set; skipping the %s email",
                     "failure" if failure else "success")
            return False

        recipients = to or _recipients("NOTIFY_FAILURE_TO" if failure else "NOTIFY_TO")
        if failure and not recipients:
            recipients = _recipients("NOTIFY_TO")
        if not recipients:
            log.warning("no recipients configured; skipping the email")
            return False

        port = int(_env("SMTP_PORT", "587") or 587)
        user = _env("SMTP_USER")
        password = _env("SMTP_PASSWORD")
        sender = _env("NOTIFY_FROM") or user
        if not sender:
            log.warning("neither NOTIFY_FROM nor SMTP_USER is set; skipping the email")
            return False

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = sender
        message["To"] = ", ".join(recipients)
        message.set_content(plain)
        message.add_alternative(html, subtype="html")

        context = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as smtp:
                if user:
                    smtp.login(user, password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=30) as smtp:
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
                if user:
                    smtp.login(user, password)
                smtp.send_message(message)

        log.info("emailed %s: %s", ", ".join(recipients), subject)
        return True

    except Exception as err:      # noqa: BLE001 - mail must never break the sync
        log.error("could not send the notification email (%s: %s). "
                  "The sync result is unaffected.", type(err).__name__, err)
        return False


def send_success(counts, *, spreadsheet_id="", when=None, source="KSP"):
    subject, plain, html = build_success(
        counts, spreadsheet_id=spreadsheet_id, when=when, source=source)
    return send(subject, plain, html, failure=False)


def send_failure(exit_code, message, *, when=None, source="KSP"):
    subject, plain, html = build_failure(
        exit_code, message, when=when, source=source)
    return send(subject, plain, html, failure=True)
