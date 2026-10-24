# scanner_overlay_bridge.py
# JSON IPC bridge for the Electron overlay.
# Reads commands from stdin, writes JSON events to stdout.

import sys
import os
import json
import time
import threading
from pathlib import Path

import mss
import cv2
import numpy as np
import pytesseract
from pynput import keyboard, mouse

import sc_parser

if sys.platform == "win32" and sys.stdout is not None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

def _documents_dir():
    home = Path.home()
    for cand in (home / "OneDrive" / "Documents", home / "Documents"):
        if cand.is_dir():
            return cand
    return home

SCAN_DIR = _documents_dir() / "SC-Truckers Scans"
SCAN_DIR.mkdir(parents=True, exist_ok=True)
DEBUG_DIR = SCAN_DIR / "debug"
CALIB_FILE = SCAN_DIR / "scanner_region.json"

# Migrate calibration/scans from older locations (~/SC-Scans, local Documents)
for _legacy in (Path.home() / "SC-Scans", Path.home() / "Documents" / "SC-Truckers Scans"):
    if _legacy == SCAN_DIR or not _legacy.is_dir():
        continue
    _legacy_calib = _legacy / "scanner_region.json"
    if not CALIB_FILE.exists() and _legacy_calib.exists():
        try:
            CALIB_FILE.write_text(_legacy_calib.read_text())
        except Exception:
            pass
    for _json in _legacy.glob("*.json"):
        _dest = SCAN_DIR / _json.name
        if not _dest.exists():
            try:
                _dest.write_text(_json.read_text())
            except Exception:
                pass
DEFAULT_REGION = {"top": 200, "left": 300, "width": 1200, "height": 800}

if getattr(sys, "frozen", False):
    base = Path(sys._MEIPASS)
    bundled = base / "tesseract.exe"
    if bundled.exists():
        pytesseract.pytesseract.tesseract_cmd = str(bundled)
        os.environ["TESSDATA_PREFIX"] = str(base / "tessdata")
    else:
        pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
else:
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

calibrating = False
calib_points = []
last_preview_at = 0.0
STATION_OVERRIDE = ""


def send(msg):
    try:
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()
    except Exception:
        pass


def load_region():
    try:
        r = json.loads(CALIB_FILE.read_text())
        if all(k in r for k in ("top", "left", "width", "height")):
            return r
    except Exception:
        pass
    return dict(DEFAULT_REGION)


def save_region(region):
    CALIB_FILE.write_text(json.dumps(region))


def grab(region):
    with mss.MSS() as sct:
        shot = sct.grab(region)
        img = np.array(shot)
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)


def preprocess_variants(img):
    return sc_parser.preprocess(img)


def run_ocr(img):
    return sc_parser.run_ocr(img)


def annotate(img, ocr_data):
    if ocr_data is None:
        return img
    return sc_parser.annotate(img, ocr_data)


def do_scan():
    region = load_region()
    send({"type": "status", "message": f"Scanning {region['width']}x{region['height']}..."})

    try:
        img = grab(region)
    except Exception as e:
        send({"type": "status", "message": f"Capture failed: {e}"})
        return

    DEBUG_DIR.mkdir(exist_ok=True)
    stamp = int(time.time())
    cv2.imwrite(str(DEBUG_DIR / f"raw_{stamp}.png"), img)

    ocr_data, parsed, tag = run_ocr(img)
    rows = parsed["rows"]
    layout = parsed["layout"]

    if ocr_data is not None:
        cv2.imwrite(str(DEBUG_DIR / f"ocr_{stamp}.png"), annotate(img, ocr_data))

    if not rows:
        send({
            "type": "status",
            "message": f"No data (layout={layout}, {tag}). Open debug folder.",
        })
        return

    station = STATION_OVERRIDE or (
        sc_parser.detect_station(ocr_data) if ocr_data is not None else "Unknown Station"
    )
    ts = int(time.time())
    merged_rows = sc_parser.merge_history(SCAN_DIR, station, rows, ts)
    scan = {
        "timestamp": ts,
        "station": station,
        "commodities": merged_rows,
        "inventory": parsed.get("inventory") or [],
        "layout": layout,
        "tab": parsed.get("tab"),
        "source": "player-scan",
        "version": "1.1",
    }
    out_path = sc_parser.station_scan_path(SCAN_DIR, station)
    out_path.write_text(json.dumps(scan, indent=2))
    send({"type": "status", "message": f"Saved {out_path.name}: {len(rows)} new / {len(merged_rows)} known"})
    send({"type": "scan_complete", "payload": scan, "file": str(out_path)})


