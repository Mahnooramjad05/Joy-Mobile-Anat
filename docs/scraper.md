# Phase 2 — The Trade-In Scraper

Pulls device prices from a trade-in site, converts them to EUR, and updates the
Phase 1 Google Sheet. The chatbot API is untouched: it keeps reading the same
tab, so nothing in Phase 3 changes.

---

## Status

The KSP side is finished and verified against real data. PelePhone remains a
fallback whose selectors have never been tested.

| Part | State |
| --- | --- |
| KSP catalogue API | **Verified** — real endpoint, 321 phones, prices match exactly ([ksp-api.md](ksp-api.md)) |
| Per-condition prices | **Verified** — KSP publishes all four grades |
| Fetching, retries, backoff, delays | Built and tested |
| Hebrew-aware normalisation | Built and tested |
| Currency | **None** -- shekels end to end since the ILS migration |
| Google Sheet upsert and safety rules | Built and tested |
| Scheduling and error handling | Built and tested |
| Google Sheet populated | **Done** — 321 devices, 1,284 rows, verified against the API |
| Network access to ksp.co.il | **Blocked by country** from this network — see [Scheduling](#scheduling) |
| PelePhone adapter | Unverified placeholders; that site also refuses requests |

The one operational constraint left is network location: ksp.co.il returns HTTP
403 via Cloudflare from outside Israel, naming the rejected country. That
affects *where* the sync runs, not whether it works.

## Install

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows
source .venv/bin/activate         # macOS / Linux
pip install -r requirements.txt
```

## Run it

```bash
# Fetch the catalogue and print it, touching nothing. Start here.
python -m scraper.sync --dry-run

# Fetch and update the Google Sheet.
python -m scraper.sync

# Work from the captured real response, with no network at all.
python -m scraper.sync --from-file scraper/fixtures/ksp_api_devices.json --dry-run

# Save the live response for inspection.
python -m scraper.sync --dry-run --capture

# Also write the results to a file.
python -m scraper.sync --dry-run --output data/scraped.json

# The unverified fallback source.
python -m scraper.sync --source pelephone --dry-run
```

| Flag | Effect |
| --- | --- |
| `--source {ksp,pelephone}` | Which source to use |
| `--dry-run` | Fetch and report; never touches the sheet |
| `--capture` | Save the raw response to `captures/` |
| `--from-file PATH` | Parse a saved response instead of fetching (`--html` also works) |
| `--output PATH` | Also write results to `.json` or `.csv` |
| `--no-deactivate` | Leave devices missing from the source active |
| `-v` | Debug logging, including every skipped entry |

`--dry-run` reads the sheet and prints the full plan — how many devices are new,
what would be repriced, what would be deactivated — and then writes nothing.
Run it before any real sync.

### Two one-off tools

Neither is part of the scheduled sync, because the sync never deletes and never
creates tabs. Both preview by default and need `--yes` to act.

```bash
python tools/init_sheet_tabs.py --yes     # build Devices + Conditions in an empty sheet
python tools/reset_devices_tab.py --yes   # clear the Devices data rows, keep the header
```

`reset_devices_tab.py` refuses to run if it finds rows that are not Phase 1
sample data, unless given `--force`, so it cannot quietly destroy real pricing.

### Exit codes

A scheduler can act on these. **On every non-zero exit the sheet is left
exactly as it was.**

| Code | Meaning |
| --- | --- |
| 0 | Success |
| 1 | Could not fetch — site down, blocked, or network failure |
| 2 | Fetched, but no devices parsed, or too few to trust |
| 3 | Could not read or write the sheet |
| 4 | Bad configuration |

## Sample output

Real output, from the captured KSP response:

```
INFO sync: source: KSP (https://ksp.co.il/snif/TradeIn/INNER_new/api/public/devices)
INFO sources: ksp: read 321 devices from the catalogue API (167 skipped, 1 duplicate listings merged)
INFO sync: parsed 321 devices via api
INFO fx: fetched ILS->EUR rate 0.28900
INFO sync: converted 321 prices at ILS 1 = EUR 0.28900
INFO sync: sample of what was scraped (prices in EUR):
INFO sync:   BRAND     MODEL                  STORAGE  Like New    Intact   Cracked    Faulty
INFO sync:   Apple     iPhone 11              256GB         115       113        47        47
INFO sync:   Apple     iPhone 11 Pro          512GB         125       119        54        54
INFO sync:   Apple     iPhone 11 Pro Max      256GB         153       141        61        61
INFO sync:   ... and 313 more
INFO sync: dry run -- the sheet was not touched
```

The 167 skipped entries are tablets, excluded by default. The merged duplicate
is a device KSP lists twice under two spellings.

`--output data/scraped.json` gives one object per device:

```json
{
  "manufacturer": "Apple",
  "model_name": "iPhone 15 Pro",
  "storage": "128GB",
  "release_year": 2023,
  "price": 364,
  "currency": "EUR",
  "condition_prices": { "Like New": 364, "Intact": 357, "Cracked": 193, "Faulty": 193 },
  "condition_multipliers": { "Like New": 1.0, "Intact": 0.9802, "Cracked": 0.5289, "Faulty": 0.5289 },
  "source": "ksp",
  "source_url": "https://ksp.co.il/snif/TradeIn/INNER_new/api/public/devices"
}
```

See `data/sample-scrape.json` for all 321.

## Google Sheets credentials

The scraper writes through a service account — a robot Google account that owns
no data and can only touch sheets you explicitly share with it.

1. Open <https://console.cloud.google.com/> and create a project.
2. **APIs & Services → Library →** enable **Google Sheets API**.
3. **APIs & Services → Credentials → Create credentials → Service account.**
   Name it something like `tradein-sync`. Skip the optional role steps.
4. Open the new service account → **Keys → Add key → Create new key → JSON**.
   Save the downloaded file as `credentials.json` in the project root.
5. Copy the service account's email address — it looks like
   `tradein-sync@your-project.iam.gserviceaccount.com`.
6. Open the Google Sheet → **Share** → paste that address → give it **Editor** →
   send. Without this step every run fails with a permissions error, and that is
   the single most common setup mistake.
7. Copy the spreadsheet ID out of the sheet's URL:
   `docs.google.com/spreadsheets/d/`**`THIS_LONG_STRING`**`/edit`

Then tell the scraper about both:

```bash
# Windows PowerShell
$env:TRADEIN_SPREADSHEET_ID = "your_spreadsheet_id"
$env:TRADEIN_CREDENTIALS = "credentials.json"

# macOS / Linux
export TRADEIN_SPREADSHEET_ID="your_spreadsheet_id"
export TRADEIN_CREDENTIALS="credentials.json"
```

Or set `SPREADSHEET_ID` directly in `scraper/config.py`.

**`credentials.json` is a password.** It is already in `.gitignore`; keep it
out of version control and out of chat messages.

---

## What the scraper writes

Three rules, enforced in code and covered by tests:

**1. Nothing is ever deleted.** A device that disappears from the source has
its `active` column set to `FALSE`. Its row, its price history and its
`device_id` all survive, and it comes back automatically if it reappears.

**2. The formula column is never written.** On an existing row the sync touches
F (`base_price_ils`), H (`price_multiplier`), J (`active`) and K
(`last_updated`). It never writes I (the `=ROUND(F*H,0)` formula) or G (the
condition label), so it cannot replace a formula with a literal or relabel a
condition. Tests assert this.

**3. Devices are matched on manufacturer + model + storage, not on
`device_id`.** Matching ignores case, spacing and punctuation. This is what
keeps hand-written Phase 1 IDs like `APL-IP15P-128` intact instead of the
scraper inventing a parallel `APL-IPHONE15PRO-128` and doubling every device.

A new device gets four rows appended, one per condition from the Conditions
tab, with the `final_price_ils` formula already pointing at its own row.

### On "clear and reload"

The brief asked for the sheet to be cleared and refilled each run. The scraper
does an upsert instead — same end state, without the failure mode. Clearing
first means that a scrape which fetches fine but parses badly (a layout change,
a half-loaded page) wipes real pricing data before anyone notices. It would also
destroy the hand-written `device_id` values and any base price someone had
adjusted by hand, neither of which the source knows about.

The upsert gets you fresh prices every run and cannot lose data. If you do want
a genuine wipe-and-reload, say so and it is a small change — but it should be a
deliberate choice, not the default.

### Two guards against a quiet failure

| Setting | Default | What it prevents |
| --- | --- | --- |
| `MIN_DEVICES_EXPECTED` | 5 | A parser returning two devices instead of two hundred silently repricing your catalogue |
| `MAX_DEACTIVATION_RATIO` | 0.30 | A broken scrape deactivating most of the sheet. Over the limit, prices still update but nothing is deactivated, and the log says so |

### The Sync Log tab

Every run appends a row: timestamp, source, status, devices found, new devices,
prices changed, deactivated, message. Failed runs are logged too. This is the
difference between "pricing looks wrong" and "pricing looks wrong, and the sync
has failed three runs in a row". The tab is created on first use.

---

## Scheduling

Daily is the right cadence: KSP's own FAQ says the price list "can update from
day to day". Run it outside business hours so nobody is editing prices by hand
while it writes.

**Where it runs matters more than when.** ksp.co.il returns HTTP 403 via
Cloudflare from outside Israel, so the scheduled job has to originate somewhere
KSP accepts — an office machine there, a small VPS in Israel, or a runner behind
a VPN endpoint there. Everything below assumes that is sorted; a blocked run
exits 1 and leaves the sheet untouched, so a misplaced scheduler is noisy rather
than dangerous.

### Windows Task Scheduler

Save as `run-sync.bat` in the project root:

```bat
@echo off
cd /d "C:\Users\pak\Desktop\KSP sync Anat"
set TRADEIN_SPREADSHEET_ID=your_spreadsheet_id
call .venv\Scripts\python.exe -m scraper.sync
if errorlevel 1 echo Sync failed with code %errorlevel% >> logs\failures.log
```

Then: **Task Scheduler → Create Task → Triggers → Daily**, and under
**Actions**, run `run-sync.bat` with **Start in** set to the project folder.
Tick *Run whether user is logged on or not*.

### cron (macOS / Linux)

```cron
# 03:15 daily
15 3 * * * cd /path/to/project && .venv/bin/python -m scraper.sync >> logs/cron.log 2>&1
```

### GitHub Actions

Useful because it needs no always-on machine, **but GitHub's hosted runners are
in the US and Europe and will be refused by the country block.** This works only
with a self-hosted runner in Israel, or an egress proxy there.

```yaml
name: Sync trade-in prices
on:
  schedule:
    - cron: "15 3 * * *"
  workflow_dispatch:

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
      - run: echo '${{ secrets.GOOGLE_CREDENTIALS }}' > credentials.json
      - run: python -m scraper.sync
        env:
          TRADEIN_SPREADSHEET_ID: ${{ secrets.SPREADSHEET_ID }}
```

Put the whole contents of `credentials.json` into a repository secret named
`GOOGLE_CREDENTIALS`. A failed run fails the workflow, so GitHub emails you.

---

## The ILS migration

The sheet used to store euros: KSP quotes shekels, the sync converted to euros,
and the API converted back. Two things were wrong with that.

**It lost accuracy.** The round trip landed within 3 shekels of KSP's number,
with 77 of 1,284 prices off by a euro. Small, but a customer comparing the bot's
quote against the KSP website would see a different figure.

**It made the log useless.** On 2026-09-22 a dry run reported 76 rows repriced
against completely unchanged KSP data. The cause was the ILS-to-EUR rate moving
0.28900 to 0.28908 -- a 0.03% twitch that flipped 19 devices' euro price by one
euro. A daily sync would churn dozens of rows on currency noise, bumping
`last_updated` each time, and a real KSP price change would be invisible among
it.

Storing the source currency fixed both. The sheet now holds exactly what KSP
quotes, and there is no exchange rate anywhere in the pipeline.

### What changed

| | Before | After |
| --- | --- | --- |
| Column F | `base_price_eur` -- the Like New price, on all four rows | `base_price_ils` -- **that row's own condition price** |
| Column H | `price_multiplier` -- KSP's real condition ratio | always `1.0` |
| Column I | `final_price_eur` = `ROUND(F*H,0)` | `final_price_ils`, same formula, now echoes F |
| Currency | EUR | ILS |
| `scraper/fx.py` | ILS to EUR via ECB | deleted |
| `api/exchange_rate.py` | EUR to ILS via ECB | deleted |

Each row storing its own price is what removes the rounding: there is no
multiplication between KSP's number and the sheet, so nothing to round.

The multiplier column stays at 1.0 rather than being dropped, because the sheet
formula and the Phase 1 layout reference it, and because a source that quotes
only one price per device still needs it -- the sync applies the Conditions tab
ratio in that case, so column F always means the same thing.

### Running the migration

```bash
python tools/migrate_sheet_to_ils.py          # preview
python tools/migrate_sheet_to_ils.py --yes    # rename headers, reformat as shekels
python -m scraper.sync --dry-run              # see the reprice plan
python -m scraper.sync                        # write the shekel prices
```

The tool only renames two headers and changes two number formats. The sync does
the prices, because every row changes anyway and it already knows how.

A sheet still on the old header gets a clear error pointing at the tool, rather
than a confusing mismatch.

### What it actually did

```
prices repriced     : 1282 rows
multipliers changed : 817 rows
done: 0 new, 1282 repriced, 817 multipliers changed, 0 deactivated (4198 cells updated)
```

Verified afterwards by reading the sheet back and comparing every row against
KSP's own figures: **1,284 exact matches, zero differences.** Two rows needed no
reprice because their euro and shekel values happened to coincide.

The next two syncs reported `0 repriced, 1284 unchanged` -- the churn is gone.

### What the first sync actually did

Run on 2026-09-21 against the "Trade In Database" sheet:

```
sheet now           : 0 data rows (0 devices)
new devices         : 321  -> 1284 rows appended
sheet after         : 1284 data rows (321 devices)
done: 321 new, 0 repriced, 0 reactivated, 0 deactivated, 0 unchanged
```

Verified by reading the sheet back: 1,284 rows, 321 unique device ids, exactly
four rows each, all four conditions present 321 times, no missing prices, all
rows active.

Against the prices KSP's API returned, the sheet's `=ROUND(F*H,0)` formulas give
**1,207 exact matches and 77 rows off by exactly EUR 1**, none off by more —
the rounding cost of keeping a whole-euro base price and a 4-decimal multiplier,
as described above.

A second identical run reported `0 new, 0 repriced, 0 unchanged rows changed`
and left the row count at 1,284. The upsert is idempotent, which is what makes a
daily schedule safe.

**`release_year` is only populated for Apple devices.** KSP puts the year in
Apple model names ("iPhone 15 Pro (2023)") and nowhere else, so the column is
blank for Samsung, Google, OnePlus and Oppo. Nothing reads it; it is there for
sorting and reporting.

## When scraping breaks

Layout changes are when, not if. The failure is designed to be loud: the run
exits non-zero, the sheet is untouched, the HTML is saved to `captures/`, and
the Sync Log records it.

| Symptom | Cause | Fix |
| --- | --- | --- |
| `FETCH FAILED ... 403` | Blocked by country or bot protection | Check the site in a browser from the same network. The block is by country |
| `FETCH FAILED` after 3 attempts | Site down or network trouble | Usually transient; the next run recovers |
| `response was not JSON` | A block page came back instead of data | Check the site in a browser from the same network; the block is by country |
| `the response has no 'catalog' object` | KSP changed the API shape | Compare a fresh `--capture` against `scraper/fixtures/ksp_api_devices.json`; see [ksp-api.md](ksp-api.md) |
| `PARSE SUSPECT: only N devices` | Parser partly broken | Same fix; the guard already stopped it writing |
| `skipped N unreadable rows` | New brand or an odd storage format | Add the brand to `MANUFACTURER_ALIASES` in `normalize.py`; run with `-v` to see the rows |
| `SHEET FAILED ... could not open` | Sheet not shared with the service account | Share it as Editor with the service account email |
| Prices look wrong by a constant factor | Currency conversion | Check the rate in `captures/fx-rate.json` |

Everything you are likely to edit lives in two files: `scraper/config.py` for
the source and its selectors, `scraper/normalize.py` for brand and format
handling.

---

## Tests

```bash
python -m pytest scraper/tests -q
```

194 tests covering normalisation (including Hebrew brand names and mixed storage
units), both extraction paths, retry and backoff behaviour, block detection, and
every rule in [What the scraper writes](#what-the-scraper-writes). They run
offline in a few seconds.

Among them: the real captured KSP response is parsed and asserted against, the
grade-to-condition mapping is pinned, 4G/5G variants are checked to stay
separate, the three iPhone SE generations are checked not to collapse, and the
sheet writer is checked never to touch the formula column.

They run offline in a few seconds, against the real payload rather than a
mock-up, so a change in KSP's API shape shows up as a test failure.

---

## Scraping responsibly

The scraper sends one request per run, waits 2 seconds between any requests,
retries with exponential backoff rather than hammering, and identifies itself
in its User-Agent.

**Put a real contact address in that User-Agent** (`USER_AGENT` in
`config.py`). A site operator who can see who you are and reach you will
usually just talk to you; an anonymous scraper gets blocked.

Two things worth settling before running this at any volume:

- **`robots.txt` could not be read** — both sites return 403 for it too, so its
  rules are unknown. Check it from a network that can reach the site and honour
  whatever it says.
- **Check the terms of use.** Trade-in prices are commercial data and these are
  commercial sites. Asking KSP for a feed is both easier and safer than scraping,
  and is worth trying before investing further here.
