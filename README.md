# SC Price Scanner — Standalone Build & Distribution Guide

## Quick Start (for your users)

1. Download `SC-Price-Scanner.exe` from your releases
2. Run it — no install needed
3. Press **F8** once to calibrate (click corners of commodity terminal)
4. In-game: open a commodity kiosk → press **F9** to scan
5. JSON scans appear in `Documents\SC-Truckers Scans\` (opened via the overlay's **Open Scan Folder** button)
6. Drag any `.json` into your website's **Import Scan** button

---

## Build Instructions (for you)

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
cd sc_price_scanner
python build_scanner.py
```
Output: `dist/SC-Price-Scanner.exe` (~35-45 MB)

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
Edit `scanner.py`:
```python
# Line ~180
if key == keyboard.Key.f8:  # calibration
if key == keyboard.Key.f9:  # scan
if key == keyboard.Key.f10: # quit
```

### Add more stations
Edit `KNOWN_STATIONS` list in `scanner.py`

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
| Tesseract not found | Ensure `tessdata/` folder is next to exe (bundled by build script). |

---

## File Structure
```
sc_price_scanner/
├── scanner.py          # Main script
├── build_scanner.py    # Build script
├── tessdata/           # OCR language data (copied at build)
│   └── eng.traineddata
├── scanner.ico         # Optional icon
├── dist/
│   └── SC-Price-Scanner.exe
└── README.md
```

---

## Next Steps for Your Site

1. **Add Import component** (see previous message)
2. **Validate scan schema** on backend
3. **Show contributor credit**: "Updated by CMDR_John 3m ago via local scan"
4. **Leaderboard**: Top scanners this week
5. **Discord webhook**: Auto-post new high-margin routes