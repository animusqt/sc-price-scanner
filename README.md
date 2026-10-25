# SC Price Scanner

## Launch the overlay

Double-click **`Launch Overlay.bat`** in the project folder. It opens the overlay directly without leaving a terminal window open. On first launch, it installs the Electron dependencies in the background.

The scanner bridge must also be built before the overlay can scan. The launcher looks for it in `scanner-engine\dist\`. See [Build the scanner](#build-the-scanner) if it is not there.

Closing the overlay window hides it to the system tray; choose **Quit** from the tray menu to exit completely.

In the overlay, choose **Calibrate Region** and click the scan area's top-left corner. A highlighted box follows your cursor; click the bottom-right corner to save the region. Press **Esc** to cancel. Then open a commodity kiosk and choose **Scan Terminal**. Each station gets one JSON file under `Documents\SC-Truckers Scans\`; scanning it again updates that same file with new commodities and newer values, without duplicate commodity rows. If the station is shown as **Unknown Station**, set its name in the station field so it is saved to the correct station file.

For hands-free updates, choose **Start Low-Impact Live Scan**. It checks the calibrated region every 2 seconds and runs OCR only after a visual change remains stable; scans are limited to one every 15 seconds. Manual **F9** scanning remains available. OCR values need agreement across multiple passes, and unconfirmed market tabs are not used for modal prices. Review warnings and the files in **Open Scan Diagnostics** before submitting scans to a market site. The scanner creates local player-scan JSON; it does not automatically upload or verify submissions.

---

## Version 2 platform

The `version-2` branch adds a FastAPI market API, PostgreSQL/Redis services,
OCR service, and a responsive dashboard. It accepts manually reviewed player
scan JSON, keeps reports pending moderation, separates patch/shard data, and
only uses moderator-confirmed reports for route recommendations. See
[VERSION_2.md](VERSION_2.md) for implemented areas and limitations.

### Run locally

1. Install Docker Compose and copy `.env.example` to `.env`.
2. Set strong, unique URL-safe values for the database and Redis passwords,
   plus a random `JWT_SECRET` with at least 32 bytes.
3. For Discord sign-in and report submission, configure the Discord client,
   public key, bot, guild, and optional verified role. Set the local callback URL to
   `http://localhost:8080/api/v2/auth/discord/callback`.
4. Run `docker compose up --build` and open `http://localhost:8080`.

The dashboard can start without Discord credentials, but authenticated
submission is unavailable until Discord is configured. For public deployment,
set production secrets and `SITE_DOMAIN`, configure the public HTTPS callback
and web URL, then start with `docker compose --profile production up --build`.
The `gateway` profile provides Caddy TLS; do not expose the development profile
directly to the internet.

### Discord bot commands

The API also supports Discord's signed interactions endpoint. Create a bot
token in the Developer Portal and place it in the ignored `.env` as
`DISCORD_BOT_TOKEN`. Add the bot to your server with the
`applications.commands` scope and set `DISCORD_GUILD_ID`, then run
`python services/discord-bot/register_commands.py` to register the commands.
In the application's General Information page, set **Interactions Endpoint
URL** to `https://<your-public-domain>/api/v2/discord/interactions`. Discord
must reach that HTTPS URL; localhost alone will not work. The integration
provides `/ping`, `/market`, `/bug-report`, and moderator-only `/review-bug`.
See [the Discord bot setup guide](services/discord-bot/README.md) for details.
The OAuth redirect URI is a separate setting.

The API, OAuth integration, authorized external market providers, moderation
staff, and production environment must be operated/configured by the deployment
owner. Provider adapters remain disabled until their API contracts are
approved. WebSocket fan-out currently expects one API instance. Supporter
payment processing and role synchronization are not included.

### Version 2 checks

```powershell
python -m unittest discover -s tests -v
npm ci --prefix apps/launcher
npm run build --prefix apps/launcher -- --dir --config.win.signAndEditExecutable=false
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
| **Ban risk** | Near-zero. Uses Windows Desktop Duplication API (same as OBS/Discord). No memory reads, no hooks, no injection. |
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
│   ├── launcher/       # Version 2 Electron launcher source
│   ├── web/            # Market dashboard
│   └── ...
├── electron-overlay/   # Legacy overlay source; retained for compatibility
├── services/           # Version 2 API, OCR and web deployment services
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