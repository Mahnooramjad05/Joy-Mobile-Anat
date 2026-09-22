import json

import pytest
from conftest import fixture

import config
import sources

# KSP is now an API source. The HTML extraction paths are still used by the
# PelePhone adapter, so they are tested through a page-based source.
HTML_SETTINGS = {
    "label": "Test HTML source",
    "url": "https://example.test/trade-in/",
    "currency": "ILS",
    "json_paths": ["props.pageProps.devices", "data.devices", "devices", "items"],
    "json_fields": {
        "manufacturer": ["manufacturer", "brand", "vendor"],
        "model": ["model", "name", "title"],
        "storage": ["storage", "capacity", "size"],
        "price": ["price", "value", "amount"],
        "condition": ["condition", "grade", "state"],
    },
    "selectors": {
        "device_row": ".device-card, [data-device], .tradein-item",
        "manufacturer": ".brand, [data-brand]",
        "model": ".model, .title, h3",
        "storage": ".storage, .capacity",
        "price": ".price, .value, [data-price]",
    },
}


@pytest.fixture
def page():
    """A page-scraping source (the PelePhone-style adapter)."""
    return sources.get_source("testsrc", HTML_SETTINGS)


@pytest.fixture
def ksp():
    """The real KSP catalogue-API source."""
    return sources.get_source("ksp", config.source_config("ksp"))


@pytest.fixture
def ksp_payload():
    return fixture("ksp_api_devices.json")


# =========================================================== page extraction

class TestEmbeddedJson:
    def test_reads_devices(self, page):
        devices = page.parse(fixture("embedded_json.html"))
        assert page.strategy == "json"
        assert len(devices) == 11          # 13 rows, 2 unusable

    def test_normalises_hebrew_brands(self, page):
        brands = {d.manufacturer for d in page.parse(fixture("embedded_json.html"))}
        assert brands == {"Apple", "Samsung", "Google", "OnePlus", "Xiaomi"}

    def test_skips_row_without_price(self, page):
        devices = page.parse(fixture("embedded_json.html"))
        assert not any(d.model == "iPhone 12 mini" for d in devices)

    def test_skips_unknown_brand(self, page):
        devices = page.parse(fixture("embedded_json.html"))
        assert not any("Zork" in d.model for d in devices)

    def test_parses_price_with_thousands_separator(self, page):
        devices = page.parse(fixture("embedded_json.html"))
        assert next(d for d in devices if d.model == "iPhone 14 Pro").price == 1392.0

    def test_normalises_storage_units(self, page):
        storages = {d.storage for d in page.parse(fixture("embedded_json.html"))}
        assert "256GB" in storages and "1TB" in storages


class TestCssFallback:
    def test_reads_devices_when_no_json_present(self, page):
        devices = page.parse(fixture("css_cards.html"))
        assert page.strategy == "css"
        # 12 cards carry a price; one of those has an unknown brand and is skipped.
        assert len(devices) == 11

    def test_strips_currency_symbol(self, page):
        devices = page.parse(fixture("css_cards.html"))
        assert all(isinstance(d.price, float) and d.price > 0 for d in devices)


class TestPageFailureIsLoud:
    def test_raises_when_page_has_no_catalogue(self, page):
        with pytest.raises(sources.ParseError) as err:
            page.parse(fixture("js_only.html"))
        assert "--capture" in str(err.value)

    def test_raises_on_empty_html(self, page):
        with pytest.raises(sources.ParseError):
            page.parse("")


class TestRealKspShell:
    """The real kspTradeIn page, saved from a browser on 2026-09-21.

    It holds the page shell only: the catalogue arrives later from the API.
    A page-scraping adapter must fail loudly on it, never write an empty
    catalogue to the sheet.
    """

    def test_shell_yields_no_devices(self, page):
        with pytest.raises(sources.ParseError):
            page.parse(fixture("ksp_shell.html"))

    def test_shell_contains_no_prices_at_all(self):
        html = fixture("ksp_shell.html")
        assert "₪" not in html and "iPhone" not in html and "Galaxy" not in html

    def test_shell_reveals_the_micro_frontend_entry_point(self):
        html = fixture("ksp_shell.html")
        assert "mfe" in html and "trade-in" in html and "gateway" in html
        assert "PublicQuoteCalculator" in html


