> **Phase 3 note.** The project now has a Python pricing service —
> [pricing-api.md](pricing-api.md) — which returns shekels and Hebrew condition
> names and is the intended endpoint for the Heyy bot. This Apps Script route is
> the lighter alternative: no server to run or pay for, but it answers in euros
> with English condition names only. Keep it if you would rather not host
> anything; otherwise use the Phase 3 service.

# Making the Sheet Queryable — Apps Script Web App

Google Sheets has no query API a chatbot can call directly. The thinnest thing
that works is a Google Apps Script web app bound to the sheet: it is free, needs
no server, no hosting account and no OAuth dance on the chatbot side.

## Deploy

1. Open the spreadsheet → **Extensions → Apps Script**.
2. Delete the placeholder `myFunction`, paste the whole of
   [`apps-script/Code.gs`](../apps-script/Code.gs), and save.
3. Set the shared secret: **Project Settings** (gear icon) → *Script Properties*
   → **Add script property**:
   - Property: `API_KEY`
   - Value: a long random string (e.g. run `openssl rand -hex 24`)
4. **Deploy → New deployment → Web app**:
   - Description: `Trade-in API`
   - Execute as: **Me**
   - Who has access: **Anyone**
5. Authorise when prompted. Google shows an "unverified app" warning because the
   script is your own and unpublished — *Advanced → Go to (project) → Allow*.
6. Copy the web app URL. It looks like
   `https://script.google.com/macros/s/AKfy.../exec`.

"Who has access: Anyone" means anyone who has the URL can send a request; the
`API_KEY` check is what actually protects the data. Treat the URL and key
together as a credential.

**After any code change you must Deploy → Manage deployments → edit → New
version.** Saving alone does not update the live URL.

## Endpoints

All requests are `GET` and every one takes `key=<API_KEY>`.

| Action | Parameters | Returns |
| --- | --- | --- |
| `manufacturers` | – | Every manufacturer with active stock |
| `models` | `manufacturer` | Models for that manufacturer |
| `storage` | `manufacturer`, `model` | Storage sizes for that model |
| `conditions` | – | The four condition labels |
| `quote` | `manufacturer`, `model`, `storage`, optional `condition` | A price |

Matching ignores case, spaces and punctuation, so `iphone15pro`, `iPhone 15 Pro`
and `IPHONE 15 PRO` all resolve to the same device. That matters because the
customer is typing free text.

### Example — full quote

```
GET .../exec?key=KEY&action=quote&manufacturer=Apple&model=iPhone%2015%20Pro&storage=128GB&condition=Cracked
```

```json
{
  "ok": true,
  "data": {
    "device_id": "APL-IP15P-128",
    "manufacturer": "Apple",
    "model_name": "iPhone 15 Pro",
    "storage": "128GB",
    "release_year": 2023,
    "condition": "Cracked",
    "base_price": 520,
    "price_multiplier": 0.6,
    "final_price": 312,
    "currency": "EUR",
    "last_updated": "2026-09-17"
  }
}
```

### Example — every condition at once

Drop `condition` and the bot gets the whole ladder in one call, which is usually
the better conversation: show all four prices and let the customer pick.

```json
{
  "ok": true,
  "data": {
    "device_id": "GOO-P8P-128",
    "manufacturer": "Google",
    "model_name": "Pixel 8 Pro",
    "storage": "128GB",
    "currency": "EUR",
    "prices": [
      { "condition": "Like New", "price_multiplier": 1,    "final_price": 300 },
      { "condition": "Intact",   "price_multiplier": 0.85, "final_price": 255 },
      { "condition": "Cracked",  "price_multiplier": 0.6,  "final_price": 180 },
      { "condition": "Faulty",   "price_multiplier": 0.2,  "final_price": 60  }
    ]
  }
}
```

### Errors

Always HTTP 200 with `ok: false` in the body — Apps Script cannot set status
codes cleanly, so the bot should branch on `ok`, not on the status.

| `error.code` | Meaning |
| --- | --- |
| `unauthorized` | Missing or wrong `key` |
| `missing_parameter` | `manufacturer`, `model` or `storage` not supplied |
| `device_not_found` | No match; `suggestions` lists that manufacturer's models |
| `condition_not_found` | Unknown condition; `suggestions` lists the valid four |
| `unknown_action` | Bad `action` value |
| `server_error` | Anything else, with the message attached |

The `suggestions` array exists so the bot can recover in-conversation — "I don't
have that one, but I do have: …" — instead of dead-ending.

## Test it before wiring up the bot

```bash
curl "https://script.google.com/macros/s/YOUR_ID/exec?key=YOUR_KEY&action=manufacturers"
```

Expected: `{"ok":true,"data":["Apple","Google","OnePlus","Samsung"]}`

## Connecting Heyy

The conversation flow maps onto the endpoints one step at a time:

| Bot asks | Call | Use the response to |
| --- | --- | --- |
| "Which brand?" | `action=manufacturers` | Build the reply buttons |
| "Which model?" | `action=models&manufacturer=…` | Build the reply buttons |
| "How much storage?" | `action=storage&manufacturer=…&model=…` | Build the reply buttons |
| "What condition?" | `action=conditions` | Build the reply buttons |
| — | `action=quote&…` | Read `data.final_price` into the quote message |

Driving the buttons from the API rather than hard-coding them in Heyy means a
device added to the sheet shows up in the bot with no bot changes.

In Heyy's HTTP/webhook step, put the `key` in the URL query string, set the
method to GET, and map `data.final_price` into the reply template.

## Caching

The script caches the sheet read for 5 minutes, so a burst of chatbot turns
costs one sheet read rather than dozens. A price edited by hand therefore takes
up to 5 minutes to appear. To flush immediately, run the `clearCache` function
from the Apps Script editor. The Phase 2 sync job should call it after each run.

## Limits

Apps Script web apps allow roughly 20,000 URL-fetch-free executions per day on a
consumer Google account, which is far above what chatbot traffic needs. If that
ever becomes a ceiling, the same sheet can be served by a small Cloud Function
without changing the request or response shapes.
