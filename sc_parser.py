"""
sc_parser.py
Shared OCR parsing for SC Price Scanner.

Supports two real Star Citizen terminal layouts:

1. MARKET TABLE - commodity list with a header row
      COMMODITY | BUY | SELL | STOCK | DEMAND

2. BUY MODAL - one card per commodity, several cards stacked
      SCRAP                SHOP QUANTITY
         VERY LOW INVENTORY   27 SCU
                              <glyph>2,954/UNITS
         AVAILABLE CARGO SIZE (SCU)

Notes on the OCR heuristics (learned from real captures):
  * Game fonts confuse letters with digits: S->5, O->0, l->1, B->8, etc.
  * A currency glyph (looks like a boxed x / aUEC symbol) sits directly left of
    prices and is often read as a leading digit, e.g. "2,954" -> "42.954" or
    "7,315" -> "17,315". We detect this because it turns a 1-digit leading group
    into a 2-digit group on a 5-digit value, and strip it.
"""

import json
import re
from pathlib import Path

import cv2
import numpy as np

try:
    import pytesseract
except Exception:  # pragma: no cover
    pytesseract = None

# ------------------------------------------------------------------
# Vocab
# ------------------------------------------------------------------
HEADER_MARKERS = {
    "commodity": ["commodity", "commodities", "item", "goods", "product"],
    "buy": ["buy", "buying", "purchase"],
    "sell": ["sell", "selling", "revenue"],
    "stock": ["stock", "supply", "inventory", "available"],
    "demand": ["demand", "requested", "wanted"],
}

NOISE_WORDS = {
    "scu", "units", "unit", "auec", "uec", "available", "cargo", "size",
    "shop", "quantity", "total", "buy", "sell", "back", "close", "purchase",
    "inventory", "stock", "demand", "commodity", "commodities", "market",
    "ref", "req", "loading", "time", "est", "click", "select", "confirm",
    "cancel", "items", "item", "price", "cost", "amount", "qty", "high",
    "low", "medium", "very", "empty", "full", "none", "per", "the", "and",
    "buying", "selling", "revenue", "supply", "product", "products", "goods",
    "trade", "trading", "screen", "terminal", "exit", "esc", "refine",
    "refining", "yield", "rate", "order", "volume",
}

GROUP_RE = re.compile(r"\d{1,3}(?:[.,\s]\d{3})+")
PLAIN_RE = re.compile(r"\d+")

# Common Star Citizen commodities (used to clean up noisy OCR names)
KNOWN_COMMODITIES = [
    "AcryliPlex Composite", "Agricium", "Agricium (Ore)", "Agricultural Supplies",
    "Altruciatoxin", "Aluminum", "Aluminum (Ore)", "Amioshi Plague", "Ammonia",
    "Anti-Hydrogen", "Aphorite", "Apoxygenite", "Argon", "Arsenic", "Aslarite",
    "Aslarite (Raw)", "Astatine", "Atacamite", "Atlasium", "Audio-Visual Equipment",
    "Beradom", "Beryl", "Beryl (Raw)", "Bexalite", "Bexalite (Raw)", "Bioplastic",
    "Blue Bilva", "Borase", "Borase (Ore)", "CK13-GID Seed Blend", "Cadmium Allinide",
    "Caranite", "Caranite (Raw)", "Carbon", "Carbon-Silk", "Carinite",
    "Carinite (Pure)", "Cave Kopion Horn", "Chlorine", "Coal", "Cobalt",
    "Cobalt (Raw)", "Compboard", "Construction Material Pebbles",
    "Construction Material Rubble", "Construction Material Salvage",
    "Construction Materials", "Construction Pieces", "Copper", "Copper (Ore)",
    "Corundum", "Corundum (Raw)", "Crude Oil", "CryoPod", "DCSR2", "Decari Pod",
    "Degnous Root", "Detatrine", "Diamond", "Diamond (Raw)", "Diamond Laminate",
    "Diluthermex", "Distilled Spirits", "Dolivine", "Dopple", "Dymantium", "DynaFlex",
    "E'tam", "Elespo", "Feynmaline", "Fireworks", "Fluorine", "Freeze", "Fresh Food",
    "Gasping Weevil Eggs", "Glacosite", "Glow", "Gold", "Gold (Ore)", "Golden Medmon",
    "Hadanite", "Heart of the Woods", "Helium", "Hephaestanite", "Hephaestanite (Raw)",
    "HexaPolyMesh Coating", "Human Food Bars", "Hydrogen", "Hydrogen Fuel",
    "Ice (Raw)", "Inert Materials", "Iodine", "Iron", "Iron (Ore)",
    "Irradiated Kopion Horn", "Jaclium", "Jaclium (Ore)", "Jahlium", "Janalite",
    "Jumping Limes", "Kopion Horn", "Krypton", "Laranite", "Laranite (Raw)",
    "Lastaphrene", "LifeCure Medsticks", "Lindinium", "Lindinium (Ore)",
    "Luminalia Gift", "Lunes", "Lycara", "Magnesium", "Mala", "Marok Gem", "Maze",
    "Medical Supplies", "Mercury", "Methane", "Molina Mold Samples",
    "Molina Mold Treatment", "Molina Ventilation Filters", "Neograph", "Neon",
    "Nitrogen", "Omnapoxy", "Organics", "Osoian Hides", "Ouratite", "Ouratite (Raw)",
    "Partillium", "Party Favors", "Phosphorus", "Pitambu", "Potassium",
    "Pressurized Ice", "Processed Food", "Prota", "Quantainium", "Quantainium (Raw)",
    "Quantum Fuel", "Quartz", "Quartz (Raw)", "Ranta Dung",
    "Recycled Material Composite", "Redfin Energy Modulators", "Revenant Pod",
    "Revenant Tree Pollen", "Riccite", "Riccite (Ore)", "SLAM", "Sadaryx",
    "Sadaryx (Raw)", "Saldynium (Ore)", "Sarilus", "Savrilium", "Savrilium (Ore)",
    "Scrap", "Selenium", "Ship Ammunition", "Ship Ammunition - Size 1",
    "Ship Ammunition - Size 2", "Ship Ammunition - Size 3", "Ship Ammunition - Size 4",
    "Ship Ammunition - Size 5", "Ship Ammunition - Size 6", "Ship Ammunition - Size 7",
    "Ship Decoy Countermeasures", "Ship Noise Countermeasures", "Silicon",
    "Silicon (Raw)", "Silnex", "Souvenirs", "Steel", "Stileron", "Stileron (Raw)",
    "Stims", "Stone Bug Shell", "Sunset Berries", "Taranite", "Taranite (Raw)",
    "Tellurium", "Thermalfoam", "Thrust", "Tin", "Tin (Ore)", "Titanium",
    "Titanium (Ore)", "Torite", "Torite (Ore)", "Tritium", "Tundra Kopion Horn",
    "Tungsten", "Tungsten (Ore)", "Waste", "WiDoW", "Wuotan Seed", "Xa'Pyen", "Xenon",
    "Year of the Dog Envelope", "Year of the Monkey Envelope",
    "Year of the Pig Envelope", "Year of the Rat Envelope",
    "Year of the Rooster Envelope", "Zeta-Prolanide", "Zip"
]

