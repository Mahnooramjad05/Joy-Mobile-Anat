"""Turn scraped text into the shapes the Google Sheet expects.

Israeli retail sites write device names in a mix of Hebrew and English, with
inconsistent spacing and storage units, so everything a source produces passes
through here before it reaches the sheet.
"""

from __future__ import annotations

import re
import unicodedata

# Hebrew and English spellings seen on Israeli retail sites -> sheet spelling.
MANUFACTURER_ALIASES = {
    "apple": "Apple", "אפל": "Apple", "iphone": "Apple", "אייפון": "Apple",
    "samsung": "Samsung", "סמסונג": "Samsung", "galaxy": "Samsung", "גלקסי": "Samsung",
    "google": "Google", "גוגל": "Google", "pixel": "Google", "פיקסל": "Google",
    "oneplus": "OnePlus", "one plus": "OnePlus", "ואנפלוס": "OnePlus",
    "xiaomi": "Xiaomi", "שיאומי": "Xiaomi", "redmi": "Xiaomi",
    "motorola": "Motorola", "מוטורולה": "Motorola",
    "huawei": "Huawei", "hauwei": "Huawei", "וואווי": "Huawei",
    "nothing": "Nothing", "honor": "Honor", "oppo": "Oppo", "vivo": "Vivo",
}

# Three-letter prefixes for generated device IDs, matching the Phase 1 convention.
MANUFACTURER_PREFIX = {
    "Apple": "APL", "Samsung": "SAM", "Google": "GOO", "OnePlus": "ONE",
    "Xiaomi": "XIA", "Motorola": "MOT", "Huawei": "HUA", "Nothing": "NOT",
    "Honor": "HON", "Oppo": "OPP", "Vivo": "VIV",
}

# Noise that appears in product titles but is not part of the model name.
_MODEL_NOISE = re.compile(
    r"\b(\d+\s*gb|\d+\s*tb|\d+\s*ג'יגה|\d+\s*ג״ב|5g|4g|lte|dual\s*sim|"
    r"סים|יד\s*שנייה|משומש|חדש|מחודש)\b",
    re.IGNORECASE,
)


class NormalizeError(ValueError):
    """The text could not be understood well enough to trust."""


def manufacturer(text):
    """Map a manufacturer or product title onto a canonical manufacturer name."""
    if not text:
        raise NormalizeError("empty manufacturer")

    cleaned = _strip_marks(text).strip().lower()

    if cleaned in MANUFACTURER_ALIASES:
        return MANUFACTURER_ALIASES[cleaned]

    # Fall back to finding a known brand anywhere in the string, longest first
    # so "one plus" wins over a stray "one".
    for alias in sorted(MANUFACTURER_ALIASES, key=len, reverse=True):
        if re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", cleaned):
            return MANUFACTURER_ALIASES[alias]

    raise NormalizeError(f"unrecognised manufacturer: {text!r}")


def storage(text):
    """Extract a storage size and render it the way the sheet writes it.

    '128 GB' -> '128GB'   '1 TB' -> '1TB'   '512ג״ב' -> '512GB'
    """
    if not text:
        raise NormalizeError("empty storage")

    cleaned = _strip_marks(text).lower().replace(",", "")

    match = re.search(r"(\d+(?:\.\d+)?)\s*(tb|gb|mb|ט[\"״']?ב|ג[\"״']?ב|טרה|ג'יגה|ג׳יגה)", cleaned)
    if not match:
        raise NormalizeError(f"no storage size in {text!r}")

    amount, unit = float(match.group(1)), match.group(2)
    is_terabytes = unit.startswith("t") or unit.startswith("ט") or unit.startswith("טרה")

    if is_terabytes:
        return f"{amount:g}TB"
    if amount >= 1024 and amount % 1024 == 0:
        return f"{amount / 1024:g}TB"
    return f"{amount:g}GB"


def model(text, manufacturer_name=None):
    """Clean a product title down to a model name."""
    if not text:
        raise NormalizeError("empty model")

    cleaned = _strip_marks(text)
    cleaned = _MODEL_NOISE.sub(" ", cleaned)

    # Drop a leading brand name — the sheet keeps it in its own column.
    if manufacturer_name:
        cleaned = re.sub(rf"^\s*{re.escape(manufacturer_name)}\b", "", cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(r"[\u200e\u200f]", "", cleaned)      # bidi marks
    cleaned = re.sub(r"\s*[-–—|,]\s*$", "", cleaned)      # trailing separators
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—|,")

    if not cleaned:
        raise NormalizeError(f"model name empty after cleaning {text!r}")
    return cleaned


def price(text):
    """Pull a number out of a price string. '₪1,250' -> 1250.0"""
    if text is None:
        raise NormalizeError("empty price")
    if isinstance(text, (int, float)):
        return float(text)

    cleaned = _strip_marks(str(text)).replace(",", "")
    match = re.search(r"\d+(?:\.\d+)?", cleaned)
    if not match:
        raise NormalizeError(f"no number in price {text!r}")
    return float(match.group(0))


def match_key(manufacturer_name, model_name, storage_name):
    """The identity of a device, ignoring case, spacing and punctuation.

    Rows are matched on this rather than on device_id, so hand-written IDs from
    Phase 1 survive and the scraper never creates a second row for a device that
    is already in the sheet under a different ID spelling.
    """
    parts = (manufacturer_name, model_name, storage_name)
    return "|".join(re.sub(r"[^a-z0-9]", "", _strip_marks(p or "").lower()) for p in parts)


def make_device_id(manufacturer_name, model_name, storage_name, taken=()):
    """Build a new device_id, only used for devices not already in the sheet."""
    prefix = MANUFACTURER_PREFIX.get(manufacturer_name, _strip_marks(manufacturer_name)[:3].upper())
    slug = re.sub(r"[^A-Z0-9]", "", _strip_marks(model_name).upper())[:12] or "DEVICE"
    # 128GB -> "128", 1TB -> "1TB", matching the Phase 1 ID convention.
    size = _strip_marks(storage_name).upper().replace(" ", "")
    size = size[:-2] if size.endswith("GB") else size
    size = re.sub(r"[^0-9A-Z]", "", size) or "NA"

    candidate = f"{prefix}-{slug}-{size}"
    if candidate not in taken:
        return candidate

    for suffix in range(2, 100):
        numbered = f"{candidate}-{suffix}"
        if numbered not in taken:
            return numbered
    raise NormalizeError(f"could not find a free device_id for {candidate}")


def _strip_marks(text):
    """Normalise unicode and drop Hebrew niqqud and bidi control characters."""
    text = unicodedata.normalize("NFKC", str(text))
    return "".join(ch for ch in text if unicodedata.category(ch) not in ("Mn", "Cf"))
