"""Source adapters: fetch a trade-in page and return devices.

Adding a third source means adding a dict to config.SOURCES. Both adapters here
share one extraction strategy, because both sites are built the same way:

  1. Look for the catalogue as JSON embedded in the page (__NEXT_DATA__, an
     __INITIAL_STATE__ assignment, or any <script type="application/json">).
     Sites of this kind usually render from such a blob, and reading it is far
     steadier than CSS selectors -- it survives restyling.
  2. Fall back to the CSS selectors in config.SOURCES[...]["selectors"].

Whichever path produced the devices is reported, so a log line always says how
the data was obtained.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from bs4 import BeautifulSoup

import normalize

log = logging.getLogger(__name__)


class ParseError(RuntimeError):
    """The page was fetched but no devices could be read from it."""


@dataclass
class ScrapedDevice:
    manufacturer: str
    model: str
    storage: str
    price: float
    currency: str
    source: str
    source_url: str
    condition: Optional[str] = None
    release_year: Optional[int] = None
    # condition name -> price, when the source publishes real per-condition
    # prices (KSP does). `price` is then the best-condition price.
    condition_prices: dict = field(default_factory=dict)
    # condition name -> price / best-condition price. Computed in the source
    # currency before any rounding, so converting to EUR cannot distort it.
    condition_multipliers: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    @property
    def key(self):
        return normalize.match_key(self.manufacturer, self.model, self.storage)

    def as_dict(self):
        return {
            "manufacturer": self.manufacturer,
            "model_name": self.model,
            "storage": self.storage,
            "release_year": self.release_year,
            "price": self.price,
            "currency": self.currency,
            "condition": self.condition,
            "condition_prices": self.condition_prices or None,
            "condition_multipliers": self.condition_multipliers or None,
            "source": self.source,
            "source_url": self.source_url,
        }


class Source:
    def __init__(self, name, settings):
        self.name = name
        self.settings = settings
        self.url = settings["url"]
        self.currency = settings.get("currency", "ILS")
        self.strategy = None   # set during parse: "json" or "css"

    def scrape(self, fetcher):
        html = fetcher.get(self.url)
        return self.parse(html)

    def parse(self, html):
        devices = self._parse_json(html)
        if devices:
            self.strategy = "json"
            log.info("%s: read %d devices from embedded JSON", self.name, len(devices))
            return devices

        devices = self._parse_css(html)
        if devices:
            self.strategy = "css"
            log.info("%s: read %d devices via CSS selectors", self.name, len(devices))
            return devices

        raise ParseError(
            f"{self.name}: fetched {self.url} but found no devices. Either the page "
            f"renders its catalogue in the browser (so the HTML has none), or the "
            f"selectors in config.py no longer match. Run with --capture and see "
            f"docs/scraper.md, 'When scraping breaks'."
        )

    # ------------------------------------------------------------------ JSON

    def _parse_json(self, html):
        for blob in _candidate_json_blobs(html):
            for path in self.settings.get("json_paths", []):
                rows = _dig(blob, path)
                if isinstance(rows, list) and rows:
                    devices = self._devices_from_rows(rows)
                    if devices:
                        log.debug("%s: matched json path %s", self.name, path)
                        return devices

            # No configured path matched -- look for any list of dicts that
            # carries both a name-ish and a price-ish field.
            rows = _find_device_list(blob)
            if rows:
                devices = self._devices_from_rows(rows)
                if devices:
                    log.info("%s: found devices by scanning JSON, not via a configured "
                             "json_path -- consider pinning the path in config.py", self.name)
                    return devices
        return []

    def _devices_from_rows(self, rows):
        fields = self.settings.get("json_fields", {})
        devices, skipped = [], 0

        for row in rows:
            if not isinstance(row, dict):
                continue
            flat = _flatten(row)
            try:
                raw_model = _first_present(flat, fields.get("model", []))
                raw_price = _first_present(flat, fields.get("price", []))
                if raw_model is None or raw_price is None:
                    skipped += 1
                    continue

                raw_manufacturer = _first_present(flat, fields.get("manufacturer", [])) or raw_model
                raw_storage = _first_present(flat, fields.get("storage", []))

                manufacturer = normalize.manufacturer(str(raw_manufacturer))
                storage = normalize.storage(str(raw_storage if raw_storage is not None else raw_model))
                model = normalize.model(str(raw_model), manufacturer)
                price = normalize.price(raw_price)

                condition = _first_present(flat, fields.get("condition", []))
                devices.append(ScrapedDevice(
                    manufacturer=manufacturer, model=model, storage=storage,
                    price=price, currency=self.currency, source=self.name,
                    source_url=self.url,
                    condition=str(condition) if condition is not None else None,
                    raw=row,
                ))
            except normalize.NormalizeError as err:
                log.debug("%s: skipped a row -- %s", self.name, err)
                skipped += 1

        if skipped:
            log.info("%s: skipped %d unreadable rows", self.name, skipped)
        return devices

    # ------------------------------------------------------------------- CSS

    def _parse_css(self, html):
        selectors = self.settings.get("selectors", {})
        if not selectors.get("device_row"):
            return []

        soup = BeautifulSoup(html, "lxml")
        rows = soup.select(selectors["device_row"])
        if not rows:
            return []

        devices, skipped = [], 0
        for row in rows:
            try:
                model_text = _select_text(row, selectors.get("model")) or row.get_text(" ", strip=True)
                price_text = _select_text(row, selectors.get("price"))
                if not price_text:
                    skipped += 1
                    continue

                manufacturer_text = _select_text(row, selectors.get("manufacturer")) or model_text
                storage_text = _select_text(row, selectors.get("storage")) or model_text

                manufacturer = normalize.manufacturer(manufacturer_text)
                devices.append(ScrapedDevice(
                    manufacturer=manufacturer,
                    model=normalize.model(model_text, manufacturer),
                    storage=normalize.storage(storage_text),
                    price=normalize.price(price_text),
                    currency=self.currency, source=self.name, source_url=self.url,
                    raw={"html": str(row)[:500]},
                ))
            except normalize.NormalizeError as err:
                log.debug("%s: skipped a row -- %s", self.name, err)
                skipped += 1

        if skipped:
            log.info("%s: skipped %d unreadable rows", self.name, skipped)
        return devices


class KspApiSource:
    """KSP's public trade-in catalogue API.

    One GET, no parameters, no authentication:

        GET https://ksp.co.il/snif/TradeIn/INNER_new/api/public/devices

    Response shape (KSP's own bundle documents it as
    "manufacturer -> model -> storage -> devices"):

        {"catalog": {"Apple": {"iPhone 15 Pro (2023)": {"128 GB": [ {...} ]}}}}

    Each leaf object carries prices for four grades, A/B/C/D. Per KSP's
    bundle: "full and no_vat both carry the VAT-free supplier-reduced value;
    clean_price is the raw supplier grade price". The public calculator renders
    `₪` + `price.full`, and the branch tool formats `full` as "ILS", so `full`
    is the customer-facing number and the currency is shekels.

    See docs/ksp-api.md for the full endpoint documentation.
    """

    # KSP's grade letters, with the labels its own UI shows, mapped onto the
    # Phase 1 condition names. The mapping is taken from the tooltips in KSP's
    # public calculator, not guessed -- see docs/ksp-api.md.
    GRADE_TO_CONDITION = {
        "A": "Like New",   # "As New" -- passes all checks, no meaningful wear
        "B": "Intact",     # "Well Kept" -- no crack or fracture, minor use
        "C": "Cracked",    # "Cosmetic Damage" -- crack, fracture or clear wear
        "D": "Faulty",     # "Below Average" -- failed a check or major fault
    }
    BEST_GRADE = "A"

    _TRAILING_YEAR = re.compile(r"\s*\((19|20)\d{2}\)\s*$")

    def __init__(self, name, settings):
        self.name = name
        self.settings = settings
        self.url = settings["url"]
        self.currency = settings.get("currency", "ILS")
        self.strategy = None
        self.skipped = 0

    def scrape(self, fetcher):
        headers = {}
        referer = self.settings.get("referer")
        if referer:
            headers["Referer"] = referer
        return self.parse(fetcher.get(self.url, headers=headers or None))

    def parse(self, text):
        try:
            payload = json.loads(text)
        except ValueError as err:
            raise ParseError(
                f"{self.name}: response was not JSON ({err}). If this is an HTML "
                f"block page, the request was refused -- see docs/scraper.md."
            ) from err

        catalog = payload.get("catalog")
        if not isinstance(catalog, dict) or not catalog:
            raise ParseError(
                f"{self.name}: the response has no 'catalog' object. KSP may have "
                f"changed the API shape; see docs/ksp-api.md for what it should look like."
            )

        exclude_types = {t.lower() for t in self.settings.get("exclude_types", [])}
        self.skipped = 0

        # Pass 1: pick one supplier entry per manufacturer/model/storage.
        candidates = []
        for raw_manufacturer, models in catalog.items():
            if not isinstance(models, dict):
                continue
            for raw_model, storages in models.items():
                if not isinstance(storages, dict):
                    continue
                for raw_storage, entries in storages.items():
                    entry = self._pick_entry(entries, exclude_types)
                    if entry is None:
                        self.skipped += 1
                        continue
                    candidates.append((raw_manufacturer, raw_model, raw_storage, entry))

        # Pass 2: decide which model names must keep their year. KSP sells
        # "iPhone SE (2016)", "(2020)" and "(2022)" at quite different prices,
        # so there the year is the only thing telling them apart and has to
        # stay in the name. Where a name carries just one year it is redundant.
        years = {}
        for raw_manufacturer, raw_model, _storage, _entry in candidates:
            base, year = self._split_year(raw_model)
            years.setdefault((raw_manufacturer, base.lower()), set()).add(year)
        keep_year = {key for key, found in years.items() if len(found) > 1}

        # Pass 3: build devices.
        devices = []
        for raw_manufacturer, raw_model, raw_storage, entry in candidates:
            device = self._build(raw_manufacturer, raw_model, raw_storage, entry, keep_year)
            if device:
                devices.append(device)

        # Pass 4: KSP occasionally lists one device twice under different
        # spellings ("Galaxy Z Flip4 5G" and "Z Flip 4 (5G)") at different
        # prices. Keep the better offer, the way the KSP calculator does.
        best = {}
        for device in devices:
            existing = best.get(device.key)
            if existing is None or device.price > existing.price:
                if existing is not None:
                    log.debug("%s: %r duplicates %r -- keeping the higher price",
                              self.name, device.model, existing.model)
                best[device.key] = device
        merged = len(devices) - len(best)
        devices = list(best.values())

        if not devices:
            raise ParseError(f"{self.name}: catalog parsed but no usable devices in it.")

        self.strategy = "api"
        log.info("%s: read %d devices from the catalogue API "
                 "(%d skipped, %d duplicate listings merged)",
                 self.name, len(devices), self.skipped, merged)
        return devices

    def _pick_entry(self, entries, exclude_types):
        """The best usable supplier entry for one model+storage, or None.

        A model+storage can be listed by several suppliers at different prices,
        and the KSP calculator works from the best offer, so take the highest.
        """
        if not isinstance(entries, list) or not entries:
            return None
        usable = [e for e in entries
                  if isinstance(e, dict)
                  and str(e.get("Type", "")).lower() not in exclude_types
                  and isinstance(e.get("prices"), dict)
                  and _grade_price(e, self.BEST_GRADE) is not None]
        if not usable:
            return None
        return max(usable, key=lambda e: _grade_price(e, self.BEST_GRADE))

    def _build(self, raw_manufacturer, raw_model, raw_storage, entry, keep_year):
        try:
            manufacturer = normalize.manufacturer(raw_manufacturer)
            storage = normalize.storage(raw_storage)
            model, release_year = self._clean_model(raw_model, manufacturer, keep_year,
                                                    raw_manufacturer)
        except normalize.NormalizeError as err:
            log.debug("%s: skipped %r/%r/%r -- %s",
                      self.name, raw_manufacturer, raw_model, raw_storage, err)
            self.skipped += 1
            return None

        condition_prices = {}
        for grade, condition in self.GRADE_TO_CONDITION.items():
            value = _grade_price(entry, grade)
            if value is not None:
                condition_prices[condition] = float(value)

        best = condition_prices.get(self.GRADE_TO_CONDITION[self.BEST_GRADE])
        if not best:
            self.skipped += 1
            return None

        # Ratios are taken here, in shekels, before the EUR conversion rounds
        # anything: a 1 ILS grade price rounds to 0 EUR and would otherwise
        # produce a zero multiplier.
        multipliers = {c: round(p / best, 4) for c, p in condition_prices.items()}

        return ScrapedDevice(
            manufacturer=manufacturer, model=model, storage=storage,
            price=best, currency=self.currency, source=self.name,
            source_url=self.url, release_year=release_year,
            condition_prices=condition_prices, condition_multipliers=multipliers,
            raw={k: entry.get(k) for k in ("row_id", "supplier_id", "DescriptiveLabel", "Type")},
        )

    def _split_year(self, raw_model):
        """"iPhone SE (2020)" -> ("iPhone SE", 2020); no year -> (name, None)."""
        text = re.sub(r"\s+", " ", str(raw_model)).strip()
        match = self._TRAILING_YEAR.search(text)
        if not match:
            return text, None
        return self._TRAILING_YEAR.sub("", text).strip(), int(match.group(0).strip(" ()"))

    def _clean_model(self, raw_model, manufacturer, keep_year=frozenset(),
                     raw_manufacturer=None):
        """Tidy a KSP model name without changing which device it refers to.

        Deliberately does NOT strip 4G/5G/LTE: KSP lists "A51" and "A51 (5G)"
        as separate entries at different prices, so collapsing them would merge
        two real SKUs into one wrong price. The trailing year moves into
        release_year only when it is not the one thing distinguishing two
        models -- see pass 2 of parse().
        """
        base, release_year = self._split_year(raw_model)
        ambiguous = (raw_manufacturer or manufacturer, base.lower()) in keep_year
        text = str(raw_model).strip() if ambiguous else base

        text = re.sub(r"\s+", " ", text).strip(" -,")

        # "OnePlus 11 OnePlus 11" -> "OnePlus 11"
        words = text.split()
        half = len(words) // 2
        if words and len(words) % 2 == 0 and words[:half] == words[half:]:
            text = " ".join(words[:half])

        # KSP drops the "Galaxy" prefix Samsung devices are universally known by
        # ("S23", "A54 (5G)"), which is also how customers say them.
        if (self.settings.get("prefix_samsung_galaxy", True)
                and manufacturer == "Samsung"
                and not text.lower().startswith("galaxy")):
            text = f"Galaxy {text}"

        if not text:
            raise normalize.NormalizeError(f"model name empty after cleaning {raw_model!r}")
        return text, release_year


def _grade_price(entry, grade):
    """The customer-facing price for one grade, or None."""
    point = (entry.get("prices") or {}).get(grade)
    if not isinstance(point, dict):
        return None
    value = point.get("full")
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def get_source(name, settings):
    if settings.get("kind") == "api":
        return KspApiSource(name, settings)
    return Source(name, settings)


# --------------------------------------------------------------------- helpers

_JSON_SCRIPT = re.compile(
    r"<script[^>]*type=[\"']application/(?:ld\+)?json[\"'][^>]*>(.*?)</script>",
    re.DOTALL | re.IGNORECASE,
)
_STATE_ASSIGNMENT = re.compile(
    r"window\.(?:__INITIAL_STATE__|__NUXT__|__DATA__|__PRELOADED_STATE__)\s*=\s*(\{.*?\})\s*[;<]",
    re.DOTALL,
)


def _candidate_json_blobs(html):
    """Yield every parsed JSON object embedded in the page."""
    for match in _JSON_SCRIPT.finditer(html):
        parsed = _try_json(match.group(1))
        if parsed is not None:
            yield parsed

    for match in _STATE_ASSIGNMENT.finditer(html):
        parsed = _try_json(match.group(1))
        if parsed is not None:
            yield parsed


def _try_json(text):
    try:
        return json.loads(text.strip())
    except ValueError:
        return None


def _dig(blob, dotted_path):
    current = blob
    for part in dotted_path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _find_device_list(blob, depth=0):
    """Depth-first search for a list of dicts that looks like a device catalogue."""
    if depth > 8:
        return None

    if isinstance(blob, list):
        dicts = [item for item in blob if isinstance(item, dict)]
        if len(dicts) >= 3:
            keys = {k.lower() for item in dicts[:5] for k in _flatten(item)}
            has_name = keys & {"model", "name", "title", "modelname", "productname", "devicename"}
            has_price = keys & {"price", "value", "amount", "tradeinprice", "tradeinvalue", "maxprice"}
            if has_name and has_price:
                return dicts
        for item in blob:
            found = _find_device_list(item, depth + 1)
            if found:
                return found

    elif isinstance(blob, dict):
        for value in blob.values():
            found = _find_device_list(value, depth + 1)
            if found:
                return found

    return None


def _flatten(row, prefix="", out=None, depth=0):
    """Flatten one level of nesting so 'brand.name' is reachable as 'name' too."""
    out = {} if out is None else out
    if depth > 3:
        return out
    for key, value in row.items():
        if isinstance(value, dict):
            _flatten(value, f"{prefix}{key}.", out, depth + 1)
        elif isinstance(value, list):
            continue
        else:
            out.setdefault(key, value)
            out[f"{prefix}{key}"] = value
    return out


def _first_present(flat, names):
    lowered = {k.lower(): v for k, v in flat.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value not in (None, ""):
            return value
    return None


def _select_text(element, selector):
    if not selector:
        return None
    found = element.select_one(selector)
    return found.get_text(" ", strip=True) if found else None