# ============================================================= KSP API source

class TestKspApiConfig:
    def test_ksp_is_an_api_source(self, ksp):
        assert isinstance(ksp, sources.KspApiSource)

    def test_endpoint_is_the_verified_one(self):
        assert config.source_config("ksp")["url"] == \
            "https://ksp.co.il/snif/TradeIn/INNER_new/api/public/devices"

    def test_grade_mapping_matches_phase_1_conditions(self):
        assert sources.KspApiSource.GRADE_TO_CONDITION == {
            "A": "Like New", "B": "Intact", "C": "Cracked", "D": "Faulty",
        }

    def test_unknown_source_is_rejected(self):
        with pytest.raises(KeyError):
            config.source_config("nosuchsite")


class TestKspApiParsing:
    def test_reads_the_real_catalogue(self, ksp, ksp_payload):
        devices = ksp.parse(ksp_payload)
        assert ksp.strategy == "api"
        assert len(devices) == 321        # phones; 167 tablets excluded, 1 duplicate merged

    def test_every_device_has_all_four_conditions(self, ksp, ksp_payload):
        for d in ksp.parse(ksp_payload):
            assert set(d.condition_prices) == {"Like New", "Intact", "Cracked", "Faulty"}

    def test_prices_are_positive_and_ordered(self, ksp, ksp_payload):
        for d in ksp.parse(ksp_payload):
            p = d.condition_prices
            assert all(v > 0 for v in p.values())
            assert p["Like New"] >= p["Intact"] >= p["Cracked"] >= p["Faulty"]

    def test_base_price_is_the_like_new_price(self, ksp, ksp_payload):
        for d in ksp.parse(ksp_payload):
            assert d.price == d.condition_prices["Like New"]

    def test_multipliers_are_relative_to_like_new(self, ksp, ksp_payload):
        for d in ksp.parse(ksp_payload):
            assert d.condition_multipliers["Like New"] == 1.0
            assert all(0 < m <= 1.0 for m in d.condition_multipliers.values())

    def test_currency_is_shekels(self, ksp, ksp_payload):
        assert all(d.currency == "ILS" for d in ksp.parse(ksp_payload))

    def test_manufacturers_are_the_five_ksp_carries(self, ksp, ksp_payload):
        brands = {d.manufacturer for d in ksp.parse(ksp_payload)}
        assert brands == {"Apple", "Samsung", "Google", "OnePlus", "Oppo"}

    def test_tablets_are_excluded_by_default(self, ksp, ksp_payload):
        assert not any("iPad" in d.model for d in ksp.parse(ksp_payload))

    def test_tablets_can_be_included(self, ksp_payload):
        settings = dict(config.source_config("ksp"), exclude_types=[])
        devices = sources.get_source("ksp", settings).parse(ksp_payload)
        assert len(devices) == 488
        assert any("iPad" in d.model for d in devices)

    def test_no_duplicate_device_keys(self, ksp, ksp_payload):
        keys = [d.key for d in ksp.parse(ksp_payload)]
        assert len(keys) == len(set(keys))


