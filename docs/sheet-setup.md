> **Note.** This page describes the sheet as Phase 1 created it: euros, with a
> base price and condition multipliers. The sheet now stores shekels, one price
> per condition row, with the multiplier fixed at 1.0 -- see
> [scraper.md](scraper.md#the-ils-migration). The structure, tabs and
> maintenance advice below still apply.

# Google Sheet Setup

The sheet is the single source of truth for trade-in pricing. Everything else —
the sync job and the chatbot — reads from it.

## 1. Create the sheet — the quick way

`device-trade-in-pricing.xlsx` in the project root is the finished sheet: both
tabs, all 60 rows, live formulas, formatting and dropdowns already in place.

1. Go to <https://drive.google.com> and drag `device-trade-in-pricing.xlsx` in.
2. Double-click the uploaded file → **Open with → Google Sheets**.
3. **File → Save as Google Sheets**. That converts the upload into a real
   Google Sheet (the `.xlsx` original stays in Drive and can be deleted).
4. Rename the file to **Device Trade-In Pricing**.

You now have `Devices`, `Conditions` and `Guide` tabs, and the formulas in
column I recalculate live. The rest of this document is reference — what each
column means (§3), how to add a device (§6), and who to share it with (§8).

Tab names matter: the API script looks for a tab called exactly `Devices`.

## 2. Create the sheet — from CSV instead

Only needed if you would rather not upload a binary file, or you are rebuilding
one tab.

1. Go to <https://sheets.new> and name the file **Device Trade-In Pricing**.
2. Rename the default tab to **Devices**, and add a second tab named
   **Conditions**.
3. For each tab, **File → Import → Upload**, pick the CSV, and choose:

| Setting | Value |
| --- | --- |
| Import location | **Replace current sheet** |
| Separator type | Comma |
| Convert text to numbers, dates and formulas | **Yes** ← required |

| CSV | Import into tab |
| --- | --- |
| `data/devices.csv` | Devices |
| `data/conditions.csv` | Conditions |

The "convert to formulas" option is what turns the `final_price_ils` column into
live formulas instead of literal text. Going this route, the formatting and
dropdowns from §7 are not set up for you — add them by hand if you want them.

## 3. Devices tab — column reference

One row per **device + condition**. A device sold in two storage sizes is two
device IDs; each device ID has four rows, one per condition. The sample data is
15 devices × 4 conditions = 60 rows.

| Col | Header | Example | Notes |
| --- | --- | --- | --- |
| A | `device_id` | `APL-IP15P-128` | Identifies manufacturer + model + storage. Repeats across that device's four condition rows. |
| B | `manufacturer` | `Apple` | Apple, Samsung, Google, OnePlus … |
| C | `model_name` | `iPhone 15 Pro` | As a customer would say it. |
| D | `storage` | `128GB` | Always the number followed by `GB`. |
| E | `release_year` | `2023` | Used for sorting and reporting. |
| F | `base_price_ils` | `1607` | The shekel price for **this row's condition**, written by the sync from KSP. |
| G | `condition` | `Intact` | One of the four values on the Conditions tab. |
| H | `price_multiplier` | `1.0` | Always 1.0: column F already holds that condition's own price. |
| I | `final_price_ils` | `=ROUND(F3*H3,0)` | **Formula -- never type a number here.** Equals F, since H is 1.0. |
| J | `active` | `TRUE` | Set to `FALSE` to hide a device from the bot without deleting its history. |
| K | `last_updated` | `2026-09-17` | Date the base price last changed. The sync job will maintain this. |

The row key is `device_id` + `condition`. That pair must be unique.

### Device ID convention

`<3-letter manufacturer>-<model code>-<storage>` — e.g. `SAM-S23-256`,
`GOO-P8P-128`, `ONE-NORD3-128`. Any scheme works as long as IDs are unique and
stable; the sync job in Phase 2 will match on this ID to decide whether a device
is new or just repriced.

## 4. Conditions tab

| condition | price_multiplier | description |
| --- | --- | --- |
| Like New | 1.00 | No visible wear, screen and body flawless, fully functional. |
| Intact | 0.85 | Light scratches or scuffs, no cracks, fully functional. |
| Cracked | 0.60 | Cracked screen or back glass, device still powers on and works. |
| Faulty | 0.20 | Does not power on, or a major fault (battery, board, water damage). |

This tab is the reference the bot reads its condition wording from. It does not
drive the Devices tab automatically — the multiplier is copied into column H so
that a historical price stays reproducible even if you retune the multipliers
later.

## 5. How the price is calculated

```
final_price_ils = ROUND(base_price_ils * price_multiplier, 0)
```

Since the ILS migration each row stores its own condition price and the
multiplier is always 1.0, so column I simply echoes column F. The formula stays
so the sheet keeps working if a source is added that quotes one price per device
rather than four.

Worked example — iPhone 15 Pro 128GB, base price €520:

| Condition | Multiplier | Quote |
| --- | --- | --- |
| Like New | 1.00 | €520 |
| Intact | 0.85 | €442 |
| Cracked | 0.60 | €312 |
| Faulty | 0.20 | €104 |

Only column F is ever edited. Change the base price and all four quotes for that
device move together.

### Changing a multiplier across the board

To reprice every device in one condition (say, drop Cracked from 0.60 to 0.55):

1. Update the value on the **Conditions** tab so the documentation stays true.
2. On the **Devices** tab, use **Edit → Find and replace**, tick *Search using
   regular expressions* off, set range to column H, and replace `0.60` with
   `0.55`.

Column I recalculates on its own.

## 6. Adding a new device

Add four rows — one per condition — at the bottom of the Devices tab:

| device_id | manufacturer | model_name | storage | release_year | base_price_ils | condition | price_multiplier | final_price_ils | active | last_updated |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `APL-IP16-128` | Apple | iPhone 16 | 128GB | 2024 | 610 | Like New | 1.00 | `=ROUND(F62*H62,0)` | TRUE | 2026-09-17 |
| `APL-IP16-128` | Apple | iPhone 16 | 128GB | 2024 | 610 | Intact | 0.85 | `=ROUND(F63*H63,0)` | TRUE | 2026-09-17 |
| `APL-IP16-128` | Apple | iPhone 16 | 128GB | 2024 | 610 | Cracked | 0.60 | `=ROUND(F64*H64,0)` | TRUE | 2026-09-17 |
| `APL-IP16-128` | Apple | iPhone 16 | 128GB | 2024 | 610 | Faulty | 0.20 | `=ROUND(F65*H65,0)` | TRUE | 2026-09-17 |

The quickest way: select the four rows of a similar device, copy, paste at the
bottom, then edit columns A–F. Pasting keeps the formula in column I and Sheets
rewrites the row numbers for you.

## 7. Guardrails worth adding

Both take a minute and prevent the two mistakes that actually happen. If you
uploaded the `.xlsx`, the condition dropdown is already there and the base-price
column is already blue — only the formula-column protection is left to add, since
Excel-style protection does not convert reliably into Google Sheets.

**Lock the formula column.** Select column I → right-click → *Protect range* →
*Set permissions* → "Show a warning when editing". Anyone who tries to type a
price over the formula gets a prompt.

**Restrict the condition column.** Select column G → **Data → Data validation**
→ *Dropdown (from a range)* → `Conditions!A2:A4` → *Reject input*. Typos like
"cracked screen" stop being possible.

## 8. Sharing and access

| Who | Access | How |
| --- | --- | --- |
| Whoever maintains pricing | Editor | Share → add their Google account as Editor |
| Everyone else on the team | Viewer | Share → add as Viewer |
| The chatbot | *none* | It reads through the Apps Script web app, not through sharing |

Leave general access on **Restricted**. The script in `apps-script/Code.gs` runs
as the sheet owner, so the sheet itself never needs to be public — see
[api-setup.md](api-setup.md).

## 9. Regenerating the sample data

The CSVs are generated, not hand-written. To change the sample device list, edit
the `DEVICES` list in `tools/generate_sample_data.py` and run:

```bash
python tools/generate_sample_data.py   # rewrites both CSVs
python tools/build_workbook.py         # rebuilds the .xlsx from the same data
```

This is only for seeding a fresh sheet. Once the sheet is live, the sheet is the
source of truth — Phase 2 writes into it rather than re-importing CSVs.
