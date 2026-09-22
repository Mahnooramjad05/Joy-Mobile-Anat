# The KSP Trade-In API

Everything the scraper knows about KSP's catalogue endpoint. Derived from a real
browser capture (`ksp_network.har`, 2026-09-21) plus the strings KSP ships in its
own front-end bundle.

---

## The endpoint

```
GET https://ksp.co.il/snif/TradeIn/INNER_new/api/public/devices
```

| | |
| --- | --- |
| Method | `GET` |
| Query parameters | **none** |
| Authentication | **none** — no key, no token, no cookie |
| Request body | none |
| Response | `application/json`, ~270 KB |
| Requests per sync | **1** |

The only header worth sending is `Referer: https://ksp.co.il/kspTradeIn/`, which
the browser sends and the scraper reproduces. Nothing observed suggests it is
required, but it costs nothing and makes the request look like what the site
expects.

This is the one and only call the trade-in calculator makes. Of 75 requests in
the capture, 56 went to ksp.co.il and exactly one carried device data — the rest
were scripts, styles, fonts, a Cloudflare challenge, and Google Translate.

### How it was found

The page at `/kspTradeIn/` is a shell. It loads a Module Federation
micro-frontend (`/mfe/trade-in/gateway`, module `PublicQuoteCalculator`) which
then fetches the catalogue. KSP's own bundle documents the call:

> `calls: ["GET /api/public/devices"]`
> `payloadShape: [{label: "catalog", value: "manufacturer -> model -> storage -> devices"}, …]`

## Response shape

```json
{
  "catalog": {
    "Apple": {
      "iPhone 15 Pro (2023)": {
        "128 GB": [
          {
            "row_id": 979,
            "price_row_id": 1171,
            "supplier_id": 2,
            "Model": "iPhone 15 Pro (2023)",
            "DescriptiveLabel": "iPhone 15 Pro (2023),128 GB",
            "Phone": "iPhone 15 Pro (2023)",
            "Storage": "128 GB",
            "Type": "Mobile Phone",
            "Manufacturer": "Apple",
            "prices": {
              "A": { "full": 1261, "no_vat": 1261, "clean_price": 1317, "valid_for_top_up": false },
              "B": { "full": 1236, "no_vat": 1236, "clean_price": 1291, "valid_for_top_up": false },
              "C": { "full":  667, "no_vat":  667, "clean_price":  697, "valid_for_top_up": false },
              "D": { "full":  667, "no_vat":  667, "clean_price":  697, "valid_for_top_up": false }
            }
          }
        ]
      }
    }
  }
}
```

Four levels: `catalog` → manufacturer → model → storage → **list** of supplier
entries. The list is almost always one element (486 of 489); three model+storage
combinations are listed by two suppliers.

### Which price field to use

KSP's bundle states it directly:

> `{label: "price point", value: "full and no_vat both carry the VAT-free supplier-reduced value; clean_price is the raw supplier grade price"}`

- **`full`** — what the scraper uses. The public calculator renders
  `₪` followed by `price.full`, and the branch tool formats `full` as `"ILS"`.
- `no_vat` — identical to `full` in all 1,968 grade entries observed.
- `clean_price` — the raw supplier price before KSP's reduction, median 4.2%
  above `full`. Not what a customer is offered.

**Currency is ILS (shekels).** There is no currency field in the response; the
value comes from the code (`k(n.full, "ILS")`) and the `₪` in the rendered
output.

## The four grades

`prices` is keyed `A`/`B`/`C`/`D`. The labels below are KSP's own, from the
`conditions` object in its bundle — not inferred:

| Grade | KSP (English) | KSP (Hebrew) | KSP's own description | Sheet condition |
| --- | --- | --- | --- | --- |
| `A` | As New | כחדש לחלוטין | "passes all checks and looks new, with no meaningful scratches, cracks, or signs of use" | **Like New** |
| `B` | Well Kept | ללא שבר/סדק | "a working, well-kept device with no crack or fracture. Minor signs of use may appear" | **Intact** |
| `C` | Cosmetic Damage | שבר/סדק | "passes the checks but has a crack, fracture, or clear visible wear" | **Cracked** |
| `D` | Below Average | תקול | "failed one of the checks, cannot be fully tested, or has a significant fault" | **Faulty** |

The mapping onto the Phase 1 conditions is as close to exact as it could be —
each KSP description matches the corresponding Phase 1 definition almost word
for word.

All 489 devices carry all four grades, and `A ≥ B ≥ C ≥ D` holds for every one
of them. No zero or missing prices.

## What is in the catalogue

