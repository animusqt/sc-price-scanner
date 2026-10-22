# scanner.py
# Star Citizen commodity terminal scanner
# Screen capture + OCR. No game injection, no memory reads.
#
# Hotkeys:
#   F7  = Toggle live preview window (see exactly what is captured)
#   F8  = Calibrate capture region (click top-left, then bottom-right)
#   F9  = Scan terminal
#   F10 = Quit

import sys
import os
import json
import time
import queue
import threading
from pathlib import Path

import mss
import cv2
import numpy as np
import pytesseract
from pynput import keyboard, mouse

import sc_parser

# Force UTF-8 output on Windows (stdout is None in --noconsole mode)
if sys.platform == "win32" and sys.stdout is not None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ---- Paths ----
SCAN_DIR = Path.home() / "SC-Scans"
SCAN_DIR.mkdir(exist_ok=True)
DEBUG_DIR = SCAN_DIR / "debug"
CALIB_FILE = SCAN_DIR / "scanner_region.json"

# ---- Tesseract ----
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

DEFAULT_REGION = {"top": 200, "left": 300, "width": 1200, "height": 800}

# ---- Shared state ----
cmd_queue = queue.Queue()
calibrating = False
calib_points = []
status_text = "Ready. F8 = calibrate, F9 = scan, F7 = preview, F10 = quit."


def log(msg):
    global status_text
    status_text = msg
    try:
        print(msg, flush=True)
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
    """Return dict of preprocessing variants to try with OCR."""
    return sc_parser.preprocess(img)


def run_ocr(img):
    """Delegate to the shared, multi-pass voting parser."""
    return sc_parser.run_ocr(img)


def annotate(img, ocr_data):
    """Draw OCR boxes onto a copy of the image for debugging."""
    if ocr_data is None:
        return img
    return sc_parser.annotate(img, ocr_data)


def do_scan():
    global status_text
    region = load_region()
    log(f"Scanning {region['width']}x{region['height']} at ({region['left']},{region['top']})...")

    try:
        img = grab(region)
    except Exception as e:
        log(f"[ERROR] Capture failed: {e}")
        return None

    DEBUG_DIR.mkdir(exist_ok=True)
    stamp = int(time.time())
    raw_path = DEBUG_DIR / f"raw_{stamp}.png"
    cv2.imwrite(str(raw_path), img)

    ocr_data, parsed, tag = run_ocr(img)
    rows = parsed["rows"]
    layout = parsed["layout"]

    if ocr_data is not None:
        annotated = annotate(img, ocr_data)
        cv2.imwrite(str(DEBUG_DIR / f"ocr_{stamp}.png"), annotated)

    if not rows:
        log(f"[NO DATA] layout={layout} variant={tag}. See {DEBUG_DIR}")
        return None

    station = sc_parser.detect_station(ocr_data) if ocr_data is not None else "Unknown Station"

    scan = {
        "timestamp": time.time(),
        "station": station,
        "commodities": rows,
        "layout": layout,
        "source": "player-scan",
        "version": "1.1",
    }
    out = SCAN_DIR / f"scan_{stamp}.json"
    out.write_text(json.dumps(scan, indent=2))

    log(f"[OK] {len(rows)} commodities ({layout}) from {station} -> {out.name}")
    return scan


# ---- Calibration ----
def start_calibration():
    global calibrating, calib_points
    calibrating = True
    calib_points = []
    log("CALIBRATE: click TOP-LEFT of the commodity table")


def on_click(x, y, button, pressed):
    global calibrating, calib_points
    if not calibrating or not pressed:
        return
    calib_points.append((int(x), int(y)))
    if len(calib_points) == 1:
        log("CALIBRATE: now click BOTTOM-RIGHT of the commodity table")
    elif len(calib_points) == 2:
        (x1, y1), (x2, y2) = calib_points
        region = {
            "top": min(y1, y2),
            "left": min(x1, x2),
            "width": abs(x2 - x1),
            "height": abs(y2 - y1),
        }
        if region["width"] < 50 or region["height"] < 50:
            log("[WARN] Region too small. Try again (F8).")
        else:
            save_region(region)
            log(f"Region saved: {region['width']}x{region['height']} at ({region['left']},{region['top']})")
        calibrating = False
        calib_points = []


