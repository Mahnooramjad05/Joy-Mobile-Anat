# The Pricing API (Phase 3)

A small Flask service that answers one question: what is this device worth, in
shekels, in each of four conditions. It reads the Google Sheet the sync
maintains and returns those numbers unchanged.

```
Heyy bot ──POST──▶ /api/device-price ──▶ device cache ──▶ Google Sheet (Devices tab)
```

Shekels end to end: KSP quotes shekels, the sheet stores shekels, the API
returns shekels. There is no currency conversion anywhere. The device index is
cached in memory, so a warm request touches no network and answers in
**well under a millisecond**.

---

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env          # already points at the right sheet

python -m api.app             # development, port 5000
gunicorn --bind 0.0.0.0:5000 --workers 2 wsgi:application   # production
```

`wsgi.py` warms the device cache at import, so the first customer request is
not the slow one.

---

## POST /api/device-price

### Request

```json
{
  "manufacturer": "Apple",
  "model": "iPhone 15 Pro Max",
  "storage_gb": 256,
  "condition": "מצב מעולה"
}
```

| Field | Required | Notes |
| --- | --- | --- |
| `manufacturer` | yes | Apple, Samsung, Google, OnePlus, Oppo |
| `model` | yes | As the sheet spells it: `iPhone 15 Pro Max`, `Galaxy S23` |
| `storage_gb` | yes | `256`, `"256"`, `"256GB"`, `"1TB"` and `1024` all work |
| `condition` | **no** | Hebrew or English. Validated, but does not filter the response |

Matching ignores case, spacing and punctuation — `"  apple "` and
`"IPHONE 15 PRO MAX"` resolve fine. Beyond that the match is exact: there is no
similarity matching, so a model name that is not in the sheet is a 404 rather
than a nearby guess.

**`condition` is optional and does not narrow the answer.** The response always
carries all four prices, which is the more useful reply for a bot: show the
ladder and let the customer place their own device. When a condition is sent it
is validated, and echoed back as `selected_condition`.

### Response — 200

```json
{
  "device": "Apple iPhone 15 Pro Max",
  "manufacturer": "Apple",
  "model": "iPhone 15 Pro Max",
  "storage": "256GB",
  "storage_gb": 256,
  "release_year": "2023",
  "selected_condition": { "hebrew": "מצב מעולה", "english": "Like New" },
  "currency": "ILS",
  "conditions": [
    { "hebrew": "מצב מעולה",  "english": "Like New", "price_ils": 1607, "currency": "ILS" },
    { "hebrew": "מצב טוב",    "english": "Intact",   "price_ils": 1501, "currency": "ILS" },
    { "hebrew": "מצב בעייתי", "english": "Cracked",  "price_ils": 756,  "currency": "ILS" },
    { "hebrew": "לא תקין",    "english": "Faulty",   "price_ils": 756,  "currency": "ILS" }
  ]
}
```

Conditions always come back best-first, and `price_ils` is always a whole
number. `selected_condition` and `release_year` are present only when they
apply — `release_year` is only populated for Apple devices, because KSP only
puts the year in Apple model names.

### Errors

| Status | Body | When |
| --- | --- | --- |
| 400 | `{"error": "Missing required field(s)", "missing_fields": [...], "required_fields": [...]}` | A required field is absent or blank |
| 400 | `{"error": "Invalid storage_gb", "detail": "..."}` | Storage could not be read as a size |
| 400 | `{"error": "Invalid condition", "valid_conditions": [...], "valid_conditions_english": [...]}` | Condition is none of the four |
| 404 | `{"error": "Device not found", "status": 404}` | No such manufacturer + model + storage |
| 405 | `{"error": "Method not allowed", ...}` | Anything other than POST |
| 500 | `{"error": "Unable to fetch pricing", "status": 500}` | Sheet unreadable and nothing cached |

The prices are exactly what KSP quotes. Nothing is converted or rounded between
KSP's number and the response.

Every error carries `status` in the body as well as in the HTTP status line, so
a client that only reads the body still knows what happened. Stack traces are
never returned.

## Other endpoints

**`GET /api/conditions`** — the four conditions in both languages. Useful for
building the bot's reply buttons from the API rather than hard-coding them.

**`GET /health`** — liveness plus cache state. Point uptime monitoring here.

```json
{
  "status": "ok",
  "currency": "ILS",
  "devices": { "loaded": true, "devices": 321, "rows": 1284,
               "age_seconds": 7, "expires_in_seconds": 1792 }
}
```

Alert if `devices.loaded` is ever false, or if `age_seconds` climbs well past the
30-minute TTL — that means refreshes are failing and a stale cache is being served.

---

## Conditions

| English (in the sheet) | Hebrew (to customers) |
| --- | --- |
| Like New | מצב מעולה |
| Intact | מצב טוב |
| Cracked | מצב בעייתי |
| Faulty | לא תקין |

Input is accepted in either language, plus common variants: `like-new`,
`good`, `broken`, and the wording KSP itself shows on its trade-in page
(`תקול`, `כחדש לחלוטין`). Invisible right-to-left marks, which Hebrew pasted
from a browser usually carries, are stripped before matching.

The Hebrew above is the customer-facing wording chosen for this project. KSP's
own page words the same four grades differently (`כחדש לחלוטין`,
`ללא שבר/סדק`, `שבר/סדק`, `תקול`) — worth knowing if you ever want the bot to
read back exactly what KSP displays. Both spellings are accepted as input.

## Currency

There is none to manage. KSP quotes shekels, the sheet stores shekels, the API
returns them. `price_ils` is exactly the number KSP quoted.

It was not always so. The sheet originally stored euros and the API converted
back at the ECB daily rate. That cost accuracy — up to 3 ₪ per price — and,
worse, made the sync noisy: a 0.03% move in the exchange rate rewrote 76 rows
whose shekel prices had not changed at all, so a genuine price change was
impossible to spot in the log. Storing the source currency removed both
problems. See [scraper.md](scraper.md#the-ils-migration).

## Caching

| Cache | TTL | Holds |
| --- | --- | --- |
| Device index | 30 minutes | All 321 devices, indexed by manufacturer + model + storage |

The whole Devices tab is read in one call and indexed in memory, rather than
querying per request: Google rate-limits per-call reads, and a bot asks about
one device at a time. One read every 30 minutes serves any amount of traffic.

The cache degrades rather than fails. If a refresh cannot reach Google the stale
index is served and a warning is logged — pricing 30 minutes out of date is far
better than an outage.

A device the sync has deactivated (`active` = `FALSE`) drops out of the index at
the next refresh and stops being quoted, without anything being deleted.

---

## Connecting Heyy

| Bot step | Call | Use |
| --- | --- | --- |
| "What condition?" buttons | `GET /api/conditions` | Build the four buttons from `hebrew` |
| Quote | `POST /api/device-price` | Read `conditions[].price_ils` |

Send `Content-Type: application/json` and UTF-8. Branch on the HTTP status:

- **200** — quote. Show all four prices, or pick the one matching what the
  customer said.
- **404** — "we do not take that model" rather than an error message.
- **400** with `valid_conditions` — re-ask, using that list for the buttons.
- **500** — apologise and offer a callback; do not retry in a tight loop.

Manufacturer and model still have to match the sheet. The bot should build its
manufacturer and model menus from the sheet rather than letting customers free-type,
or add a lookup endpoint for that — see *Not built* below.

### Testing Hebrew from a terminal

On Windows, an inline `curl --data '{"condition":"מצב מעולה"}'` gets mangled by
the shell before curl sees it, and the API correctly rejects the corrupt bytes
as an invalid condition. Put the body in a file instead:

```bash
curl -X POST http://127.0.0.1:5000/api/device-price \
     -H "Content-Type: application/json" --data-binary @body.json
