"""Tests for the pricing API.

Everything runs offline: the device index is seeded directly, so no test touches
Google or the network. Prices are shekels throughout -- there is no currency
conversion anywhere in the service since the ILS migration.
"""

import time

import pytest

from api import conditions as conditions_module
from api import sheets_query
from api.app import app as flask_app

# Real shekel prices, exactly as KSP quotes them.
DEVICES = {
    sheets_query.device_key("Apple", "iPhone 15 Pro Max", 256): {
        "manufacturer": "Apple", "model": "iPhone 15 Pro Max",
        "storage": "256GB", "storage_gb": 256, "release_year": "2023",
        "prices_ils": {"Like New": 1607, "Intact": 1501, "Cracked": 756, "Faulty": 756},
    },
    sheets_query.device_key("Apple", "iPhone 15 Pro Max", 1024): {
        "manufacturer": "Apple", "model": "iPhone 15 Pro Max",
        "storage": "1TB", "storage_gb": 1024, "release_year": "2023",
        "prices_ils": {"Like New": 1725, "Intact": 1640, "Cracked": 765, "Faulty": 765},
    },
    sheets_query.device_key("Samsung", "Galaxy S23", 128): {
        "manufacturer": "Samsung", "model": "Galaxy S23",
        "storage": "128GB", "storage_gb": 128, "release_year": None,
        "prices_ils": {"Like New": 509, "Intact": 464, "Cracked": 200, "Faulty": 200},
    },
}

PRO_MAX_256 = {"manufacturer": "Apple", "model": "iPhone 15 Pro Max", "storage_gb": 256}


@pytest.fixture(autouse=True)
def offline():
    """Seed the device index so no test reaches Google."""
    sheets_query.seed_cache({k: dict(v) for k, v in DEVICES.items()})
    yield
    sheets_query.reset_cache()


@pytest.fixture
def client():
    return flask_app.test_client()


def prices(body, client):
    data = client.post("/api/device-price", json=body).get_json()
    return {c["english"]: c["price_ils"] for c in data["conditions"]}


# ============================================================ device lookup

class TestDeviceLookup:
    def test_known_device_returns_200(self, client):
        assert client.post("/api/device-price", json=PRO_MAX_256).status_code == 200

    def test_returns_all_four_conditions(self, client):
        data = client.post("/api/device-price", json=PRO_MAX_256).get_json()
        assert [c["english"] for c in data["conditions"]] == \
            ["Like New", "Intact", "Cracked", "Faulty"]

    def test_condition_in_the_request_does_not_filter_the_response(self, client):
        data = client.post("/api/device-price", json={
            **PRO_MAX_256, "condition": "מצב מעולה"}).get_json()
        assert len(data["conditions"]) == 4
        assert data["selected_condition"] == {"hebrew": "מצב מעולה", "english": "Like New"}

    def test_device_name_and_storage_are_echoed(self, client):
        data = client.post("/api/device-price", json=PRO_MAX_256).get_json()
        assert data["device"] == "Apple iPhone 15 Pro Max"
        assert data["storage"] == "256GB"
        assert data["storage_gb"] == 256

    def test_case_and_spacing_are_ignored(self, client):
        a = client.post("/api/device-price", json=PRO_MAX_256).get_json()
        b = client.post("/api/device-price", json={
            "manufacturer": "  apple ", "model": "IPHONE 15 PRO MAX",
            "storage_gb": 256}).get_json()
        assert a["conditions"] == b["conditions"]

    def test_storage_accepts_several_spellings(self, client):
        for storage in (256, "256", "256GB", "256 GB"):
            response = client.post("/api/device-price", json={
                **PRO_MAX_256, "storage_gb": storage})
            assert response.status_code == 200, storage

    def test_terabyte_devices_are_reachable(self, client):
        for storage in (1024, "1TB", "1 TB"):
            data = client.post("/api/device-price", json={
                **PRO_MAX_256, "storage_gb": storage}).get_json()
            assert data["storage"] == "1TB", storage

    def test_unknown_model_is_404(self, client):
        response = client.post("/api/device-price", json={
            "manufacturer": "Apple", "model": "iPhone 99", "storage_gb": 256})
        assert response.status_code == 404
        assert response.get_json() == {"error": "Device not found", "status": 404}

    def test_unstocked_storage_is_404(self, client):
        response = client.post("/api/device-price", json={**PRO_MAX_256, "storage_gb": 999})
        assert response.status_code == 404

    def test_no_fuzzy_matching(self, client):
        response = client.post("/api/device-price", json={
            "manufacturer": "Apple", "model": "iPhone 15 Pro", "storage_gb": 256})
        assert response.status_code == 404

    def test_inactive_rows_are_not_quoted(self):
        rows = [
            ["device_id", "manufacturer", "model_name", "storage", "release_year",
             "base_price_ils", "condition", "price_multiplier", "final_price_ils",
             "active", "last_updated"],
            ["X-1", "Apple", "iPhone Retired", "128GB", "2019", "100",
             "Like New", "1.0", "100", "FALSE", "2026-09-22"],
        ]
        assert sheets_query.build_index(rows) == {}

    def test_index_reads_the_final_price_column(self):
        rows = [
            ["device_id", "manufacturer", "model_name", "storage", "release_year",
             "base_price_ils", "condition", "price_multiplier", "final_price_ils",
             "active", "last_updated"],
            ["X-1", "Apple", "iPhone X", "128GB", "2019", "1607",
             "Like New", "1.0", "1607", "TRUE", "2026-09-22"],
        ]
        index = sheets_query.build_index(rows)
        entry = next(iter(index.values()))
        assert entry["prices_ils"] == {"Like New": 1607.0}


