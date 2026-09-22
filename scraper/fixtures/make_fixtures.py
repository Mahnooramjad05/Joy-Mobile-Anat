"""Generate the synthetic test fixtures.

These stand in for the real trade-in pages, which could not be reached from the
network this was built on. See README.md in this directory.

Run:  python scraper/fixtures/make_fixtures.py
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# Deliberately messy, the way real listings are: Hebrew brand names, mixed
# storage units, prices as both numbers and strings, a couple of unusable rows.
DEVICES = [
    {"brand": "אפל", "name": "iPhone 15 Pro", "capacity": "128GB", "price": 1810},
    {"brand": "אפל", "name": "iPhone 15 Pro", "capacity": "256 GB", "price": 2054},
    {"brand": "אפל", "name": "iPhone 14 Pro", "capacity": "128GB", "price": "1,392"},
    {"brand": "Apple", "name": "iPhone 13", "capacity": "128GB", "price": 835},
    {"brand": "סמסונג", "name": "Galaxy S24 Ultra", "capacity": "256GB", "price": 1671},
    {"brand": "סמסונג", "name": "Galaxy S23", "capacity": "128GB", "price": 870},
    {"brand": "Samsung", "name": "Galaxy Z Flip5", "capacity": "512GB", "price": 1010},
    {"brand": "גוגל", "name": "Pixel 8 Pro", "capacity": "128GB", "price": 1044},
    {"brand": "Google", "name": "Pixel 7a", "capacity": "128GB", "price": 452},
    {"brand": "OnePlus", "name": "OnePlus 12", "capacity": "1TB", "price": 1310},
    {"brand": "שיאומי", "name": "Redmi Note 13", "capacity": "256GB", "price": 400},
    # Unusable on purpose: no price, and an unknown brand. Both must be skipped
    # without taking the run down.
    {"brand": "אפל", "name": "iPhone 12 mini", "capacity": "64GB"},
    {"brand": "Frobozz", "name": "Zork Phone", "capacity": "128GB", "price": 99},
]


def embedded_json():
    payload = {"props": {"pageProps": {"devices": DEVICES}}}
    return f"""<!doctype html>
<html lang="he" dir="rtl">
<head><meta charset="utf-8"><title>טרייד אין</title></head>
<body>
  <h1>מחשבון טרייד אין</h1>
  <div id="root"><!-- rendered in the browser --></div>
  <script id="__NEXT_DATA__" type="application/json">{json.dumps(payload, ensure_ascii=False)}</script>
</body>
</html>
"""


def css_cards():
    cards = "\n".join(
        f"""      <div class="device-card">
        <span class="brand">{d['brand']}</span>
        <h3 class="model">{d['name']}</h3>
        <span class="storage">{d['capacity']}</span>
        <span class="price">₪{d['price']}</span>
      </div>"""
        for d in DEVICES if "price" in d
    )
    return f"""<!doctype html>
<html lang="he" dir="rtl">
<head><meta charset="utf-8"><title>טרייד אין</title></head>
<body>
  <main class="device-list">
{cards}
  </main>
</body>
</html>
"""


def js_only():
    return """<!doctype html>
<html lang="he" dir="rtl">
<head><meta charset="utf-8"><title>טרייד אין</title></head>
<body>
  <div id="app"></div>
  <script src="/static/bundle.js"></script>
</body>
</html>
"""


if __name__ == "__main__":
    for name, builder in [("embedded_json", embedded_json),
                          ("css_cards", css_cards),
                          ("js_only", js_only)]:
        path = os.path.join(HERE, f"{name}.html")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(builder())
        print(f"wrote {path}")
