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
SCAN_LOCK = threading.Lock()
LIVE_SCAN_LOCK = threading.Lock()
LIVE_SCAN_STOP = threading.Event()
LIVE_SCAN_THREAD = None
LIVE_SCAN_INTERVAL_SECONDS = 2.0
LIVE_SCAN_COOLDOWN_SECONDS = 15.0
LIVE_SCAN_CHANGE_RATIO = 0.0015
LIVE_SCAN_STABLE_RATIO = 0.0005


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


def annotate(img, ocr_data, scale=None, offset_x=0):
    if ocr_data is None:
        return img
    return sc_parser.annotate(img, ocr_data, scale=scale, offset_x=offset_x)


def do_scan():
    if not SCAN_LOCK.acquire(blocking=False):
        send({"type": "status", "message": "A scan is already running; ignoring duplicate trigger."})
        return
    try:
        return _do_scan()
    finally:
        SCAN_LOCK.release()


def _live_signature(region):
    image = grab(region)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, (96, 64), interpolation=cv2.INTER_AREA)


def _live_scan_loop():
    send({"type": "live_scan_status", "enabled": True})
    send({
        "type": "status",
        "message": (
            "Low-impact live scan enabled: checking every 2 seconds; "
            "OCR runs only after a stable change, at most once every 15 seconds."
        ),
    })
    try:
        previous = _live_signature(load_region())
    except Exception as exc:
        send({"type": "status", "message": f"Live scan capture failed: {exc}"})
        send({"type": "live_scan_status", "enabled": False})
        return

    do_scan()
    last_scan = time.monotonic()
    candidate = None
    stable_samples = 0
    last_error = None

    while not LIVE_SCAN_STOP.wait(LIVE_SCAN_INTERVAL_SECONDS):
        if calibrating:
            continue
        try:
            current = _live_signature(load_region())
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            if error != last_error:
                send({"type": "status", "message": f"Live scan capture failed: {error}"})
                last_error = error
            continue

        last_error = None
        change_ratio = sc_parser.image_change_ratio(previous, current)
        if candidate is None:
            if change_ratio >= LIVE_SCAN_CHANGE_RATIO:
                candidate = current
                stable_samples = 0
            else:
                previous = current
            continue

        stable_ratio = sc_parser.image_change_ratio(candidate, current)
        if stable_ratio <= LIVE_SCAN_STABLE_RATIO:
            stable_samples += 1
        else:
            candidate = current
            stable_samples = 0

        if stable_samples < 2:
            continue

        now = time.monotonic()
        if now - last_scan >= LIVE_SCAN_COOLDOWN_SECONDS:
            do_scan()
            last_scan = now
        previous = current
        candidate = None
        stable_samples = 0

    send({"type": "live_scan_status", "enabled": False})


def toggle_live_scan():
    global LIVE_SCAN_THREAD
    with LIVE_SCAN_LOCK:
        if LIVE_SCAN_THREAD is not None and LIVE_SCAN_THREAD.is_alive() and not LIVE_SCAN_STOP.is_set():
            LIVE_SCAN_STOP.set()
            enabled = False
        else:
            LIVE_SCAN_STOP.clear()
            LIVE_SCAN_THREAD = threading.Thread(target=_live_scan_loop, daemon=True)
            LIVE_SCAN_THREAD.start()
            enabled = True
    if not enabled:
        send({"type": "live_scan_status", "enabled": False})
        send({"type": "status", "message": "Low-impact live scan disabled."})


def _do_scan():
    region = load_region()
    send({"type": "status", "message": f"Scanning {region['width']}x{region['height']}..."})

    try:
        img = grab(region)
    except Exception as e:
        send({"type": "status", "message": f"Capture failed: {e}"})
        return

    DEBUG_DIR.mkdir(exist_ok=True)
    stamp = int(time.time() * 1000)
    raw_path = DEBUG_DIR / f"raw_{stamp}.png"
    if not cv2.imwrite(str(raw_path), img):
        send({"type": "status", "message": f"Could not save debug capture: {raw_path}"})

    ocr_data, parsed, tag = run_ocr(img)
    rows = parsed["rows"]
    layout = parsed["layout"]
    ocr_diagnostics = parsed.get("diagnostics", {})

    if ocr_data is not None:
        scale = ocr_diagnostics.get("coordinate_scale")
        offset_x = ocr_diagnostics.get("crop_left", 0)
        annotated = annotate(img, ocr_data, scale=scale, offset_x=offset_x)
        annotated_path = DEBUG_DIR / f"ocr_{stamp}.png"
        if not cv2.imwrite(str(annotated_path), annotated):
            send({"type": "status", "message": f"Could not save OCR overlay: {annotated_path}"})

    station = STATION_OVERRIDE or (
        sc_parser.detect_station(ocr_data) if ocr_data is not None else "Unknown Station"
    )
    debug_path = DEBUG_DIR / f"scan_{stamp}.json"
    debug_report = {
        "timestamp": int(time.time()),
        "station": station,
        "capture": {
            "region": region,
            "width": int(img.shape[1]),
            "height": int(img.shape[0]),
        },
        "ocr": ocr_diagnostics,
        "result": {
            "tag": tag,
            "layout": layout,
            "tab": parsed.get("tab"),
            "rows": rows,
        },
    }
    try:
        debug_path.write_text(json.dumps(debug_report, indent=2), encoding="utf-8")
    except OSError as exc:
        send({"type": "status", "message": f"Could not save scan diagnostics: {exc}"})

    if not rows:
        errors = ocr_diagnostics.get("errors", [])
        detail = errors[0].get("error", "No reliable values were found.") if errors else "No reliable values were found."
        send({
            "type": "status",
            "message": f"No reliable data ({layout}, {tag}): {detail} Debug: {debug_path.name}",
        })
        return

    ts = int(time.time())
    merged_rows = sc_parser.merge_history(SCAN_DIR, station, rows, ts)
    scan = {
        "timestamp": ts,
        "station": station,
        "commodities": merged_rows,
        "inventory": parsed.get("inventory") or [],
        "layout": layout,
        "tab": parsed.get("tab"),
        "quality": sc_parser.quality_report(parsed),
        "source": "player-scan",
        "version": "1.1",
    }
    out_path = sc_parser.station_scan_path(SCAN_DIR, station)
    out_path.write_text(json.dumps(scan, indent=2))
    quality = scan["quality"]
    review = " Review recommended." if quality["review_recommended"] else ""
    send({
        "type": "status",
        "message": (
            f"Saved {out_path.name}: {len(rows)} new / {len(merged_rows)} known; "
            f"tab={quality['active_tab'] or 'unconfirmed'}, "
            f"{quality['ocr_passes']} OCR passes.{review} Debug: {debug_path.name}"
        ),
    })
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
        elif action == "toggle_live_scan":
            toggle_live_scan()
        elif action == "quit":
            LIVE_SCAN_STOP.set()
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
