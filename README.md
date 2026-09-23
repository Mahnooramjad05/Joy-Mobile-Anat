# Device Trade-In Chatbot

Instant device trade-in quotes through a Heyy chatbot. A customer says what
device they have and what state it is in; the bot answers with a price.

There is no pricing engine. There is a spreadsheet, a lookup, and a multiplier.

## How it fits together

```
KSP catalogue API ──(Phase 2 sync)──▶ Google Sheet ──(Phase 3 REST API)──▶ Heyy bot ──▶ customer
        ILS                                ILS                   ILS + Hebrew
```

| Phase | What it does | Status |
| --- | --- | --- |
| 1 | Google Sheet holding devices, base prices and condition multipliers | Done — populated from KSP — [sheet-setup.md](docs/sheet-setup.md) |
| 2 | Sync that pulls KSP prices into the sheet | Done — 321 devices live, runs daily at 06:00 Israel time — [scraper.md](docs/scraper.md) |
| 3 | Pricing API in ILS with Hebrew conditions | Built and tested — [pricing-api.md](docs/pricing-api.md) |

## Pricing model

There isn't one. KSP quotes a price for each of four conditions, the sheet
stores those four numbers, and the API returns them. No conversion, no
multiplier, no rounding anywhere between KSP and the customer.

Real values for an iPhone 15 Pro Max 256GB:

| Condition | KSP grade | Quote |
| --- | --- | --- |
| מצב מעולה — Like New | A "As New" | 1,607 |
| מצב טוב — Intact | B "Well Kept" | 1,501 |
| מצב בעייתי — Cracked | C "Cosmetic Damage" | 756 |
| לא תקין — Faulty | D "Below Average" | 756 |

Prices are shekels. The sheet keeps a `price_multiplier` column, fixed at 1.0,
so the layout still works for a source that quotes one price per device instead
of four.

## What is here

```
device-trade-in-pricing.xlsx  The finished sheet — upload to Drive, open as Google Sheets
data/devices.csv              Same 60 rows as CSV, if you prefer importing tab by tab
data/conditions.csv           The four conditions and their multipliers
data/sample-scrape.json       All 321 devices as the sync extracts them
tools/generate_sample_data.py Regenerates both CSVs
tools/build_workbook.py       Rebuilds the .xlsx from the same data
tools/init_sheet_tabs.py      Builds Devices + Conditions in an empty sheet
tools/reset_devices_tab.py    Clears the Devices data rows (one-off, guarded)
tools/migrate_sheet_to_ils.py Renames the euro headers to shekels (one-off)
tools/build_installer.py      Builds the Windows installer for running the sync in Israel
installer/                    Install.bat, Uninstall.bat and the scheduled-task definition

scraper/sync.py               Phase 2 entry point — fetch, convert, update the sheet
scraper/config.py             Every knob: source, endpoint, safety limits
scraper/sources.py            KSP catalogue-API adapter, plus the PelePhone page adapter
scraper/normalize.py          Hebrew-aware brand, model, storage and price parsing
scraper/sheets.py             The Google Sheet upsert, and the rules that keep it safe
scraper/fetch.py              HTTP with retries, backoff, polite delays, optional proxy
scraper/credentials.py        Service account key from the environment or a file
scraper/notify.py             Result email over SMTP
scraper/fixtures/             The real captured KSP response, used by the tests
scraper/tests/                129 tests, all offline

api/app.py                    Phase 3 REST API — POST /api/device-price
api/conditions.py             Hebrew <-> English condition mapping
api/sheets_query.py           Device lookups, cached 30 min
api/settings.py               Env / .env configuration
wsgi.py                       Production entry point (gunicorn / Passenger)
Dockerfile                    Container image for the API
.dockerignore                 Build context exclusions, incl. every secret
tests/test_api.py             API tests, all offline
apps-script/Code.gs           Alternative serverless read API (EUR, English only)
docs/sheet-setup.md           Build the sheet, maintain it, add devices
docs/api-setup.md             Deploy the API, endpoint reference, Heyy wiring
docs/pricing-api.md           The Phase 3 API: endpoints, deployment, Heyy wiring
docs/scraper.md               Run, schedule and repair the sync
docs/ksp-api.md               The KSP API: endpoint, payload, grades, quirks
docs/phase2-ksp-sync.md       Design decisions and what is still open
```

## Getting started

1. Upload `device-trade-in-pricing.xlsx` to Drive and open it as a Google Sheet — [docs/sheet-setup.md](docs/sheet-setup.md)
2. Deploy the API and test it with curl — [docs/api-setup.md](docs/api-setup.md)
3. Point the Heyy bot at the endpoints — same document, *Connecting Heyy*
4. Set up the scraper — [docs/scraper.md](docs/scraper.md)

Steps 1–3 give a working chatbot on hand-maintained prices. Step 4 is what
stops anyone having to maintain them.

## Running the sync

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows;  source .venv/bin/activate elsewhere
pip install -r requirements.txt

# Against the real captured payload, no network needed:
python -m scraper.sync --from-file scraper/fixtures/ksp_api_devices.json --dry-run

# Against the live API:
python -m scraper.sync --dry-run

python -m pytest scraper/tests -q   # 194 tests, offline
```

KSP turned out to have a clean public catalogue API — one GET, no parameters, no
authentication, the whole catalogue as JSON, including a real price for each of
the four conditions. Nothing is scraped: there is no HTML parsing in the KSP
path at all. [docs/ksp-api.md](docs/ksp-api.md) documents it.

The sheet is populated: **321 devices, 1,284 rows**, every price verified
against KSP's API. A second run changes nothing, so a daily schedule is safe.

**One operational constraint.** ksp.co.il returns HTTP 403 via Cloudflare from
outside Israel, so the scheduled job has to run from a network KSP accepts. The
data currently in the sheet came from a captured API response; a live run needs
that network. A blocked run exits non-zero and leaves the sheet untouched.

## Running the API

```bash
pip install -r requirements.txt
cp .env.example .env

python -m api.app                                            # dev, port 5000
gunicorn --bind 0.0.0.0:5000 --workers 2 wsgi:application    # production

python -m pytest tests scraper/tests -q   # 258 tests, offline
```

```bash
curl -X POST http://localhost:5000/api/device-price      -H "Content-Type: application/json"      --data '{"manufacturer":"Apple","model":"iPhone 15 Pro Max","storage_gb":256}'
```

Returns all four conditions in shekels with Hebrew names, in well under a
millisecond once warm. Full reference and Hostinger deployment notes:
[docs/pricing-api.md](docs/pricing-api.md).

Or in a container:

```bash
docker build -t joy-mobile-api .
docker run -p 5000:5000 \
  -e SPREADSHEET_ID=... -e GOOGLE_CREDENTIALS_JSON="$(cat google-credentials.json)" \
  joy-mobile-api
```

Coolify settings and the full environment reference are in
[docs/pricing-api.md](docs/pricing-api.md#deploying-with-docker-coolify).

**Before it faces the internet** it needs an API key or an IP allowlist — there
is no authentication on the endpoint yet.