# ========================================================= condition handling

class TestConditions:
    @pytest.mark.parametrize("value,expected", [
        ("Like New", "Like New"), ("like new", "Like New"), ("  LIKE  NEW ", "Like New"),
        ("Intact", "Intact"), ("Cracked", "Cracked"), ("Faulty", "Faulty"),
        ("מצב מעולה", "Like New"), ("מצב טוב", "Intact"),
        ("מצב בעייתי", "Cracked"), ("לא תקין", "Faulty"),
    ])
    def test_accepts_both_languages(self, value, expected):
        assert conditions_module.to_english(value) == expected

    def test_accepts_ksp_own_hebrew_wording(self):
        assert conditions_module.to_english("תקול") == "Faulty"
        assert conditions_module.to_english("כחדש לחלוטין") == "Like New"

    def test_ignores_bidi_marks(self):
        assert conditions_module.to_english("‏מצב מעולה‎") == "Like New"

    def test_every_condition_maps_back_to_hebrew(self):
        for entry in conditions_module.CONDITIONS:
            assert conditions_module.to_hebrew(entry["english"]) == entry["hebrew"]

    @pytest.mark.parametrize("value", ["", "   ", None, "Mint", "שבור לגמרי", "42"])
    def test_rejects_anything_else(self, value):
        with pytest.raises(conditions_module.UnknownCondition):
            conditions_module.to_english(value)

    def test_invalid_condition_is_400_with_the_valid_ones(self, client):
        response = client.post("/api/device-price", json={**PRO_MAX_256, "condition": "Mint"})
        assert response.status_code == 400
        body = response.get_json()
        assert body["error"] == "Invalid condition"
        assert body["valid_conditions"] == ["מצב מעולה", "מצב טוב", "מצב בעייתי", "לא תקין"]

    def test_condition_is_optional(self, client):
        assert client.post("/api/device-price", json=PRO_MAX_256).status_code == 200

    def test_conditions_endpoint_lists_both_languages(self, client):
        data = client.get("/api/conditions").get_json()
        assert len(data["conditions"]) == 4
        assert all({"hebrew", "english"} <= set(c) for c in data["conditions"])


# ======================================================== hebrew in responses

class TestHebrewOutput:
    def test_every_price_carries_both_languages(self, client):
        data = client.post("/api/device-price", json=PRO_MAX_256).get_json()
        for entry in data["conditions"]:
            assert entry["hebrew"] and entry["english"]
            assert entry["currency"] == "ILS"

    def test_hebrew_is_not_escaped_in_the_payload(self, client):
        raw = client.post("/api/device-price", json=PRO_MAX_256).get_data(as_text=True)
        assert "מצב מעולה" in raw
        assert "\\u05de" not in raw


# ============================================================ currency

class TestNativeShekels:
    """Since the ILS migration the sheet stores what KSP quotes, so the API
    returns those numbers unchanged. Any arithmetic here would be a bug."""

    def test_prices_are_the_sheet_values_untouched(self, client):
        assert prices(PRO_MAX_256, client) == \
            DEVICES[sheets_query.device_key("Apple", "iPhone 15 Pro Max", 256)]["prices_ils"]

    def test_prices_are_integers(self, client):
        data = client.post("/api/device-price", json=PRO_MAX_256).get_json()
        assert all(isinstance(c["price_ils"], int) for c in data["conditions"])

    def test_response_says_shekels(self, client):
        data = client.post("/api/device-price", json=PRO_MAX_256).get_json()
        assert data["currency"] == "ILS"
        assert all(c["currency"] == "ILS" for c in data["conditions"])

    def test_no_exchange_rate_in_the_response(self, client):
        data = client.post("/api/device-price", json=PRO_MAX_256).get_json()
        assert "exchange_rate" not in data

    def test_the_service_has_no_exchange_rate_module(self):
        with pytest.raises(ImportError):
            from api import exchange_rate  # noqa: F401

    def test_better_conditions_are_never_worth_less(self, client):
        values = [c["price_ils"] for c in
                  client.post("/api/device-price", json=PRO_MAX_256).get_json()["conditions"]]
        assert values == sorted(values, reverse=True)