class TestKspModelCleaning:
    def _find(self, devices, model, storage):
        return next((d for d in devices if d.model == model and d.storage == storage), None)

    def test_trailing_year_becomes_release_year(self, ksp, ksp_payload):
        d = self._find(ksp.parse(ksp_payload), "iPhone 15 Pro", "128GB")
        assert d is not None
        assert d.release_year == 2023

    def test_samsung_gets_its_galaxy_prefix_back(self, ksp, ksp_payload):
        devices = ksp.parse(ksp_payload)
        samsung = [d.model for d in devices if d.manufacturer == "Samsung"]
        assert samsung and all(m.startswith("Galaxy") for m in samsung)
        assert self._find(devices, "Galaxy S23", "128GB") is not None

    def test_network_generation_is_preserved(self, ksp, ksp_payload):
        # KSP prices "A51" and "A51 (5G)" differently, so they must stay apart.
        models = {d.model for d in ksp.parse(ksp_payload)}
        assert "Galaxy A51" in models and "Galaxy A51 (5G)" in models

    def test_repeated_name_is_collapsed(self, ksp, ksp_payload):
        models = {d.model for d in ksp.parse(ksp_payload)}
        assert "OnePlus 11" in models
        assert "OnePlus 11 OnePlus 11" not in models

    def test_oneplus_keeps_its_brand_in_the_model(self, ksp, ksp_payload):
        # Matches the Phase 1 sheet, which has "OnePlus 12".
        assert self._find(ksp.parse(ksp_payload), "OnePlus 12", "512GB") is not None


class TestKspMultiSupplier:
    def test_best_supplier_offer_wins(self, ksp, ksp_payload):
        # S23 FE 256GB is listed twice: supplier 2 at A=296, supplier 4 at A=411.
        d = next(d for d in ksp.parse(ksp_payload)
                 if d.model == "Galaxy S23 FE" and d.storage == "256GB")
        assert d.price == 411.0


class TestKspApiFailureIsLoud:
    def test_html_instead_of_json_is_reported(self, ksp):
        with pytest.raises(sources.ParseError) as err:
            ksp.parse("<html><body>Forbidden</body></html>")
        assert "not JSON" in str(err.value)

    def test_missing_catalog_key_is_reported(self, ksp):
        with pytest.raises(sources.ParseError) as err:
            ksp.parse(json.dumps({"something_else": {}}))
        assert "catalog" in str(err.value)

    def test_empty_catalog_is_reported(self, ksp):
        with pytest.raises(sources.ParseError):
            ksp.parse(json.dumps({"catalog": {}}))

    def test_catalog_of_junk_is_reported(self, ksp):
        payload = {"catalog": {"Nosuchbrand": {"Whatever": {"128 GB": [{"prices": {}}]}}}}
        with pytest.raises(sources.ParseError):
            ksp.parse(json.dumps(payload))

    def test_entry_without_prices_is_skipped_not_fatal(self, ksp, ksp_payload):
        payload = json.loads(ksp_payload)
        payload["catalog"]["Apple"]["Broken Model"] = {"128 GB": [{"Type": "Mobile Phone"}]}
        devices = ksp.parse(json.dumps(payload))
        assert len(devices) == 321
        assert ksp.skipped >= 1


class TestKspAmbiguousModelNames:
    """KSP sells three different "iPhone SE" generations at different prices.

    The trailing year is normally redundant and moved into release_year, but
    where it is the only thing separating two models it has to stay in the
    name -- otherwise three devices collapse into one and two of the three
    prices are lost.
    """

    def test_iphone_se_generations_stay_apart(self, ksp, ksp_payload):
        devices = ksp.parse(ksp_payload)
        se = {d.model for d in devices if "iPhone SE" in d.model}
        assert se == {"iPhone SE (2016)", "iPhone SE (2020)", "iPhone SE (2022)"}

    def test_their_prices_differ(self, ksp, ksp_payload):
        devices = ksp.parse(ksp_payload)
        by_model = {d.model: d.price for d in devices
                    if "iPhone SE" in d.model and d.storage == "128GB"}
        assert by_model["iPhone SE (2016)"] != by_model["iPhone SE (2020)"]

    def test_release_year_is_still_populated(self, ksp, ksp_payload):
        devices = ksp.parse(ksp_payload)
        se = next(d for d in devices if d.model == "iPhone SE (2020)")
        assert se.release_year == 2020

    def test_unambiguous_names_still_drop_the_year(self, ksp, ksp_payload):
        models = {d.model for d in ksp.parse(ksp_payload)}
        assert "iPhone 15 Pro" in models
        assert "iPhone 15 Pro (2023)" not in models