# Names that differ between the game and the SC-Truckers / UEX market list
COMMODITY_ALIASES = {
    "aluminium": "Aluminum",
    "quantanium": "Quantainium",
    "rcm": "Recycled Material Composite",
    "recycledmaterial": "Recycled Material Composite",
    "hydrogenfuel": "Hydrogen Fuel",
    "quantumfuel": "Quantum Fuel",
    "vaste": "Waste",
    "eluorine": "Fluorine",
}

_COMMODITY_INDEX = {}
_COMMODITY_BY_NORM = {}
for _c in KNOWN_COMMODITIES:
    _cn = re.sub(r"[^a-z]", "", _c.lower())
    _COMMODITY_INDEX[_cn] = _c
    _COMMODITY_BY_NORM.setdefault(_cn, _c)
_COMMODITY_NORMS = list(_COMMODITY_BY_NORM.keys())

# Letter -> digit confusions common in game/pixel fonts
CONFUSABLE = str.maketrans({
    "S": "5", "s": "5",
    "O": "0", "o": "0",
    "l": "1", "I": "1", "|": "1",
    "B": "8",
    "Z": "2", "z": "2",
    "G": "6",
    "q": "9",
    "D": "0",
})


# ------------------------------------------------------------------
# Numeric helpers
# ------------------------------------------------------------------
def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _is_noise(text):
    n = _norm(text)
    return (not n) or (n in NOISE_WORDS)


def _digits_only(text):
    """Normalise a token for numeric reading: map confusables, keep digits."""
    t = str(text)
    # Cut a unit suffix after a slash, e.g. "2,954/UNITS" -> "2,954"
    if "/" in t:
        t = t.split("/", 1)[0]
    t = re.sub(r"(?i)\b(scu|units?|auec|uec)\b", "", t)
    # Drop whole words (SHOP, QUANTITY...) before mapping confusables, otherwise
    # "SHOP" -> "50P" injects fake digits.
    t = re.sub(r"[A-Za-z]{2,}", " ", t)
    t = t.translate(CONFUSABLE)
    # Collapse whitespace inside a number: "17, 315" -> "17,315"
    t = re.sub(r"(?<=[\d.,])\s+(?=\d)", "", t)
    return t


_UI_WORDS = (
    "inventory", "quantity", "ventory", "available", "transaction",
    "shop", "stock", "units", "unit", "cargo", "demand", "please",
    "select", "valid", "make", "high", "very", "medium", "low", "max", "wax",
)


def _strip_ui(norm):
    for w in _UI_WORDS:
        norm = norm.replace(w, "")
    return norm