# ============================================================ device caching

class TestDeviceCaching:
    def test_repeated_lookups_do_not_reread_the_sheet(self, monkeypatch):
        calls = []
        monkeypatch.setattr(sheets_query, "_open_worksheet",
                            lambda: calls.append(1) or (_ for _ in ()).throw(AssertionError))
        for _ in range(10):
            sheets_query.find_device("Apple", "iPhone 15 Pro Max", 256)
        assert calls == []

    def test_the_index_expires(self, monkeypatch):
        sheets_query._cache["loaded_at"] = time.time() - sheets_query.CACHE_TTL_SECONDS - 1
        reloaded = []

        class FakeWorksheet:
            def get_all_values(self):
                reloaded.append(1)
                return [
                    ["device_id", "manufacturer", "model_name", "storage", "release_year",
                     "base_price_ils", "condition", "price_multiplier", "final_price_ils",
                     "active", "last_updated"],
                    ["A-1", "Apple", "iPhone 15 Pro Max", "256GB", "2023", "1607",
                     "Like New", "1.0", "1607", "TRUE", "2026-09-22"],
                ]

        monkeypatch.setattr(sheets_query, "_open_worksheet", lambda: FakeWorksheet())
        sheets_query.find_device("Apple", "iPhone 15 Pro Max", 256)
        assert reloaded == [1]

    def test_a_stale_index_is_served_when_the_sheet_is_unreachable(self, monkeypatch):
        sheets_query._cache["loaded_at"] = time.time() - sheets_query.CACHE_TTL_SECONDS - 1
        monkeypatch.setattr(sheets_query, "_open_worksheet",
                            lambda: (_ for _ in ()).throw(RuntimeError("google is down")))
        device = sheets_query.find_device("Apple", "iPhone 15 Pro Max", 256)
        assert device["prices_ils"]["Like New"] == 1607

    def test_sheet_failure_with_no_cache_is_a_500(self, client, monkeypatch):
        sheets_query.reset_cache()
        monkeypatch.setattr(sheets_query, "_open_worksheet",
                            lambda: (_ for _ in ()).throw(RuntimeError("google is down")))
        response = client.post("/api/device-price", json=PRO_MAX_256)
        assert response.status_code == 500
        assert response.get_json() == {"error": "Unable to fetch pricing", "status": 500}


# ============================================================ request handling

class TestRequestValidation:
    @pytest.mark.parametrize("body,missing", [
        ({}, ["manufacturer", "model", "storage_gb"]),
        ({"manufacturer": "Apple"}, ["model", "storage_gb"]),
        ({"manufacturer": "Apple", "model": "iPhone 15 Pro Max"}, ["storage_gb"]),
        ({"manufacturer": "  ", "model": "x", "storage_gb": 256}, ["manufacturer"]),
    ])
    def test_missing_fields_are_named(self, client, body, missing):
        response = client.post("/api/device-price", json=body)
        assert response.status_code == 400
        assert response.get_json()["missing_fields"] == missing

    def test_unreadable_storage_is_400_not_404(self, client):
        response = client.post("/api/device-price", json={**PRO_MAX_256, "storage_gb": "lots"})
        assert response.status_code == 400
        assert response.get_json()["error"] == "Invalid storage_gb"

    def test_non_json_body_is_400(self, client):
        response = client.post("/api/device-price", data="not json",
                               content_type="text/plain")
        assert response.status_code == 400

    def test_get_is_405(self, client):
        assert client.get("/api/device-price").status_code == 405

    def test_unknown_path_is_404_json(self, client):
        response = client.get("/nope")
        assert response.status_code == 404
        assert response.get_json()["error"] == "Not found"


class TestHealth:
    def test_health_reports_the_device_cache(self, client):
        data = client.get("/health").get_json()
        assert data["status"] == "ok"
        assert data["currency"] == "ILS"
        assert data["devices"]["loaded"] is True
        assert data["devices"]["devices"] == len(DEVICES)

    def test_health_no_longer_reports_an_exchange_rate(self, client):
        assert "exchange_rate" not in client.get("/health").get_json()


class TestIdempotenceAndSpeed:
    def test_same_request_gives_the_same_answer(self, client):
        first = client.post("/api/device-price", json=PRO_MAX_256).get_json()
        for _ in range(5):
            assert client.post("/api/device-price", json=PRO_MAX_256).get_json() == first

    def test_cached_requests_are_fast(self, client):
        client.post("/api/device-price", json=PRO_MAX_256)          # warm

        started = time.perf_counter()
        for _ in range(20):
            client.post("/api/device-price", json=PRO_MAX_256)
        average_ms = (time.perf_counter() - started) / 20 * 1000
        assert average_ms < 100, f"average {average_ms:.1f}ms"
