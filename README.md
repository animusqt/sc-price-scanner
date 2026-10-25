# SCIP — Star Citizen Intelligence Platform

The primary V2 application is the React + Tauri desktop client documented in
[SCIP Version 2 desktop](#scip-version-2-desktop). The original OCR overlay is
legacy tooling; it is not required to launch or use SCIP.

## Legacy scanner overlay

Double-click **`Launch Overlay.bat`** in the project folder. It opens the overlay directly without leaving a terminal window open. On first launch, it installs the Electron dependencies in the background.

The scanner bridge must also be built before the overlay can scan. The launcher looks for it in `scanner-engine\dist\`. See [Build the scanner](#build-the-scanner) if it is not there.

Closing the overlay window hides it to the system tray; choose **Quit** from the tray menu to exit completely.

In the overlay, choose **Calibrate Region** and click the scan area's top-left corner. A highlighted box follows your cursor; click the bottom-right corner to save the region. Press **Esc** to cancel. Then open a commodity kiosk and choose **Scan Terminal**. Each station gets one JSON file under `Documents\SC-Truckers Scans\`; scanning it again updates that same file with new commodities and newer values, without duplicate commodity rows. If the station is shown as **Unknown Station**, set its name in the station field so it is saved to the correct station file.

For hands-free updates, choose **Start Low-Impact Live Scan**. It checks the calibrated region every 2 seconds and runs OCR only after a visual change remains stable; scans are limited to one every 15 seconds. Manual **F9** scanning remains available. OCR values need agreement across multiple passes, and unconfirmed market tabs are not used for modal prices. Review warnings and the files in **Open Scan Diagnostics** before submitting scans to a market site. The scanner creates local player-scan JSON; it does not automatically upload or verify submissions.

---

## SCIP Version 2 desktop

The `apps/desktop` directory contains the React + TypeScript desktop client
packaged with Tauri 2 and configured to build a per-user Windows NSIS installer.
It opens directly without an account. The interface includes Dashboard,
Commodities, Ships, Routes, Fleet, OCR Upload, Analytics, Replay, Data Import,
and Settings. Catalog and ship data (including viewed ship images) are cached
locally for offline browsing; fleet profiles are stored in the local API
database. The OCR uploader
captures a screen with F9 while SCIP is focused, supports screenshot files,
validates editable OCR results, and queues failed website submissions locally.
Support links appear inside Settings.

### Run the desktop client

Start the local API and OCR service in one PowerShell window:

```powershell
pip install -r services\api\requirements.txt
pip install -r services\ocr\requirements.txt
.\scripts\start-local.ps1
```

Install Tesseract OCR (for example, `winget install UB-Mannheim.TesseractOCR`)
and add it to `PATH`, or set `$env:TESSERACT_CMD` to the executable path before
starting the services. To enable website submissions, add
`SC_TRUCKERS_API_BASE_URL` and the optional `SC_TRUCKERS_API_TOKEN` to `.env`.

### Catalog data and imports

The API stores commodities, locations, timestamped market prices, ships,
components, ship-component links, and fleet profiles in the SQLAlchemy
database. Apply migrations with
`alembic -c services\api\alembic.ini upgrade head`. The desktop's **Data
Import** page accepts normalized JSON or CSV files and shows import history.
Imports validate and upsert catalog records; market
price rows are kept as timestamped snapshots, and live browser/route results
ignore snapshots older than seven days.

JSON imports use a top-level object with one or more arrays named
`commodities`, `locations`, `ships`, `components`, `prices`, and
`ship_components`. CSV imports select one of those record types and use the
corresponding JSON field names as column headers. The Data Import page contains
a sample payload.

Optional API importers are prepared for SC Trade Tools, UEX, Hauler, Erkul,
Star Citizen Wiki, and Lovable. Configure each `SCIP_IMPORT_<SOURCE>_URL` in
the API server environment (and an optional matching `_TOKEN`). Lovable also
requires `SCIP_IMPORT_LOVABLE_ALLOWED_HOST`. URLs must use HTTPS and pass the
source host allowlist. These adapters intentionally accept SCIP's normalized
JSON envelope; proprietary provider response formats are not guessed or
silently mapped. A provider-specific mapping can be added once its API
documentation and response contract are available.

Install Rust with the Windows MSVC toolchain and WebView2, then run the Tauri
desktop shell in another PowerShell window:

```powershell
npm ci --prefix apps\desktop
npm run tauri --prefix apps\desktop -- dev
```

The UI API defaults to `http://127.0.0.1:8080`. Create a production frontend
build with `npm run build --prefix apps\desktop`. Build the NSIS installer with
`npm run tauri --prefix apps\desktop -- build`.

The desktop shell is an implementation foundation, not yet a self-contained
production installer: FastAPI and the OCR service must currently be started
separately. OCR forwarding is implemented, but website uploads need
`SC_TRUCKERS_API_BASE_URL` (and, if required by the website,
`SC_TRUCKERS_API_TOKEN`) configured on the API server. The website must accept
the documented report payload and return JSON confirmation; this has not yet
been verified against a live Lovable endpoint. Market read synchronization
from a proprietary Lovable response remains unavailable until its API contract
is supplied; normalized market snapshots can be imported through the catalog
importer in the meantime. Without synced/imported market records, the dashboard
and route planner show empty-state guidance. See
[apps/desktop/README.md](apps/desktop/README.md) and [VERSION_2.md](VERSION_2.md)
for current scope and limitations.

### Checks

```powershell
python -m pytest -q
npm ci --prefix apps\desktop
npm run build --prefix apps\desktop
```

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
Close all running scanner overlays before rebuilding; Windows locks the bridge executable while it is running.

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
| **Ban risk** | Near-zero. Uses Windows Desktop Duplication API (same technology as OBS). No memory reads, no hooks, no injection. |
| **Admin rights** | Not required. Runs as standard user. |
| **Antivirus** | May flag unsigned exe. Solution: code-signing cert (~$200/yr) or user adds exception. |
| **Performance** | OCR runs only for manual scans or stable live-mode changes. Live mode samples the calibrated region every 2 seconds and limits OCR scans to one per 15 seconds. |

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
├── apps/
│   ├── desktop/        # React + TypeScript / Tauri desktop app
│   ├── launcher/       # Legacy Electron overlay launcher source
│   └── ...
├── electron-overlay/   # Legacy overlay source; retained for compatibility
├── services/           # Version 2 API and OCR services
├── scanner-engine/     # Python scanner, bridge, and build files
└── README.md
```