def canonical_commodity(name):
    """Fuzzy-map a noisy OCR name onto the SC-Truckers commodity list, else None."""
    if not name:
        return None
    norm = re.sub(r"[^a-z]", "", str(name).lower())
    if not norm:
        return None
    if norm in COMMODITY_ALIASES:
        return COMMODITY_ALIASES[norm]
    if norm in _COMMODITY_INDEX:
        return _COMMODITY_INDEX[norm]
    # Card titles often fuse a UI label with the name: "WAXCHLORINEINVENTORY".
    stripped = _strip_ui(norm)
    if stripped in _COMMODITY_INDEX:
        return _COMMODITY_INDEX[stripped]
    if stripped in COMMODITY_ALIASES:
        return COMMODITY_ALIASES[stripped]
    # A known name may be embedded in a noisy token, but only small OCR noise
    # may surround it ("CHLORINE12" ok; "tejncorunaluminumrnventory" not).
    best = None
    best_len = 0
    for cn, c in _COMMODITY_BY_NORM.items():
        if len(cn) < 4:
            continue
        idx = stripped.find(cn)
        if idx < 0:
            continue
        pre = stripped[:idx]
        suf = stripped[idx + len(cn):]
        if len(pre) > 5 or len(suf) > 5:
            continue
        if len(cn) > best_len:
            best = c
            best_len = len(cn)
    return best


def _to_int(text):
    """
    Parse a token to int, applying glyph-strip + grouping heuristics.
    """
    if text is None:
        return None
    t = _digits_only(text)

    m = GROUP_RE.search(t)
    if m:
        raw = re.sub(r"\s", "", m.group())
        parts = re.split(r"[.,]", raw)
        digits = "".join(p for p in parts if p.isdigit())
        if not digits:
            return None
        # Glyph strip: glyph turns a 1-digit leading group into 2 digits.
        # e.g. "42.954" -> 2954, "17,315" -> 7315, "51.376" -> 1376
        if len(parts) >= 2 and len(parts[0]) == 2 and len(digits) == 5:
            return int(digits[1:])
        return int(digits)

    m = PLAIN_RE.search(t)
    if m:
        return int(m.group())
    return None


def _has_digit(text):
    return bool(re.search(r"\d", text))


