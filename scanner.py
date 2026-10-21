# scanner.py
# Standalone Star Citizen commodity terminal scanner
# Hotkeys: F8=calibrate, F9=scan, F10=quit

import sys
import os
import mss
import cv2
import pytesseract
import json
import time
import numpy as np
from pathlib import Path
from pynput import keyboard, mouse

# Force UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ---- Config ----
SCAN_DIR = Path.home() / "SC-Scans"
SCAN_DIR.mkdir(exist_ok=True)
CALIB_FILE = Path.home() / "SC-Scans" / "scanner_region.json"

# Tesseract path: bundled exe or system install
if getattr(sys, "frozen", False):
    # Running as PyInstaller exe
    tesseract_dir = Path(sys._MEIPASS)
    bundled_tess = tesseract_dir / "tesseract.exe"
    if bundled_tess.exists():
        pytesseract.pytesseract.tesseract_cmd = str(bundled_tess)
        os.environ["TESSDATA_PREFIX"] = str(tesseract_dir / "tessdata")
    else:
        pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
else:
    # Running as script
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# ---- Known stations ----
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

calibrating = False
calib_points = []


def log(msg):
    print(msg, flush=True)


def load_region():
    try:
        return json.loads(CALIB_FILE.read_text())
    except Exception:
        return {"top": 200, "left": 300, "width": 1200, "height": 800}


def save_region(region):
    CALIB_FILE.write_text(json.dumps(region))


def parse_num(s):
    try:
        return int(s.replace(",", "").replace(".", ""))
    except Exception:
        return None


def detect_station(ocr_data):
    texts = [t.strip() for t in ocr_data["text"] if t.strip()]
    for t in texts[:30]:
        for known in KNOWN_STATIONS:
            if known.lower() in t.lower():
                return known
    return "Unknown Station"


def parse_rows(ocr_data):
    lines = {}
    for i, text in enumerate(ocr_data["text"]):
        text = text.strip()
        if not text:
            continue
        y = ocr_data["top"][i]
        line_key = round(y / 12) * 12
        lines.setdefault(line_key, []).append(
            {"text": text, "x": ocr_data["left"][i], "conf": ocr_data["conf"][i]}
        )

    rows = []
    for y in sorted(lines.keys()):
        items = sorted(lines[y], key=lambda x: x["x"])
        filtered = [(i["text"], i["conf"]) for i in items if i["conf"] > 30]
        if not filtered:
            continue
        texts = [t for t, _ in filtered]

        nums = []
        for t in texts[1:]:
            clean = t.replace(",", "").replace(".", "")
            if clean.isdigit():
                nums.append(int(clean))

        if len(nums) >= 2 and len(texts[0]) > 2:
            rows.append(
                {
                    "commodity": texts[0],
                    "buy": nums[0] if len(nums) > 0 else None,
                    "sell": nums[1] if len(nums) > 1 else None,
                    "stock": nums[2] if len(nums) > 2 else None,
                    "demand": nums[3] if len(nums) > 3 else None,
                }
            )
    return rows


def capture_and_parse():
    global calibrating
    if calibrating:
        log("[WARN] Finish calibration first (F8)")
        return

    region = load_region()
    log("[SCAN] Capturing screen...")

    try:
        with mss.mss() as sct:
            screenshot = sct.grab(region)
            img = np.array(screenshot)
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        _, thresh = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)

        custom_config = (
            r"--oem 3 --psm 6 "
            r"-c tessedit_char_whitelist="
            r"0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz.,()-/'"
        )
        data = pytesseract.image_to_data(
            thresh, output_type=pytesseract.Output.DICT, config=custom_config
        )

        rows = parse_rows(data)
        station = detect_station(data)

        if not rows:
            log("[WARN] No commodities detected. Adjust region (F8) or check lighting.")
            return

        scan = {
            "timestamp": time.time(),
            "station": station,
            "commodities": rows,
            "source": "player-scan",
            "version": "1.0",
        }

        fp = SCAN_DIR / f"scan_{int(time.time())}.json"
        fp.write_text(json.dumps(scan, indent=2))
        log(f"[OK] Saved {len(rows)} commodities from {station} -> {fp}")

    except Exception as e:
        log(f"[ERROR] Scan failed: {e}")


def on_click(x, y, button, pressed):
    global calibrating, calib_points
    if not calibrating or not pressed:
        return

    calib_points.append((x, y))
    if len(calib_points) == 1:
        log("  [OK] Top-left set. Click BOTTOM-RIGHT of terminal.")
    elif len(calib_points) == 2:
        x1, y1 = calib_points[0]
        x2, y2 = calib_points[1]
        region = {
            "top": min(y1, y2),
            "left": min(x1, x2),
            "width": abs(x2 - x1),
            "height": abs(y2 - y1),
        }
        save_region(region)
        log(f"  [OK] Region saved: {region}")
        calibrating = False
        calib_points = []


def start_calibration():
    global calibrating
    calibrating = True
    calib_points.clear()
    log("")
    log("CALIBRATION MODE")
    log("  1. Open commodity terminal in-game")
    log("  2. Click TOP-LEFT corner of the commodity table")
    log("  3. Click BOTTOM-RIGHT corner of the commodity table")
    log("")


def on_press(key):
    try:
        if key == keyboard.Key.f8:
            start_calibration()
        elif key == keyboard.Key.f9:
            capture_and_parse()
        elif key == keyboard.Key.f10:
            log("Exiting...")
            return False
    except Exception as e:
        log(f"[ERROR] {e}")


if __name__ == "__main__":
    print("=" * 56)
    print("  SC Price Scanner v1.0 - Standalone")
    print("  Screen capture + OCR, zero game injection")
    print("=" * 56)
    print()
    print("  Hotkeys:")
    print("    F8  = Calibrate capture region (once per resolution)")
    print("    F9  = Scan terminal (look at commodity kiosk)")
    print("    F10 = Quit")
    print()
    print(f"  Scans saved to: {SCAN_DIR}")
    print()

    if not CALIB_FILE.exists():
        print("  [!] No calibration found. Press F8 to set region.")
    else:
        r = load_region()
        print(f"  [OK] Region: {r['width']}x{r['height']} at ({r['left']},{r['top']})")
    print()
    print("Waiting for hotkey...")

    mouse_listener = mouse.Listener(on_click=on_click)
    mouse_listener.start()

    with keyboard.Listener(on_press=on_press) as listener:
        listener.join()

    mouse_listener.stop()
    log("Scanner stopped.")