```

Python `requests`, Postman and Heyy all send proper UTF-8 and are unaffected.

---

## Deploying to Hostinger

Hostinger's Python hosting runs WSGI apps through Passenger, and VPS plans run
whatever you like. Either way `wsgi.py` is the entry point.

```bash
# On the server
git clone <repo> && cd <repo>
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Upload google-credentials.json separately -- it is gitignored, deliberately.
# Then:
cp .env.example .env        # edit if the sheet id differs
gunicorn --bind 127.0.0.1:5000 --workers 2 --timeout 30 wsgi:application
```

Put nginx in front for TLS, and keep gunicorn bound to localhost. On shared
hosting, point Passenger's startup file at `wsgi.py` and its application object
at `application`.

**Checklist before it takes traffic**

- [ ] `google-credentials.json` on the server, readable only by the app user
      (`chmod 600`), and **not** in version control
- [ ] The sheet shared with the service account as at least Viewer
- [ ] `GET /health` returns `devices.loaded: true`
- [ ] Outbound HTTPS allowed to `sheets.googleapis.com`
- [ ] Process supervised (systemd or Passenger) so it restarts on failure
- [ ] The API reachable only by the Heyy bot, or behind an API key — see below

### Security

**There is no authentication on this endpoint.** That was not in the brief, and
the data is trade-in prices rather than anything personal, but an open endpoint
is an open endpoint. Before it is reachable from the internet, put one of these
in front: an API key header checked in `before_request`, an IP allowlist for
Heyy's egress addresses, or nginx basic auth. Add rate limiting at the same time
— the caches make the service cheap to run but not free to abuse.

---

## Tests

```bash
python -m pytest tests -q                 # API tests
python -m pytest tests scraper/tests -q   # 194, the whole project
```

They run fully offline: the device index is seeded directly, so no test touches
Google or the network. Covered: device lookup and 404s, both languages in and
out, condition validation, prices passing through untouched, the device cache
including expiry and degradation, request validation, and a performance
assertion that a cached request averages under 100 ms.

## Not built

Worth knowing, in case the bot needs them:

- **No manufacturer / model / storage listing endpoints.** The bot cannot
  currently populate its menus from this API — it can only price a device it
  already names exactly. If Heyy needs cascading dropdowns, that is three small
  endpoints reading the same cached index.
- **No authentication or rate limiting**, as above.
- **No fuzzy matching**, by request. A customer typing "iphone 15 promax" gets a
  404. Whether the bot should handle that, or the API should, is a decision
  worth making before launch.
