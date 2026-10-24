# SC Price Scanner

## Launch the overlay

Double-click **`Launch Overlay.bat`** in the project folder. It opens the overlay directly without leaving a terminal window open. On first launch, it installs the Electron dependencies in the background.

The scanner bridge must also be built before the overlay can scan. The launcher looks for it in `scanner-engine\dist\`. See [Build the scanner](#build-the-scanner) if it is not there.

Closing the overlay window hides it to the system tray; choose **Quit** from the tray menu to exit completely.

In the overlay, choose **Calibrate Region** and click the scan area's top-left corner. A highlighted box follows your cursor; click the bottom-right corner to save the region. Press **Esc** to cancel. Then open a commodity kiosk and choose **Scan Terminal**. Scan files are saved under `Documents\SC-Truckers Scans\`.

---

## Build the scanner

### Prerequisites
```bash
# Windows
winget install Python.Python.3.11
pip install pyinstaller mss opencv-python pytesseract pillow pynput numpy

# Tesseract OCR (required for build)
winget install UB-Mannheim.TesseractOCR
```

### Build
```bash
python scanner-engine\build_scanner.py
```
Output: `scanner-engine\dist\SC-Price-Scanner-Bridge.exe` and `SC-Price-Scanner.exe` (~35-45 MB each).

### What's Bundled
- Python interpreter + all deps
- Tesseract OCR engine + English traineddata
- No system dependencies required

---

## Distribution

### Option 1: GitHub Releases (recommended)
1. Create repo: `sc-price-scanner`
2. Add `SC-Price-Scanner.exe` to releases
3. Users download directly

### Option 2: Your Website
```tsx
// Add to your Market page
<a href="/downloads/SC-Price-Scanner.exe" className="btn-primary">
  Download Scanner (Windows, 40 MB)
</a>
```

### Option 3: Auto-updater (later)
Add `electron-updater` style checks or simple version JSON on your CDN.

---

## Safety Notes for Users

| Concern | Reality |
|---------|---------|
| **Ban risk** | Near-zero. Uses Windows Desktop Duplication API (same as OBS/Discord). No memory reads, no hooks, no injection. |
| **Admin rights** | Not required. Runs as standard user. |
| **Antivirus** | May flag unsigned exe. Solution: code-signing cert (~$200/yr) or user adds exception. |
| **Performance** | ~200ms per scan. Zero impact when idle. |

---

## Customization

### Change hotkeys
Edit `scanner-engine/scanner.py`:
```python
# Line ~180
if key == keyboard.Key.f8:  # calibration
if key == keyboard.Key.f9:  # scan
if key == keyboard.Key.f10: # quit
```

### Add more stations
Edit `KNOWN_STATIONS` list in `scanner-engine/scanner.py`

### Adjust OCR sensitivity
```python
# Line ~95 - confidence threshold
if c > 30:  # lower = more permissive, higher = stricter
```

### Change output folder
```python
# Lines ~34-45
SCAN_DIR = Path.home() / "OneDrive" / "Documents" / "SC-Truckers Scans"
# Falls back to Path.home() / "Documents" / "SC-Truckers Scans"
```

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| "No commodities detected" | Re-calibrate (F8). Ensure terminal fills capture region. Good lighting helps. |
| Wrong station name | Station detection uses OCR on top UI. Add station to `KNOWN_STATIONS`. |
| Numbers wrong (1,234 → 1234) | Already handled. Check `parse_num` function. |
| Slow scan | Reduce capture region size. Disable `--noconsole` to see timing. |
| Tesseract not found | Ensure `scanner-engine/tessdata/` is available to the build script. |

---

## File Structure
```
sc_price_scanner/
├── Launch Overlay.bat  # One-click launcher
├── electron-overlay/   # Desktop overlay application
├── scanner-engine/     # Python scanner, bridge, and build files
└── README.md
```

---

## Next Steps for Your Site

1. **Add Import component** (see previous message)
2. **Validate scan schema** on backend
3. **Show contributor credit**: "Updated by CMDR_John 3m ago via local scan"
4. **Leaderboard**: Top scanners this week
5. **Discord webhook**: Auto-post new high-margin routes