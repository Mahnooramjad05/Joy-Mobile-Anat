"""Tests for the scheduled-run machinery: credentials, proxy, email, hour guard.

All offline. No SMTP server is contacted, no proxy is dialled, and no
credentials are read from disk.
"""

import datetime as dt
import os
import re
import sys

import pytest
from zoneinfo import ZoneInfo

import credentials
import fetch
import notify
import sync

UTC = dt.timezone.utc
ISRAEL = ZoneInfo("Asia/Jerusalem")

VALID_KEY = (
    '{"type":"service_account","project_id":"p",'
    '"client_email":"bot@p.iam.gserviceaccount.com","private_key":"x"}'
)


# ======================================================== credentials from env

class TestCredentialsFromEnvironment:
    def test_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_CREDENTIALS_JSON", raising=False)
        assert credentials.from_env() is None

    def test_blank_counts_as_unset(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CREDENTIALS_JSON", "   \n ")
        assert credentials.from_env() is None

    def test_parsed_when_set(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CREDENTIALS_JSON", VALID_KEY)
        assert credentials.from_env()["client_email"] == "bot@p.iam.gserviceaccount.com"

    def test_malformed_json_is_explained(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CREDENTIALS_JSON", "{nope")
        with pytest.raises(credentials.CredentialsError) as err:
            credentials.from_env()
        assert "not valid JSON" in str(err.value)

    def test_wrong_shape_is_explained(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CREDENTIALS_JSON", '{"hello":"world"}')
        with pytest.raises(credentials.CredentialsError) as err:
            credentials.from_env()
        assert "client_email" in str(err.value)

    def test_missing_everything_names_both_options(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_CREDENTIALS_JSON", raising=False)
        with pytest.raises(credentials.CredentialsError) as err:
            credentials.load("/no/such/key.json")
        message = str(err.value)
        assert "GOOGLE_CREDENTIALS_JSON" in message and "/no/such/key.json" in message

    def test_describe_never_leaks_the_key(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CREDENTIALS_JSON", VALID_KEY)
        described = credentials.describe()
        assert "bot@p.iam.gserviceaccount.com" in described
        assert "private_key" not in described and '"x"' not in described

    def test_matches_the_api_packages_rules(self, monkeypatch):
        """The sync and the API validate the same variable the same way.

        The logic is deliberately duplicated so the sync can run with no api
        package present. This is what stops the two copies drifting apart.
        """
        sys.path.insert(0, os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))))
        from api import settings as api_settings

        for value in ("", "   ", VALID_KEY, "{nope", '{"hello":"world"}'):
            monkeypatch.setenv("GOOGLE_CREDENTIALS_JSON", value)
            scraper_result = _outcome(credentials.from_env)
            api_result = _outcome(api_settings.credentials_json)
            assert scraper_result == api_result, f"disagree on {value!r}"


def _outcome(fn):
    """('ok', value) or ('error',) -- enough to compare two implementations."""
    try:
        return ("ok", fn())
    except Exception:             # noqa: BLE001
        return ("error",)


# ================================================================ the proxy

class TestKspProxyIsolation:
    def test_no_proxy_by_default(self):
        fetcher = fetch.Fetcher(user_agent="T/1.0")
        assert not fetcher.session.proxies

    def test_proxy_applied_to_the_session(self):
        fetcher = fetch.Fetcher(user_agent="T/1.0",
                                proxy_url="http://proxy.example.co.il:8080")
        assert fetcher.session.proxies == {
            "http": "http://proxy.example.co.il:8080",
            "https": "http://proxy.example.co.il:8080",
        }

    def test_blank_proxy_is_ignored(self):
        assert not fetch.Fetcher(user_agent="T/1.0", proxy_url="").session.proxies

    def test_it_does_not_set_global_proxy_variables(self, monkeypatch):
        """Google Sheets and SMTP must not be routed through the Israeli proxy.

        They use their own connections, so the only way the proxy could leak
        onto them is via the process environment. It must stay untouched.
        """
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            monkeypatch.delenv(name, raising=False)

        fetch.Fetcher(user_agent="T/1.0", proxy_url="http://proxy.example.co.il:8080")

        for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            assert name not in os.environ, f"{name} leaked into the environment"

    def test_a_second_fetcher_is_unaffected(self):
        fetch.Fetcher(user_agent="T/1.0", proxy_url="http://proxy.example.co.il:8080")
        assert not fetch.Fetcher(user_agent="T/1.0").session.proxies

    @pytest.mark.parametrize("url,expected", [
        ("http://user:secret@h:8080", "http://user:***@h:8080"),
        ("http://h:8080", "http://h:8080"),
    ])
    def test_passwords_are_redacted_for_logs(self, url, expected):
        assert fetch._redact(url) == expected
        assert "secret" not in fetch._redact(url)


# ============================================================ the hour guard

class TestHourGuardAcrossDst:
    """Israel runs UTC+3 in summer and UTC+2 in winter, so 06:00 local is
    03:00Z for half the year and 04:00Z for the other half. The task is
    scheduled at both; the guard decides which one works."""

    @pytest.mark.parametrize("when,expected", [
        (dt.datetime(2026, 7, 1, 3, 0, tzinfo=UTC), True),    # 06:00 IDT
        (dt.datetime(2026, 7, 1, 4, 0, tzinfo=UTC), False),   # 07:00 IDT
        (dt.datetime(2026, 12, 1, 3, 0, tzinfo=UTC), False),  # 05:00 IST
        (dt.datetime(2026, 12, 1, 4, 0, tzinfo=UTC), True),   # 06:00 IST
    ])
    def test_both_sides_of_the_change(self, when, expected):
        assert sync._is_scheduled_hour(6, "Asia/Jerusalem", when) is expected

    @pytest.mark.parametrize("day", [
        dt.date(2026, 1, 15),    # deep winter
        dt.date(2026, 3, 26),    # day before the spring change
        dt.date(2026, 3, 28),    # day after
        dt.date(2026, 7, 1),     # deep summer
        dt.date(2026, 10, 24),   # day before the autumn change
        dt.date(2026, 10, 26),   # day after
        dt.date(2027, 2, 2),     # next year
    ])
    def test_exactly_one_of_the_two_cron_entries_runs(self, day):
        """The property the whole scheme rests on: never twice, never zero."""
        fires = [
            sync._is_scheduled_hour(
                6, "Asia/Jerusalem",
                dt.datetime(day.year, day.month, day.day, utc_hour, 0, tzinfo=UTC))
            for utc_hour in (3, 4)
        ]
        assert sum(fires) == 1, f"{day}: {fires}"

    def test_it_really_is_six_in_israel_when_it_runs(self, day=dt.date(2026, 7, 1)):
        for utc_hour in (3, 4):
            when = dt.datetime(day.year, day.month, day.day, utc_hour, 0, tzinfo=UTC)
            if sync._is_scheduled_hour(6, "Asia/Jerusalem", when):
                assert when.astimezone(ISRAEL).hour == 6

    def test_unknown_timezone_runs_rather_than_skips(self):
        # Never syncing is worse than syncing at the wrong hour.
        assert sync._is_scheduled_hour(6, "Mars/Olympus_Mons") is True

    def test_the_hour_is_validated(self):
        with pytest.raises(SystemExit):
            sync._parse_args(["--only-at-hour", "24"])

    def test_default_timezone_is_israel(self):
        assert sync._parse_args([]).tz == "Asia/Jerusalem"

    def test_guard_is_off_unless_asked_for(self):
        assert sync._parse_args([]).only_at_hour is None


# ================================================================== email

class Recorder:
    """Stands in for smtplib, capturing what would have been sent."""

    def __init__(self, fail=False):
        self.fail = fail
        self.sent = []
        self.logged_in = None

    def __call__(self, host, port, **kwargs):
        if self.fail:
            raise OSError("connection refused")
        self.host, self.port = host, port
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        pass

    def starttls(self, **kwargs):
        pass

    def login(self, user, password):
        self.logged_in = user

    def send_message(self, message):
        self.sent.append(message)


@pytest.fixture
def mail_env(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")
    monkeypatch.setenv("NOTIFY_FROM", "bot@example.com")
    monkeypatch.setenv("NOTIFY_TO", "anat@example.com")
    monkeypatch.delenv("NOTIFY_FAILURE_TO", raising=False)


COUNTS = {"devices_found": 321, "new_devices": 2,
          "prices_changed": 47, "deactivated": 1}
WHEN = dt.datetime(2026, 9, 23, 6, 0, tzinfo=ISRAEL)


class TestSuccessEmail:
    def test_subject_names_the_source_and_date(self):
        subject, _, _ = notify.build_success(COUNTS, when=WHEN)
        assert subject == "KSP prices updated - 23 Sep 2026"

    def test_body_carries_every_count(self):
        _, plain, _ = notify.build_success(COUNTS, when=WHEN)
        for value in ("321", "2", "47", "1"):
            assert value in plain
        assert "06:00 Israel time" in plain

    def test_body_links_to_the_sheet(self):
        _, plain, html = notify.build_success(COUNTS, spreadsheet_id="SHEET123", when=WHEN)
        assert "docs.google.com/spreadsheets/d/SHEET123" in plain
        assert "docs.google.com/spreadsheets/d/SHEET123" in html

    def test_there_is_an_html_alternative(self):
        _, plain, html = notify.build_success(COUNTS, when=WHEN)
        assert html.startswith("<!doctype html>") and "<table" in html
        assert "<" not in plain.split("\n")[0]

    def test_a_quiet_day_says_so(self):
        _, plain, _ = notify.build_success(
            {"devices_found": 321, "new_devices": 0,
             "prices_changed": 0, "deactivated": 0}, when=WHEN)
        assert "No prices moved" in plain

    def test_it_goes_to_notify_to(self, mail_env, monkeypatch):
        recorder = Recorder()
        monkeypatch.setattr(notify.smtplib, "SMTP", recorder)
        assert notify.send_success(COUNTS, when=WHEN) is True
        assert recorder.sent[0]["To"] == "anat@example.com"
        assert recorder.logged_in == "bot@example.com"


class TestFailureEmail:
    def test_subject_says_failed(self):
        subject, _, _ = notify.build_failure(1, "403 from Cloudflare", when=WHEN)
        assert "FAILED" in subject and "23 Sep 2026" in subject

    def test_it_states_plainly_that_the_sheet_is_unchanged(self):
        _, plain, html = notify.build_failure(1, "403", when=WHEN)
        assert "SHEET WAS NOT CHANGED" in plain
        assert "last good prices" in plain
        # The same reassurance has to survive into the HTML part, with the
        # markup and line wrapping taken out before looking.
        stripped = " ".join(re.sub(r"<[^>]+>", " ", html).split())
        assert "Google Sheet was not changed" in stripped
        assert "last good prices" in stripped

    def test_it_gives_a_one_sentence_reason_and_the_exit_code(self):
        _, plain, _ = notify.build_failure(1, "403 from Cloudflare", when=WHEN)
        assert "could not be fetched" in plain
        assert "Exit code: 1" in plain

    @pytest.mark.parametrize("code,fragment", [
        (1, "could not be fetched"),
        (2, "could not be read"),
        (3, "Google Sheet"),
        (4, "misconfigured"),
    ])
    def test_every_exit_code_has_a_reason(self, code, fragment):
        _, plain, _ = notify.build_failure(code, "", when=WHEN)
        assert fragment in plain

    def test_it_goes_to_notify_failure_to(self, mail_env, monkeypatch):
        monkeypatch.setenv("NOTIFY_FAILURE_TO", "mahnoor@example.com")
        recorder = Recorder()
        monkeypatch.setattr(notify.smtplib, "SMTP", recorder)
        notify.send_failure(1, "403", when=WHEN)
        assert recorder.sent[0]["To"] == "mahnoor@example.com"

    def test_it_falls_back_to_notify_to(self, mail_env, monkeypatch):
        recorder = Recorder()
        monkeypatch.setattr(notify.smtplib, "SMTP", recorder)
        notify.send_failure(1, "403", when=WHEN)
        assert recorder.sent[0]["To"] == "anat@example.com"


class TestMailFailuresAreContained:
    def test_a_broken_smtp_server_does_not_raise(self, mail_env, monkeypatch):
        monkeypatch.setattr(notify.smtplib, "SMTP", Recorder(fail=True))
        assert notify.send_success(COUNTS, when=WHEN) is False

    def test_no_smtp_host_is_a_quiet_skip(self, monkeypatch):
        monkeypatch.delenv("SMTP_HOST", raising=False)
        assert notify.send_success(COUNTS, when=WHEN) is False

    def test_no_recipient_is_a_quiet_skip(self, mail_env, monkeypatch):
        monkeypatch.delenv("NOTIFY_TO", raising=False)
        monkeypatch.setattr(notify.smtplib, "SMTP", Recorder())
        assert notify.send_success(COUNTS, when=WHEN) is False

    def test_is_configured_needs_host_and_recipient(self, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("NOTIFY_TO", "a@b.c")
        assert notify.is_configured() is True
        monkeypatch.delenv("NOTIFY_TO")
        assert notify.is_configured() is False

    def test_a_mail_failure_leaves_the_exit_code_alone(self, mail_env, monkeypatch):
        """The whole point: email is a side channel, not part of the result."""
        monkeypatch.setattr(notify.smtplib, "SMTP", Recorder(fail=True))

        for code in (0, 1, 2, 3, 4):
            args = sync._parse_args([])
            sync._notify(code, args, {"counts": COUNTS, "error": "x"})
        # _notify returns nothing and raises nothing; the caller's code stands.

    def test_port_465_uses_implicit_tls(self, mail_env, monkeypatch):
        monkeypatch.setenv("SMTP_PORT", "465")
        recorder = Recorder()
        monkeypatch.setattr(notify.smtplib, "SMTP_SSL", recorder)
        monkeypatch.setattr(notify.smtplib, "SMTP",
                            Recorder(fail=True))   # must not be used
        assert notify.send_success(COUNTS, when=WHEN) is True

    def test_several_recipients_are_split(self, mail_env, monkeypatch):
        monkeypatch.setenv("NOTIFY_TO", "anat@example.com, ops@example.com")
        recorder = Recorder()
        monkeypatch.setattr(notify.smtplib, "SMTP", recorder)
        notify.send_success(COUNTS, when=WHEN)
        assert recorder.sent[0]["To"] == "anat@example.com, ops@example.com"


class TestWhenNoEmailIsSent:
    def test_not_on_a_dry_run(self, mail_env, monkeypatch):
        sent = []
        monkeypatch.setattr(notify, "send_success", lambda *a, **k: sent.append(1))
        monkeypatch.setattr(notify, "send_failure", lambda *a, **k: sent.append(1))
        sync._notify(0, sync._parse_args(["--dry-run"]), {"counts": COUNTS})
        assert sent == []

    def test_not_when_email_is_unconfigured(self, monkeypatch):
        monkeypatch.delenv("SMTP_HOST", raising=False)
        sent = []
        monkeypatch.setattr(notify, "send_success", lambda *a, **k: sent.append(1))
        sync._notify(0, sync._parse_args([]), {"counts": COUNTS})
        assert sent == []

    def test_success_and_failure_take_different_paths(self, mail_env, monkeypatch):
        calls = []
        monkeypatch.setattr(notify, "send_success", lambda *a, **k: calls.append("success"))
        monkeypatch.setattr(notify, "send_failure", lambda *a, **k: calls.append("failure"))
        args = sync._parse_args([])
        sync._notify(0, args, {"counts": COUNTS})
        sync._notify(3, args, {"error": "sheet down"})
        assert calls == ["success", "failure"]

    def test_a_skipped_hour_never_reaches_the_notifier(self, mail_env, monkeypatch):
        """A guard skip is not a run: no email, and exit 0."""
        sent = []
        monkeypatch.setattr(notify, "send_success", lambda *a, **k: sent.append(1))
        monkeypatch.setattr(notify, "send_failure", lambda *a, **k: sent.append(1))
        monkeypatch.setattr(sync, "_is_scheduled_hour", lambda *a, **k: False)
        ran = []
        monkeypatch.setattr(sync, "_run", lambda *a, **k: ran.append(1))

        assert sync.main(["--only-at-hour", "6"]) == sync.EXIT_OK
        assert ran == [] and sent == []


class TestCountsForTheEmail:
    def test_counts_come_from_the_plan(self):
        class FakePlan:
            new_devices = [1, 2]
            price_updates = [1] * 47
            deactivations = [1]

        counts = sync._counts([0] * 321, FakePlan())
        assert counts == {"devices_found": 321, "new_devices": 2,
                          "prices_changed": 47, "deactivated": 1}