def on_click(x, y, button, pressed):
    global calibrating, calib_points, last_preview_at
    if not calibrating or not pressed or button != mouse.Button.left:
        return
    calib_points.append((int(x), int(y)))
    if len(calib_points) == 1:
        last_preview_at = 0.0
        send({
            "type": "calibration_preview",
            "payload": {"left": int(x), "top": int(y), "width": 0, "height": 0},
        })
        send({"type": "status", "message": "Now click BOTTOM-RIGHT of the table"})
    elif len(calib_points) == 2:
        (x1, y1), (x2, y2) = calib_points
        region = {
            "top": min(y1, y2),
            "left": min(x1, x2),
            "width": abs(x2 - x1),
            "height": abs(y2 - y1),
        }
        if region["width"] < 50 or region["height"] < 50:
            send({"type": "calibration_cancelled"})
            send({"type": "status", "message": "Region too small, try again"})
        else:
            save_region(region)
            send({"type": "calibration_complete", "payload": region})
        calibrating = False
        calib_points = []


def on_move(x, y):
    global last_preview_at
    if not calibrating or not calib_points:
        return
    now = time.monotonic()
    if now - last_preview_at < 1 / 30:
        return
    last_preview_at = now
    x1, y1 = calib_points[0]
    x2, y2 = int(x), int(y)
    send({
        "type": "calibration_preview",
        "payload": {
            "left": min(x1, x2),
            "top": min(y1, y2),
            "width": abs(x2 - x1),
            "height": abs(y2 - y1),
        },
    })


def start_calibration():
    global calibrating, calib_points
    calibrating = True
    calib_points = []
    send({"type": "calibration_cancelled"})
    send({"type": "status", "message": "Click TOP-LEFT, then BOTTOM-RIGHT of the table"})


def handle_stdin():
    global STATION_OVERRIDE
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
        except json.JSONDecodeError:
            continue
        action = cmd.get("action")
        if action == "scan":
            do_scan()
        elif action == "calibrate":
            start_calibration()
        elif action == "set_station":
            STATION_OVERRIDE = str(cmd.get("station") or "").strip()
            if STATION_OVERRIDE:
                send({"type": "status", "message": f"Station set to {STATION_OVERRIDE}"})
            else:
                send({"type": "status", "message": "Station set to auto-detect"})
        elif action == "quit":
            send({"type": "status", "message": "Exiting"})
            os._exit(0)


def on_press(key):
    try:
        if key == keyboard.Key.f8:
            start_calibration()
        elif key == keyboard.Key.f9:
            do_scan()
        elif key == keyboard.Key.esc and calibrating:
            calibrating = False
            calib_points = []
            send({"type": "calibration_cancelled"})
            send({"type": "status", "message": "Calibration cancelled"})
        elif key == keyboard.Key.f10:
            send({"type": "status", "message": "Exiting"})
            return False
    except Exception as e:
        send({"type": "status", "message": f"Error: {e}"})


if __name__ == "__main__":
    send({"type": "status", "message": "Bridge ready"})
    with mss.MSS() as sct:
        send({
            "type": "display_layout",
            "payload": [
                {key: monitor[key] for key in ("left", "top", "width", "height")}
                for monitor in sct.monitors[1:]
            ],
        })
    r = load_region()
    send({
        "type": "status",
        "message": f"Region {r['width']}x{r['height']} at ({r['left']},{r['top']})",
    })
    send({"type": "status", "message": f"Scans folder: {SCAN_DIR}"})

    mouse_listener = mouse.Listener(on_click=on_click, on_move=on_move)
    mouse_listener.start()

    stdin_thread = threading.Thread(target=handle_stdin, daemon=True)
    stdin_thread.start()

    with keyboard.Listener(on_press=on_press) as listener:
        listener.join()

    mouse_listener.stop()
    send({"type": "status", "message": "Bridge stopped"})