| | Count |
| --- | --- |
| Manufacturers | 5 — Apple, Samsung, Google, OnePlus, Oppo |
| Model + storage combinations | 489 |
| — phones (`Type: "Mobile Phone"` or blank) | 322 |
| — tablets (`Type: "Tablet"`) | 167 |
| Grade price points | 1,956 |
| Price range (grade A) | ₪20 – ₪2,440 |

Storage appears as `"128 GB"`, `"1 TB"`, and occasionally without the space
(`"512GB"`). `Type` is blank on 31 phones (mostly Pixel and OnePlus), so the
scraper excludes tablets by name rather than requiring `Type == "Mobile Phone"`
— otherwise those 31 would be dropped.

## Quirks worth knowing

**KSP's real condition curve is nothing like a fixed multiplier.** Median grade
ratios are B/A 0.95, C/A 0.30, D/A 0.28 — but C/A ranges from **0.04 to 0.93**
across devices. A Pixel 8 Pro drops from €400 to €33 when cracked; a Galaxy S24
Ultra drops from €1,167 to €407. Applying one fixed multiplier curve would
produce quotes wildly different from KSP's own, which is why the scraper writes
the real per-device ratios into the sheet.

**Grades B and A are often identical**, and C and D very often are. KSP appears
to price in two or three bands for many devices rather than four.

**One model+storage, several suppliers.** Three combinations are listed twice at
different prices (Galaxy S23 FE 256 GB: ₪296 from supplier 2, ₪411 from supplier
4). The scraper takes the highest, matching how KSP's own UI picks the best
supplier offer.

**Duplicate listings under different spellings.** "Galaxy Z Flip4 5G" (₪366) and
"Z Flip 4 (5G)" (₪244) are the same device entered twice. After name cleaning
they collide, and the scraper keeps the higher price.

**4G and 5G variants are genuinely different SKUs.** `A51` and `A51 (5G)` carry
different prices, as do `Note 20` (₪252) and `Note 20 (5G)` (₪309). The scraper
therefore **does not** strip `(5G)` from model names — doing so would merge two
real products into one wrong price.

**Three iPhone SE generations share a base name.** `iPhone SE (2016)` is ₪36
while `(2020)` and `(2022)` are ₪123. The trailing year is normally moved into
`release_year` and dropped from the model name, but where a base name maps to
more than one year the year stays in the name, or three devices would collapse
into one.

**Samsung models have no "Galaxy" prefix.** KSP lists `S23`, `A54 (5G)`,
`Note 10`. The scraper prepends `Galaxy ` (configurable), because that is how
customers speak and how the Phase 1 sheet is written.

**KSP's own FAQ on freshness:** "המחירון יכול להתעדכן מיום ליום" — the price
list can change from day to day. A daily sync is the right cadence.

## Rate limits and politeness

No rate-limit headers appear in the response, and no documentation exists — it
is an internal endpoint, not a published API. The scraper's behaviour:

- **one request per sync run** for the entire catalogue
- a 2-second floor between any requests
- exponential backoff on 5xx and network errors; no retry on 4xx
- a self-identifying `User-Agent` with a contact address

The response carries an `ETag` (`W/"41cab-a0Sdt+NUp4VfDXrR246X88I/69A"`) and the
captured request came back `304 Not Modified`, so conditional requests work.
Sending `If-None-Match` would make a no-change run nearly free; the scraper does
not do this yet, and at one request a day it does not need to.

## Caveats

**This is an undocumented internal endpoint.** KSP has made no commitment about
it and can change or withdraw it without notice. Three things follow:

1. The scraper fails loudly rather than silently on a shape change — a missing
   `catalog` key or a non-JSON body raises with a message pointing here.
2. `scraper/fixtures/ksp_api_devices.json` is the real captured response, and
   the test suite asserts against it. If KSP changes shape, refresh that file
   and the tests will show what moved.
3. Asking KSP for a supported feed is still worth doing. The data is clean and
   well structured, which suggests there is a real system behind it that they
   could expose properly.

**Access is geo-restricted.** From outside Israel, ksp.co.il returns HTTP 403
via Cloudflare, naming the rejected country. This applies to the API too — it
is the same host. Scheduled runs need to originate somewhere KSP accepts. See
[scraper.md](scraper.md#scheduling).

## Reproducing this analysis

```bash
# Devices, per-condition prices, and what would change in the sheet:
python -m scraper.sync --from-file scraper/fixtures/ksp_api_devices.json --dry-run

# Against the live API, from a network KSP accepts:
python -m scraper.sync --dry-run --capture
```

`--capture` writes the raw response to `captures/ksp-<timestamp>.json`, which is
what to diff against the fixture if something looks wrong.
