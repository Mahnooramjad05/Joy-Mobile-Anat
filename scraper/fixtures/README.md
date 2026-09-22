# Test fixtures

**These are synthetic pages, not captures of the real sites.**

Neither ksp.co.il nor pelephone.co.il could be reached from the network this
project was built on — both refused every request by country (Cloudflare and
Imperva respectively) — so the real markup has never been seen.

What these fixtures prove is that the pipeline works end to end: extraction,
Hebrew-aware normalisation, currency conversion, and the sheet upsert plan.
What they cannot prove is that the selectors in `config.py` match the real
pages, because those selectors are placeholders.

Replace these with a real capture as soon as one is available:

```bash
python -m scraper.sync --capture --dry-run
cp captures/ksp-<timestamp>.html scraper/fixtures/ksp_real.html
```

Then point `test_sources.py::test_real_capture` at it. That test is skipped
while no real capture exists.

| Fixture | What it exercises |
| --- | --- |
| `embedded_json.html` | The JSON-in-page path — a `__NEXT_DATA__` blob, the way most sites of this kind ship their catalogue |
| `css_cards.html` | The CSS-selector fallback, for a server-rendered page |
| `js_only.html` | A page whose catalogue never appears in the HTML — the parser must fail loudly rather than silently return nothing |
