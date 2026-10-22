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

import re

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
    "Acrylic", "Agricium", "Agricultural Supplies", "Aluminium", "Ammonia",
    "Argon", "Astrocrete", "Beryl", "Bexalite", "Borase", "Chlorine",
    "Compboard", "Construction Blocks", "Copper", "Corundum", "Decari",
    "Diamond", "Distillates", "Energy", "Fluorine", "Gold", "Gasoline",
    "Hephaestanite", "Hydrogen", "Inert Materials", "Iodine", "Iridium",
    "Laranite", "Liquid Fuel", "Medical Supplies", "Nitrogen", "Partillium",
    "Pressurized Ice", "Processed Food", "Quantanium", "Quartz", "Recycled",
    "Recycled Material Composite", "Research", "Salvage", "Scrap", "Steel",
    "Stims", "Tin", "Titanium", "Tungsten", "Waste", "Xenon", "Zinc",
    "Aluminium", "RCM", "Waste", "Scrap", "Steel",
]

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


def canonical_commodity(name):
    """Fuzzy-map a noisy OCR name onto a known commodity, else None."""
    if not name:
        return None
    norm = re.sub(r"[^a-z]", "", str(name).lower())
    if not norm:
        return None
    best = None
    for c in KNOWN_COMMODITIES:
        cn = re.sub(r"[^a-z]", "", c.lower())
        if cn and cn in norm:
            if best is None or len(cn) > len(best[1]):
                best = (c, cn)
    return best[0] if best else None


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


def parse_modal(lines):
    results = []
    n = len(lines)

    for i, line in enumerate(lines):
        joined = " ".join(s["text"] for s in line["segments"]).lower()
        if "shop" not in joined or "quantity" not in joined:
            continue

        # Commodity name: fuzzy-match any segment on the header line
        name = None
        for s in line["segments"]:
            c = canonical_commodity(s["text"])
            if c:
                name = c
                break
        if not name:
            continue

        stock = _stock_from_header(" ".join(s["text"] for s in line["segments"]))
        price = None

        for j in range(i, min(i + 6, n)):
            ltxt = " ".join(s["text"] for s in lines[j]["segments"])
            low = ltxt.lower()

            if j > i and "shop" in low and "quantity" in low:
                break  # next card started

            for s in lines[j]["segments"]:
                stxt = s["text"]
                slow = stxt.lower()

                # Price token: has a slash-unit suffix
                if ("/unit" in slow or "/scu" in slow or "/uec" in slow) and _has_digit(stxt):
                    v = _to_int(stxt)
                    if v:
                        price = v
                    continue

                # Stock (fallback): numeric segment on a line that mentions SCU
                if stock is None and _has_digit(stxt) and "/" not in stxt:
                    if "scu" in low and "available cargo" not in low:
                        v = _to_int(stxt)
                        if v and 0 < v < 1_000_000:
                            stock = v

        results.append({
            "commodity": name,
            "buy": None,
            "sell": price,
            "stock": stock,
            "demand": None,
            "layout": "modal",
        })

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


def parse_table(lines, header_cols):
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
def parse_fallback(lines):
    results = []
    for line in lines:
        name = None
        nums = []
        for s in line["segments"]:
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
def parse_commodities(ocr_data, image=None):
    lines = group_lines(ocr_data)
    if not lines:
        return {"rows": [], "layout": "unknown", "headers": []}

    modal_rows = parse_modal(lines)
    header_cols = find_header(lines)
    table_rows = parse_table(lines, header_cols) if header_cols else []

    if table_rows and len(table_rows) >= len(modal_rows):
        return {"rows": table_rows, "layout": "table", "headers": header_cols}
    if modal_rows:
        return {"rows": modal_rows, "layout": "modal", "headers": []}

    fallback = parse_fallback(lines)
    if fallback:
        return {"rows": fallback, "layout": "guessed", "headers": []}

    return {"rows": [], "layout": "unknown", "headers": []}


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
                    if not is_guessed and _name_is_clean(r.get("commodity")):
                        key = r["commodity"].strip().title()
                    else:
                        continue
                b = buckets.setdefault(key, {
                    "commodity": key,
                    "layout": parsed["layout"],
                    "best_score": score,
                    "buy": [], "sell": [], "stock": [], "demand": [],
                })
                b["best_score"] = max(b["best_score"], score)
                for f in ("buy", "sell", "stock", "demand"):
                    v = r.get(f)
                    if v is not None:
                        b[f].append((v, score))
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
        rows.append((b["best_score"], row))

    rows.sort(key=lambda t: (-t[0], t[1]["commodity"]))
    return [r for _, r in rows]


def run_ocr(img):
    """Run many preprocessing/PSM passes and vote on the most reliable values."""
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
    for vname, processed in variants.items():
        for cfg in configs:
            try:
                data = pytesseract.image_to_data(
                    processed, output_type=pytesseract.Output.DICT, config=cfg
                )
            except Exception:
                continue
            parsed = parse_commodities(data, image=processed)
            score = layout_bonus.get(parsed["layout"], 0)
            for r in parsed["rows"]:
                if r.get("sell") or r.get("buy"):
                    score += 10
                if canonical_commodity(r.get("commodity")) or _name_is_clean(r.get("commodity")):
                    score += 6
                if r.get("stock"):
                    score += 4
            score += variant_weight.get(vname, 0)
            collected.append((score, parsed))
            layout_scores[parsed["layout"]] = layout_scores.get(parsed["layout"], 0) + max(score, 0)
            if best_score is None or score > best_score:
                best_score = score
                best_data = data

    if not collected:
        return None, {"rows": [], "layout": "unknown"}, "none"

    rows = _merge_results(collected)
    layout = "table" if any(r["layout"] == "table" for r in rows) else (
        "modal" if any(r["layout"] == "modal" for r in rows) else "unknown"
    )
    return best_data, {"rows": rows, "layout": layout, "headers": []}, "merged"


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
    texts = [t.strip() for t in ocr_data.get("text", []) if t and t.strip()]
    for t in texts:
        n = _norm(t)
        for k in KNOWN_STATIONS:
            if _norm(k) == n:
                return k
    for t in texts:
        n = _norm(t)
        if len(n) < 4:
            continue
        for k in KNOWN_STATIONS:
            nk = _norm(k)
            if len(nk) >= 4 and (nk in n or n in nk):
                return k
    return "Unknown Station"