# ------------------------------------------------------------------
# Line building + token merging
# ------------------------------------------------------------------
def group_lines(ocr_data, tol_factor=0.5):
    tokens = []
    n = len(ocr_data.get("text", []))
    for i in range(n):
        text = (ocr_data["text"][i] or "").strip()
        if not text:
            continue
        try:
            conf = float(ocr_data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if conf < 0:
            continue
        left = ocr_data["left"][i]
        top = ocr_data["top"][i]
        width = ocr_data["width"][i]
        height = ocr_data["height"][i]
        tokens.append({
            "text": text,
            "conf": conf,
            "x": left,
            "y": top,
            "w": width,
            "h": height,
            "x_center": left + width / 2.0,
            "y_center": top + height / 2.0,
            "y_bottom": top + height,
        })

    if not tokens:
        return []

    # Cluster by vertical centre. Compare each token to a row's *mean* centre so
    # one tall token cannot swallow the following line.
    tokens.sort(key=lambda t: t["y_center"])
    rows = []
    for tok in tokens:
        placed = False
        for row in rows:
            ref = row["y_center"]
            ref_h = max(1.0, row["h"])
            if abs(tok["y_center"] - ref) <= tol_factor * max(tok["h"], ref_h):
                row["tokens"].append(tok)
                row["y_center"] = sum(t["y_center"] for t in row["tokens"]) / len(row["tokens"])
                row["h"] = max(row["h"], tok["h"])
                placed = True
                break
        if not placed:
            rows.append({"y_center": tok["y_center"], "h": tok["h"], "tokens": [tok]})

    rows.sort(key=lambda r: r["y_center"])
    out = []
    for row in rows:
        rt = sorted(row["tokens"], key=lambda t: t["x"])
        out.append({"segments": _merge_tokens(rt)})
    return out


def _merge_tokens(tokens, gap_factor=0.6):
    """Merge horizontally adjacent tokens (fixes '2' + ',954/UNITS')."""
    if not tokens:
        return []
    segments = []
    cur = dict(tokens[0])
    cur["text"] = tokens[0]["text"]

    for tok in tokens[1:]:
        gap = tok["x"] - (cur["x"] + cur["w"])
        ref_h = max(1.0, min(cur["h"] or 10, tok["h"] or 10))
        if gap <= gap_factor * ref_h:
            # merge (space only if there was a visible gap)
            sep = "" if gap < 0.15 * ref_h else " "
            cur["text"] = cur["text"] + sep + tok["text"]
            cur["w"] = (tok["x"] + tok["w"]) - cur["x"]
            cur["h"] = max(cur["h"], tok["h"])
            cur["x_center"] = cur["x"] + cur["w"] / 2.0
            cur["conf"] = max(cur["conf"], tok["conf"])
        else:
            segments.append(cur)
            cur = dict(tok)
            cur["text"] = tok["text"]
    segments.append(cur)
    return segments


# ------------------------------------------------------------------
# Modal layout (multi-card)
# ------------------------------------------------------------------
def _stock_from_header(joined):
    """Extract stock fused inside the header, e.g. 'SHOP2,082QUANTITYSCU'."""
    m = re.search(r"(?i)shop\s*([\d][\d,\.\s]{0,12}?)\s*quantity", joined)
    if not m:
        return None
    v = _to_int(m.group(1))
    return v if v and 0 < v < 1_000_000 else None


def parse_modal(lines, tab=None, min_x=0):
    results = []
    n = len(lines)

    for i, line in enumerate(lines):
        segs = [s for s in line["segments"] if s["x_center"] >= min_x]
        joined = " ".join(s["text"] for s in segs).lower()
        if "shop" not in joined or "quantity" not in joined:
            continue

        # Commodity name: fuzzy-match any segment on the card line
        name = None
        for s in segs:
            c = canonical_commodity(s["text"])
            if c:
                name = c
                break
        if not name and i > 0:
            prev_segs = [s for s in lines[i - 1]["segments"] if s["x_center"] >= min_x]
            for s in prev_segs:
                c = canonical_commodity(s["text"])
                if c:
                    name = c
                    break
        if not name:
            # Unknown commodity: accept a clean title/uppercase label on the card
            for s in segs:
                t = s["text"].strip()
                if not re.fullmatch(r"[A-Z][A-Za-z'&.\- ]{2,28}", t):
                    continue
                nt = _norm(t)
                if _is_noise(t) or any(
                    bad in nt
                    for bad in ("shop", "quantity", "inventory", "stock", "cargo", "available", "scu")
                ):
                    continue
                name = t.title() if t.isupper() else t
                break
        if not name:
            continue

        header = " ".join(s["text"] for s in segs)
        stock = _stock_from_header(header)
        price = None
        demand_label = None

        for j in range(i, min(i + 6, n)):
            lsegs = [s for s in lines[j]["segments"] if s["x_center"] >= min_x]
            ltxt = " ".join(s["text"] for s in lsegs)
            low = ltxt.lower()

            if j > i and "shop" in low and "quantity" in low:
                break  # next card started

            for s in lsegs:
                stxt = s["text"]
                slow = stxt.lower()

                # Price token: number followed by a slash-unit suffix (SCU is
                # often misread as 8CU/5CU in the game font).
                m = re.search(r"(\d[\d,\.\s]{0,12})/\s*(?:scu|8cu|5cu|units?|uec)", slow)
                if m and _has_digit(m.group(1)):
                    v = _to_int(m.group(1))
                    if v:
                        price = v
                    continue

                # Stock (fallback): numeric segment on a line that mentions SCU
                if stock is None and _has_digit(stxt) and "/" not in stxt:
                    if "scu" in low and "available cargo" not in low:
                        v = _to_int(stxt)
                        if v and 0 < v < 1_000_000:
                            stock = v

                if demand_label is None:
                    nl = _norm(stxt)
                    if any(k in nl for k in ("indemand", "nodemand", "cannotsell")):
                        demand_label = stxt.strip()

        row = {
            "commodity": name,
            "buy": None,
            "sell": None,
            "stock": stock,
            "demand": None,
            "layout": "modal",
        }
        if price is not None:
            if tab == "sell" or (tab is None and demand_label):
                row["sell"] = price
            else:
                row["buy"] = price
        if demand_label:
            row["demand_label"] = demand_label
        results.append(row)

    return results


# ------------------------------------------------------------------
# Table layout
# ------------------------------------------------------------------
def find_header(lines):
    for line in lines:
        joined = _norm(" ".join(s["text"] for s in line["segments"]))
        hits = {}
        for kind, markers in HEADER_MARKERS.items():
            for m in markers:
                if _norm(m) and _norm(m) in joined:
                    for s in line["segments"]:
                        if _norm(m) in _norm(s["text"]):
                            hits.setdefault(kind, s["x_center"])
                            break
        if "commodity" in hits and ("buy" in hits or "sell" in hits):
            return sorted(hits.items(), key=lambda kv: kv[1])
    return None


def parse_table(lines, header_cols, min_x=0):
    kinds = [k for k, _ in header_cols]
    xs = [x for _, x in header_cols]

    header_idx = None
    for i, line in enumerate(lines):
        if any(_norm(m) in _norm(" ".join(s["text"] for s in line["segments"]))
               for m in HEADER_MARKERS["commodity"]):
            header_idx = i
            break

    results = []
    for i, line in enumerate(lines):
        if header_idx is not None and i <= header_idx:
            continue

        name_parts = []
        values = {}

        for s in line["segments"]:
            if s["x_center"] < min_x:
                continue
            band = kinds[-1]
            if len(xs) > 1:
                band = kinds[-1]
                for j in range(len(xs) - 1):
                    if s["x_center"] < xs[j + 1]:
                        band = kinds[j]
                        break

            if _has_digit(s["text"]):
                if band == kinds[0]:
                    # commodity band: numbers here are likely part of the name;
                    # treat as numeric only if it looks like a pure number
                    if re.fullmatch(r"[\d.,\s]+", s["text"]):
                        v = _to_int(s["text"])
                        if v is not None:
                            values.setdefault(band, v)
                    else:
                        name_parts.append(s["text"])
                else:
                    v = _to_int(s["text"])
                    if v is not None:
                        values.setdefault(band, v)
            elif band == kinds[0]:
                name_parts.append(s["text"])

        name = " ".join(name_parts).strip()
        if not name or len(name) < 2 or _is_noise(name):
            continue
        if "buy" not in values and "sell" not in values:
            continue

        results.append({
            "commodity": name,
            "buy": values.get("buy"),
            "sell": values.get("sell"),
            "stock": values.get("stock"),
            "demand": values.get("demand"),
            "layout": "table",
        })
    return results


# ------------------------------------------------------------------
# Fallback positional
# ------------------------------------------------------------------
def parse_fallback(lines, min_x=0):
    results = []
    for line in lines:
        name = None
        nums = []
        for s in line["segments"]:
            if s["x_center"] < min_x:
                continue
            if _has_digit(s["text"]) and re.fullmatch(r"[\d.,\s/]+[A-Za-z]{0,3}", s["text"].strip()):
                v = _to_int(s["text"])
                if v is not None:
                    nums.append(v)
            elif name is None and not _is_noise(s["text"]):
                if _name_is_clean(s["text"]):
                    name = s["text"].strip()
        if name and len(nums) >= 2:
            results.append({
                "commodity": name,
                "buy": nums[0],
                "sell": nums[1],
                "stock": nums[2] if len(nums) > 2 else None,
                "demand": nums[3] if len(nums) > 3 else None,
                "layout": "guessed",
            })
    return results


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------
def _detect_tab(lines, color_image=None, proc_w=None, proc_h=None, right_min=0):
    """Return 'buy', 'sell' or None for the kiosk tab that was scanned."""
    if not lines:
        return None
    texts = []
    for line in lines:
        for s in line["segments"]:
            n = _norm(s["text"])
            if n:
                texts.append((s, n))
    # Prefer the row labels on the right-hand commodity list. The BUY tab shows
    # inventory levels ("MAX INVENTORY"), the LOCAL MARKET VALUE tab shows
    # demand ("IN DEMAND" / "NO DEMAND" / "CANNOT SELL").
    right = [n for s, n in texts if s["x_center"] >= right_min]
    joined = " ".join(right)
    if "demand" in joined:
        return "sell"
    if "inventory" in joined:
        return "buy"

    # Fallback: compare the tab label backgrounds (the active tab is filled).
    if color_image is None or not proc_w or not proc_h:
        return None
    scale = proc_w / float(color_image.shape[1])
    heights = {}
    for s, n in texts:
        if s["y_center"] > 0.18 * proc_h:
            continue
        kind = None
        if n == "buy":
            kind = "buy"
        elif "local" in n and "value" in n:
            kind = "sell"
        if kind is None:
            continue
        x1 = max(0, int(s["x"] / scale))
        y1 = max(0, int(s["y"] / scale))
        x2 = min(color_image.shape[1], int((s["x"] + s["w"]) / scale))
        y2 = min(color_image.shape[0], int((s["y"] + s["h"]) / scale))
        patch = color_image[y1:y2, x1:x2]
        if patch.size:
            heights[kind] = max(heights.get(kind, 0.0), float(patch.mean()))
    if len(heights) == 2 and abs(heights["buy"] - heights["sell"]) >= 6:
        return "buy" if heights["buy"] > heights["sell"] else "sell"
    return None


def parse_commodities(ocr_data, image=None, color_image=None):
    lines = group_lines(ocr_data)
    if not lines:
        return {"rows": [], "layout": "unknown", "headers": []}

    proc_w = image.shape[1] if image is not None else None
    proc_h = image.shape[0] if image is not None else None
    joined_all = " ".join(_norm(s["text"]) for line in lines for s in line["segments"])
    has_left = "selectlocation" in joined_all
    min_x = 0.45 * proc_w if (has_left and proc_w) else 0
    tab = _detect_tab(lines, color_image, proc_w, proc_h, right_min=min_x)

    modal_rows = parse_modal(lines, tab=tab, min_x=min_x)
    header_cols = find_header(lines)
    table_rows = parse_table(lines, header_cols, min_x=min_x) if header_cols else []

    if table_rows and len(table_rows) >= len(modal_rows):
        return {"rows": table_rows, "layout": "table", "headers": header_cols, "tab": tab}
    if modal_rows:
        return {"rows": modal_rows, "layout": "modal", "headers": [], "tab": tab}

    fallback = parse_fallback(lines, min_x=min_x)
    if fallback:
        return {"rows": fallback, "layout": "guessed", "headers": [], "tab": tab}

    return {"rows": [], "layout": "unknown", "headers": [], "tab": tab}


# ------------------------------------------------------------------
# Preprocessing + OCR runner (shared by scanner + bridge)
# ------------------------------------------------------------------
def preprocess(img, scale=5, max_dim=3200):
    h, w = img.shape[:2]
    longest = float(max(h, w))
    if longest > 0 and scale * longest > max_dim:
        scale = max(1.5, max_dim / longest)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(gray)
    variants = {}
    _, otsu_inv = cv2.threshold(clahe, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    variants["otsu_inv"] = otsu_inv
    _, otsu = cv2.threshold(clahe, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variants["otsu"] = otsu
    return variants


def _name_is_clean(name):
    if not name:
        return False
    letters = re.sub(r"[^A-Za-z]", "", name)
    return len(letters) >= 3 and re.fullmatch(r"[A-Za-z][A-Za-z0-9 '&.\-]*", name.strip()) is not None


def _merge_results(collected):
    """Vote across many OCR passes to get the most reliable value per field."""
    def build(allow_guessed):
        buckets = {}
        for score, parsed in collected:
            is_guessed = parsed["layout"] == "guessed"
            if is_guessed and not allow_guessed:
                continue
            for r in parsed["rows"]:
                key = canonical_commodity(r.get("commodity"))
                if key is None:
                    continue
                b = buckets.setdefault(key, {
                    "commodity": key,
                    "layout": parsed["layout"],
                    "best_score": score,
                    "buy": [], "sell": [], "stock": [], "demand": [], "label": [],
                })
                b["best_score"] = max(b["best_score"], score)
                for f in ("buy", "sell", "stock", "demand"):
                    v = r.get(f)
                    if v is not None:
                        b[f].append((v, score))
                if r.get("demand_label"):
                    b["label"].append((r["demand_label"], score))
        return buckets

    buckets = build(allow_guessed=False) or build(allow_guessed=True)

    rows = []
    for b in buckets.values():
        row = {"commodity": b["commodity"], "layout": b["layout"]}
        for f in ("buy", "sell", "stock", "demand"):
            vals = b[f]
            if not vals:
                row[f] = None
                continue
            counts = {}
            for v, s in vals:
                counts[v] = counts.get(v, 0) + 1
            row[f] = max(
                counts,
                key=lambda v: (counts[v], max(s for vv, s in vals if vv == v)),
            )
        if b["label"]:
            counts = {}
            for v, s in b["label"]:
                counts[v] = counts.get(v, 0) + 1
            row["demand_label"] = max(
                counts,
                key=lambda v: (counts[v], max(s for vv, s in b["label"] if vv == v)),
            )
        rows.append((b["best_score"], row))

    rows.sort(key=lambda t: (-t[0], t[1]["commodity"]))
    return [r for _, r in rows]


def parse_inventory(lines, color_image=None, proc_w=None):
    """Read the left-hand YOUR INVENTORIES list (ship cargo) and profit arrows."""
    if not lines or not proc_w:
        return []
    left_max = 0.45 * proc_w
    items = {}
    scale = proc_w / float(color_image.shape[1]) if color_image is not None else 1.0

    for line in lines:
        segs = [s for s in line["segments"] if s["x_center"] < left_max]
        if not segs:
            continue
        joined = " ".join(s["text"] for s in segs)
        name = None
        for s in segs:
            c = canonical_commodity(s["text"])
            if c:
                name = c
                break
        if not name:
            continue

        m = re.search(r"([\d][\d,\.\s]{0,12}?)\s*(?:scu|units?)", joined, re.I)
        qty = _to_int(m.group(1)) if m else None
        if qty is None:
            for s in segs:
                if "scu" in s["text"].lower() and _has_digit(s["text"]):
                    v = _to_int(s["text"])
                    if v:
                        qty = v
                        break
        if not qty or qty <= 0:
            continue

        arrow = None
        if color_image is not None:
            x1 = int(min(s["x"] for s in segs) / scale)
            x2 = int(max(s["x"] + s["w"] for s in segs) / scale)
            y1 = int(min(s["y"] for s in segs) / scale)
            y2 = int(max(s["y"] + s["h"] for s in segs) / scale)
            patch = color_image[max(0, y1):max(0, y2), max(0, x1):max(0, x2)]
            if patch.size:
                hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
                h, sat, val = cv2.split(hsv)
                green = int(np.count_nonzero((h >= 40) & (h <= 95) & (sat >= 60) & (val >= 50)))
                red = int(np.count_nonzero(((h <= 10) | (h >= 170)) & (sat >= 60) & (val >= 50)))
                if max(green, red) >= 8 and abs(green - red) >= 3:
                    arrow = "up" if green > red else "down"

        prev = items.get(name)
        if prev is None:
            items[name] = {"commodity": name, "quantity": qty, "profit": arrow}
        else:
            prev["quantity"] = max(prev["quantity"], qty)
            if prev.get("profit") is None and arrow:
                prev["profit"] = arrow

    return sorted(items.values(), key=lambda r: r["commodity"])


def _probe_left_panel(img):
    """Quick low-cost pass to see if the capture includes YOUR INVENTORIES."""
    try:
        processed = preprocess(img, max_dim=2400)["otsu_inv"]
        data = pytesseract.image_to_data(
            processed, output_type=pytesseract.Output.DICT, config="--oem 3 --psm 6"
        )
    except Exception:
        return None
    txt = re.sub(r"[^a-z]", "", " ".join(data.get("text") or []).lower())
    if "selectlocation" in txt:
        return data, processed
    return None


def run_ocr(img):
    """Run many preprocessing/PSM passes and vote on the most reliable values."""
    inventory = []
    probe = _probe_left_panel(img)
    if probe is not None:
        probe_data, probe_img = probe
        inventory = parse_inventory(
            group_lines(probe_data), color_image=img, proc_w=probe_img.shape[1]
        )
        # OCR the commodity list only: the inventories column confuses layout.
        img = img[:, int(0.45 * img.shape[1]):]

    variants = preprocess(img)
    # 2 preprocessors x 2 layouts is enough for reliable voting and keeps a scan
    # under ~3s. The old adaptive/psm-11 passes were ~30x slower for no gain.
    configs = ["--oem 3 --psm 6", "--oem 3 --psm 4"]
    layout_bonus = {"table": 50, "modal": 40, "guessed": 0, "unknown": -100}
    variant_weight = {"otsu_inv": 6, "otsu": 2}

    collected = []
    layout_scores = {}
    best_data = None
    best_score = None
    best_processed = None
    best_parsed = None
    for vname, processed in variants.items():
        for cfg in configs:
            try:
                data = pytesseract.image_to_data(
                    processed, output_type=pytesseract.Output.DICT, config=cfg
                )
            except Exception:
                continue
            parsed = parse_commodities(data, image=processed, color_image=img)
            score = layout_bonus.get(parsed["layout"], 0)
            for r in parsed["rows"]:
                if r.get("sell") or r.get("buy"):
                    score += 10
                if canonical_commodity(r.get("commodity")):
                    score += 6
                if r.get("stock"):
                    score += 4
            score += variant_weight.get(vname, 0)
            collected.append((score, parsed))
            layout_scores[parsed["layout"]] = layout_scores.get(parsed["layout"], 0) + max(score, 0)
            if best_score is None or score > best_score:
                best_score = score
                best_data = data
                best_processed = processed
                best_parsed = parsed

    if not collected:
        return None, {"rows": [], "layout": "unknown"}, "none"

    rows = _merge_results(collected)
    layout = "table" if any(r["layout"] == "table" for r in rows) else (
        "modal" if any(r["layout"] == "modal" for r in rows) else "unknown"
    )
    tab = best_parsed.get("tab") if best_parsed else None
    inventory = []
    if probe is None and best_data is not None and best_processed is not None:
        inventory = parse_inventory(
            group_lines(best_data), color_image=img, proc_w=best_processed.shape[1]
        )
    return best_data, {
        "rows": rows,
        "layout": layout,
        "headers": [],
        "inventory": inventory,
        "tab": tab,
    }, "merged"


def annotate(image, ocr_data):
    vis = image.copy()
    if vis.ndim == 2:
        vis = cv2.cvtColor(vis, cv2.COLOR_GRAY2BGR)
    for i in range(len(ocr_data.get("text", []))):
        txt = (ocr_data["text"][i] or "").strip()
        if not txt:
            continue
        try:
            conf = float(ocr_data["conf"][i])
        except (TypeError, ValueError):
            continue
        if conf < 0:
            continue
        x, y = ocr_data["left"][i], ocr_data["top"][i]
        w, h = ocr_data["width"][i], ocr_data["height"][i]
        color = (0, 200, 0) if conf > 60 else (0, 165, 255)
        cv2.rectangle(vis, (x, y), (x + w, y + h), color, 1)
    return vis


# ------------------------------------------------------------------
# Station detection
# ------------------------------------------------------------------
KNOWN_STATIONS = [
    "Orison", "Area18", "New Babbage", "Lorville", "Levski", "Grim HEX",
    "Port Tressler", "Everus Harbor", "Baijini Point", "Checkmate", "Ruin Station",
    "MIC-L1", "MIC-L2", "MIC-L3", "MIC-L4", "MIC-L5",
    "ARC-L1", "ARC-L2", "ARC-L3", "ARC-L4", "ARC-L5",
    "CRU-L1", "CRU-L2", "CRU-L3", "CRU-L4", "CRU-L5",
    "HUR-L1", "HUR-L2", "HUR-L3", "HUR-L4", "HUR-L5",
    "Clio", "Aquila", "Epoch", "Shallow Frontier", "Endless Odyssey",
    "Faint Glen", "Red Crossroads", "Yellow Core", "Modern Icarus",
    "Modern Express", "Ambitious Dream", "High Course",
    "Shepherd's Rest", "Fallow Field", "Devlin Scrap", "Salvage Center",
    "Jackson's Swap", "Seer's Canyon", "Gaslight", "Dudley & Daughters",
    "Rayari", "Anvik", "Deltana", "McGrath", "Kaltag",
    "Hickes", "Deakins", "Norgaard", "Hadley", "Anderson",
    "Bueno Ravine", "Sacren's Plot", "Canard View", "Rustville",
    "Samson & Son's", "Rat's Nest", "Starlight", "People's Service",
    "Terra Gateway", "Stanton Gateway", "Nyx Gateway", "Pyro Gateway",
    "Orbituary", "Megumi Refueling", "Seraphim Station", "Endgame",
    "Shubin", "ArcCorp Mining", "Rod's Fuel", "Chawla's Beach",
    "Ashland", "Brio's Breaker Yard", "Patch City", "Cubby Blast",
    "New Deal", "Casaba Outlet", "Omni Polo", "Traveler Rentals",
    "Platinum Bay", "Astro Armada", "Dumper's Depot", "Centermass", "HDMS",
]


def detect_station(ocr_data):
    if not ocr_data:
        return "Unknown Station"
    texts = [t.strip() for t in ocr_data.get("text", []) if t and t.strip()]
    # Exact line match only. The trade popup contains words like "HIGH"
    # (demand), which must never fuzzy-match "High Course".
    for t in texts:
        n = _norm(t)
        for k in KNOWN_STATIONS:
            if _norm(k) == n:
                return k
    # A full station name embedded in a longer line is safe; the reverse
    # (short OCR token inside a station name) is what caused bad matches.
    for t in texts:
        n = _norm(t)
        if len(n) < 6:
            continue
        for k in KNOWN_STATIONS:
            nk = _norm(k)
            if len(nk) >= 6 and nk in n:
                return k
    return "Unknown Station"

# ------------------------------------------------------------------
# Scan history (remember every scan, merged per station)
# ------------------------------------------------------------------
def merge_history(scan_dir, station, rows, ts):
    """Upsert a scan into history.json and return the merged station snapshot.

    Non-null fields from the newest scan win; older known values are kept, so
    repeated scans build up a fuller picture even if one pass misses a row.
    """
    path = Path(scan_dir) / "history.json"
    hist = {"version": 1, "stations": {}}
    loaded_ok = False
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict) and isinstance(loaded.get("stations"), dict):
            hist = loaded
            loaded_ok = True
    except Exception:
        pass

    if loaded_ok:
        # Drop junk left behind by older fuzzy matching and merge duplicates.
        for st in hist["stations"].values():
            if not isinstance(st, dict):
                continue
            comms = st.get("commodities")
            if not isinstance(comms, dict):
                st["commodities"] = {}
                continue
            clean = {}
            for name, entry in comms.items():
                canon = canonical_commodity(name)
                if not canon or not isinstance(entry, dict):
                    continue
                prev = clean.get(canon)
                if prev is None or (entry.get("scanned_at") or 0) >= (prev.get("scanned_at") or 0):
                    clean[canon] = entry
            st["commodities"] = clean

    def upsert(st, items, when):
        for row in items:
            name = (row.get("commodity") or "").strip()
            if not name:
                continue
            key = canonical_commodity(name)
            if not key:
                continue
            entry = st["commodities"].setdefault(key, {})
            for field in ("buy", "sell", "stock", "demand"):
                value = row.get(field)
                if value is not None:
                    entry[field] = value
            if row.get("demand_label"):
                entry["demand_label"] = row["demand_label"]
            entry["scanned_at"] = int(when)

    # First run: seed from every scan JSON already in the folder
    if not loaded_ok:
        for p in Path(scan_dir).glob("*.json"):
            if p.name == "history.json":
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            old_station = data.get("station")
            old_rows = data.get("commodities")
            if not old_station or not isinstance(old_rows, list):
                continue
            old_ts = data.get("timestamp") or 0
            st = hist["stations"].setdefault(old_station, {"commodities": {}, "updated": 0})
            upsert(st, old_rows, old_ts)
            st["updated"] = max(int(st.get("updated") or 0), int(old_ts))

    station_key = (station or "Unknown Station").strip() or "Unknown Station"
    st = hist["stations"].setdefault(station_key, {"commodities": {}, "updated": 0})
    upsert(st, rows, ts)

    st["updated"] = max(int(st.get("updated") or 0), int(ts))
    hist["version"] = 1
    try:
        path.write_text(json.dumps(hist, indent=2), encoding="utf-8")
    except Exception:
        pass

    merged = []
    for name in sorted(st["commodities"]):
        entry = st["commodities"][name]
        merged.append({
            "commodity": name,
            "buy": entry.get("buy"),
            "sell": entry.get("sell"),
            "stock": entry.get("stock"),
            "demand": entry.get("demand"),
            "demand_label": entry.get("demand_label"),
            "scanned_at": entry.get("scanned_at"),
        })
    return merged
