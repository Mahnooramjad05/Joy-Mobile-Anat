import time

import pytest
import requests

import fetch


class FakeResponse:
    def __init__(self, status_code, text="ok", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


def make_fetcher(**kwargs):
    defaults = dict(user_agent="TestBot/1.0", delay_seconds=0,
                    backoff_seconds=0.01, max_retries=3)
    defaults.update(kwargs)
    return fetch.Fetcher(**defaults)


class TestRetries:
    def test_returns_body_on_success(self, monkeypatch):
        fetcher = make_fetcher()
        monkeypatch.setattr(fetcher.session, "get",
                            lambda *a, **k: FakeResponse(200, "<html>hi</html>"))
        assert fetcher.get("https://example.test/") == "<html>hi</html>"

    def test_retries_server_errors_then_succeeds(self, monkeypatch):
        fetcher = make_fetcher()
        calls = []

        def flaky(*args, **kwargs):
            calls.append(1)
            return FakeResponse(200, "recovered") if len(calls) == 3 else FakeResponse(503)

        monkeypatch.setattr(fetcher.session, "get", flaky)
        assert fetcher.get("https://example.test/") == "recovered"
        assert len(calls) == 3

    def test_gives_up_after_max_retries(self, monkeypatch):
        fetcher = make_fetcher(max_retries=2)
        calls = []

        def always_503(*args, **kwargs):
            calls.append(1)
            return FakeResponse(503)

        monkeypatch.setattr(fetcher.session, "get", always_503)
        with pytest.raises(fetch.FetchError):
            fetcher.get("https://example.test/")
        assert len(calls) == 2

    def test_retries_network_errors(self, monkeypatch):
        fetcher = make_fetcher()
        calls = []

        def flaky(*args, **kwargs):
            calls.append(1)
            if len(calls) < 3:
                raise requests.ConnectionError("connection reset")
            return FakeResponse(200, "recovered")

        monkeypatch.setattr(fetcher.session, "get", flaky)
        assert fetcher.get("https://example.test/") == "recovered"

    def test_timeout_is_retried(self, monkeypatch):
        fetcher = make_fetcher(max_retries=2)
        monkeypatch.setattr(fetcher.session, "get",
                            lambda *a, **k: (_ for _ in ()).throw(requests.Timeout("slow")))
        with pytest.raises(fetch.FetchError) as err:
            fetcher.get("https://example.test/")
        assert "Timeout" in str(err.value)


class TestNoPointlessRetries:
    @pytest.mark.parametrize("status", [401, 403, 404, 451])
    def test_refusals_fail_immediately(self, status, monkeypatch):
        fetcher = make_fetcher()
        calls = []

        def refuse(*args, **kwargs):
            calls.append(1)
            return FakeResponse(status, "denied")

        monkeypatch.setattr(fetcher.session, "get", refuse)
        with pytest.raises(fetch.FetchError):
            fetcher.get("https://example.test/")
        assert len(calls) == 1, "a refusal must not be retried"


class TestBlockDiagnostics:
    def test_cloudflare_block_is_named(self, monkeypatch):
        fetcher = make_fetcher()
        monkeypatch.setattr(fetcher.session, "get", lambda *a, **k: FakeResponse(
            403, "blocked", headers={"Server": "cloudflare", "CF-RAY": "abc"}))
        with pytest.raises(fetch.FetchError) as err:
            fetcher.get("https://example.test/")
        assert "Cloudflare" in str(err.value)

    def test_incapsula_block_is_named(self, monkeypatch):
        fetcher = make_fetcher()
        monkeypatch.setattr(fetcher.session, "get", lambda *a, **k: FakeResponse(
            403, "Request unsuccessful. Incapsula incident ID: 123",
            headers={"X-Iinfo": "49-1044"}))
        with pytest.raises(fetch.FetchError) as err:
            fetcher.get("https://example.test/")
        assert "Incapsula" in str(err.value)

    def test_block_message_points_at_the_docs(self, monkeypatch):
        fetcher = make_fetcher()
        monkeypatch.setattr(fetcher.session, "get",
                            lambda *a, **k: FakeResponse(403, "nope"))
        with pytest.raises(fetch.FetchError) as err:
            fetcher.get("https://example.test/")
        assert "docs/scraper.md" in str(err.value)


class TestPoliteness:
    def test_requests_are_spaced_out(self, monkeypatch):
        fetcher = make_fetcher(delay_seconds=0.25)
        monkeypatch.setattr(fetcher.session, "get", lambda *a, **k: FakeResponse(200))

        started = time.monotonic()
        fetcher.get("https://example.test/a")
        fetcher.get("https://example.test/b")
        assert time.monotonic() - started >= 0.25

    def test_user_agent_is_sent(self):
        fetcher = make_fetcher(user_agent="TradeInSyncBot/1.0 (+contact: me@example.com)")
        assert "TradeInSyncBot" in fetcher.session.headers["User-Agent"]

    def test_hebrew_accept_language_is_sent(self):
        fetcher = make_fetcher()
        assert "he" in fetcher.session.headers["Accept-Language"]
