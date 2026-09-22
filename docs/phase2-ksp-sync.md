# Phase 2 — Decisions and open questions

The scraper is built. How to install, run, schedule and repair it is in
[scraper.md](scraper.md). This page records the decisions behind it and what is
still genuinely unresolved.

## Settled

**Python, not Node.** The work is HTML and text parsing with Hebrew in it, plus
a Google Sheets write. `requests` + `beautifulsoup4` + `gspread` is the shortest
path, and `gspread` handles service-account auth without ceremony.

**An external script, not Apps Script.** Apps Script would have kept everything
in one project, but it is a poor place to debug parsing, has no real test
runner, and its URL-fetch quota is easy to hit while iterating on selectors.

**Upsert, not clear-and-reload.** Explained in
[scraper.md](scraper.md#on-clear-and-reload). Clearing first turns a bad parse
into data loss.

**Match on manufacturer + model + storage, not on `device_id`.** The Phase 1
IDs are hand-written abbreviations (`APL-IP15P-128`) that no generator would
reproduce. Matching on the device itself keeps them intact.

**Prices are trade-in values already.** Both sources are trade-in pages, not
retail listings, so the earlier open question about deriving trade-in value from
retail price does not arise. What KSP quotes goes straight into
`base_price_ils`.

**No currency conversion.** The sheet stores shekels, the currency KSP quotes,
so there is no exchange rate in the pipeline at all. It briefly stored euros;
[scraper.md](scraper.md#the-ils-migration) explains why that was a mistake and
what the migration changed.

**KSP has a clean public API, so nothing is scraped.** The trade-in calculator
calls `GET /snif/TradeIn/INNER_new/api/public/devices` — one request, no
parameters, no authentication, the whole catalogue as JSON. There is no HTML
parsing and no CSS selector anywhere in the KSP path, which removes the entire
class of breakage that a scraper would have carried.
[ksp-api.md](ksp-api.md) documents it.

**KSP publishes real per-condition prices**, as grades A/B/C/D whose own
descriptions map almost word for word onto the Phase 1 conditions. The earlier
open question about condition tiers is answered: they exist, and the sync writes
them, because KSP's real condition curve varies far too much between devices for
a fixed multiplier to approximate.

## Open

**Where the scheduled job runs.** ksp.co.il refuses requests by country. The
code is finished; it needs to run from a network KSP accepts. This is the only
thing between the current state and a live daily sync.

**Whether to keep the 15 Phase 1 sample devices.** They carry invented prices
and mostly do not match KSP's naming, so after the first real sync they are
clutter. Clearing them by hand before the first run is the tidiest option, but
it is a judgement call about data you own.

**Whether to include tablets.** KSP lists 167 tablets alongside 322 phones.
Excluded by default, one config line to include.

**Whether PelePhone is still wanted.** KSP's API covers 5 manufacturers and 489
device variants with real condition pricing, which is more and better data than
scraping a second site would give. The PelePhone adapter still exists and still
has unverified selectors; it may simply not be worth finishing.

**Asking KSP for supported access.** The endpoint is internal and undocumented,
so it can change without notice. The data behind it is clean and well
structured, which suggests they could expose it properly if asked — and that
would also be the cleanest way to resolve the country block.

**Terms of use and `robots.txt`.** Neither could be read — both sites return 403
for `robots.txt` from here. Worth checking from a network that can reach the
site. The load involved is one request per day for the whole catalogue, which is
about as light as an integration gets, but the terms are still worth reading.

**Model naming.** Handled and tested against the real catalogue: the Galaxy
prefix, 4G/5G variants kept apart, the three iPhone SE generations kept apart,
duplicate listings merged at the better price. New quirks may still appear as
KSP adds models; `-v` lists every entry that was skipped and why.