# ---- Hotkeys ----
def on_press(key):
    try:
        if key == keyboard.Key.f7:
            cmd_queue.put("toggle_preview")
        elif key == keyboard.Key.f8:
            cmd_queue.put("calibrate")
        elif key == keyboard.Key.f9:
            cmd_queue.put("scan")
        elif key == keyboard.Key.f10:
            cmd_queue.put("quit")
            return False
    except Exception as e:
        log(f"[ERROR] {e}")


# ---- Preview UI (tkinter, main thread) ----
def run_preview():
    import tkinter as tk
    from PIL import Image, ImageTk

    root = tk.Tk()
    root.title("SC Price Scanner")
    root.attributes("-topmost", True)
    root.configure(bg="#111111")
    root.geometry("+40+40")

    status_var = tk.StringVar(value=status_text)
    info_var = tk.StringVar(value="")

    tk.Label(
        root, text="SC Price Scanner", fg="#00ccff", bg="#111111",
        font=("Segoe UI", 12, "bold"),
    ).pack(padx=10, pady=(8, 0), anchor="w")

    img_label = tk.Label(root, bg="#111111")
    img_label.pack(padx=10, pady=6)

    tk.Label(
        root, textvariable=status_var, fg="#cccccc", bg="#111111",
        font=("Segoe UI", 9), wraplength=420, justify="left",
    ).pack(padx=10, anchor="w")

    tk.Label(
        root, textvariable=info_var, fg="#88ff88", bg="#111111",
        font=("Consolas", 9), justify="left", anchor="w",
    ).pack(padx=10, pady=(4, 8), anchor="w")

    visible = {"on": False}
    root.withdraw()

    def toggle():
        visible["on"] = not visible["on"]
        if visible["on"]:
            root.deiconify()
            root.lift()
            root.attributes("-topmost", True)
        else:
            root.withdraw()

    def refresh_preview():
        if visible["on"]:
            try:
                region = load_region()
                img = grab(region)
                # Scale down for display
                maxw = 420
                h, w = img.shape[:2]
                if w > maxw:
                    scale = maxw / w
                    img = cv2.resize(img, (maxw, int(h * scale)))
                rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                photo = ImageTk.PhotoImage(Image.fromarray(rgb))
                img_label.configure(image=photo)
                img_label.image = photo
                info_var.set(
                    f"region {region['width']}x{region['height']} @ "
                    f"({region['left']},{region['top']})"
                )
            except Exception as e:
                info_var.set(f"preview error: {e}")
        root.after(400, refresh_preview)

    def poll_commands():
        try:
            while True:
                cmd = cmd_queue.get_nowait()
                if cmd == "toggle_preview":
                    toggle()
                elif cmd == "calibrate":
                    start_calibration()
                elif cmd == "scan":
                    scan = do_scan()
                    if scan:
                        info_var.set(
                            f"{len(scan['commodities'])} items · "
                            f"{scan['layout']} · {scan['station']}"
                        )
                elif cmd == "quit":
                    log("Exiting...")
                    root.quit()
                    return
        except queue.Empty:
            pass
        status_var.set(status_text)
        root.after(120, poll_commands)

    root.after(200, refresh_preview)
    root.after(120, poll_commands)
    root.mainloop()

    # After UI closes, stop listeners
    os._exit(0)


def main():
    print("=" * 58)
    print("  SC Price Scanner v1.1")
    print("  Screen capture + OCR · no game injection · safe")
    print("=" * 58)
    print("  F7  = Live preview window")
    print("  F8  = Calibrate region (click 2 corners)")
    print("  F9  = Scan terminal")
    print("  F10 = Quit")
    print()
    print(f"  Scans -> {SCAN_DIR}")
    print(f"  Debug -> {DEBUG_DIR}")
    print()

    region = load_region()
    print(f"  Region: {region['width']}x{region['height']} at "
          f"({region['left']},{region['top']})")

    # Mouse listener for calibration clicks
    mouse_listener = mouse.Listener(on_click=on_click)
    mouse_listener.start()

    # Keyboard listener for hotkeys
    keyboard_listener = keyboard.Listener(on_press=on_press)
    keyboard_listener.start()

    try:
        run_preview()
    finally:
        keyboard_listener.stop()
        mouse_listener.stop()


if __name__ == "__main__":
    main()
