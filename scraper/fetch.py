"""HTTP fetching with retries, backoff and polite delays.

Every network request the scraper makes goes through Fetcher, so the retry
policy, the delay between requests and the User-Agent are defined in exactly
one place.
"""

from __future__ import annotations

import logging
import random
import time

import requests

log = logging.getLogger(__name__)


class FetchError(RuntimeError):
    """A request failed after exhausting retries, or was refused outright."""


class Fetcher:
    def __init__(self, *, user_agent, accept_language="he-IL,he;q=0.9,en;q=0.8",
                 delay_seconds=2.0, timeout=30, max_retries=3, backoff_seconds=5.0):
        self.delay_seconds = delay_seconds
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self._last_request_at = 0.0

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": accept_language,
        })

    def get(self, url, **kwargs):
        """GET a URL, retrying transient failures. Returns the response body."""
        self._wait_turn()

        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                clean = {k: v for k, v in kwargs.items() if v is not None}
                response = self.session.get(url, timeout=self.timeout, **clean)
            except requests.RequestException as err:
                last_error = f"{type(err).__name__}: {err}"
                log.warning("attempt %d/%d failed for %s — %s",
                            attempt, self.max_retries, url, last_error)
            else:
                if response.status_code == 200:
                    return response.text

                last_error = f"HTTP {response.status_code}"

                # 403/404 will not fix themselves; retrying only adds load.
                if response.status_code in (401, 403, 404, 451):
                    raise FetchError(
                        f"{url} returned {response.status_code}. "
                        f"{_explain_status(response)}"
                    )

                log.warning("attempt %d/%d got %s for %s",
                            attempt, self.max_retries, last_error, url)

            if attempt < self.max_retries:
                pause = self.backoff_seconds * (2 ** (attempt - 1))
                pause += random.uniform(0, 1)  # jitter, so retries do not sync up
                log.info("retrying in %.1fs", pause)
                time.sleep(pause)

        raise FetchError(f"{url} failed after {self.max_retries} attempts — {last_error}")

    def _wait_turn(self):
        """Keep at least delay_seconds between requests."""
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.delay_seconds:
            time.sleep(self.delay_seconds - elapsed)
        self._last_request_at = time.monotonic()


def _explain_status(response):
    """Turn a refusal into something actionable in the log."""
    server = response.headers.get("Server", "").lower()
    body = (response.text or "")[:4000].lower()

    if "cloudflare" in server or "__cf" in body or "cf-ray" in response.headers.get("CF-RAY", "").lower():
        hint = "Blocked by Cloudflare."
    elif "incapsula" in body or "x-iinfo" in {k.lower() for k in response.headers}:
        hint = "Blocked by Imperva/Incapsula."
    else:
        hint = "Request refused."

    return (hint + " Israeli retail sites commonly restrict traffic by country; "
            "check whether this network can reach the site in a browser, and see "
            "docs/scraper.md, 'When scraping breaks'.")
