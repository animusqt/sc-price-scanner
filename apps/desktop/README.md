# SCIP Desktop

The new desktop client is a React + TypeScript user interface packaged with
Tauri 2. It does not require a SCIP account or external sign-in. Fleet
profiles and theme preferences stay in this Windows user profile's local
browser storage.

## Run for development

1. Install Python dependencies from the repository's existing environment and
   start the local API and OCR service in a separate PowerShell window:

   ```powershell
   pip install -r services\api\requirements.txt
   pip install -r services\ocr\requirements.txt
   .\scripts\start-local.ps1
   ```

   Install Tesseract OCR (for example, `winget install UB-Mannheim.TesseractOCR`)
   and ensure `tesseract.exe` is on `PATH`, or set
   `$env:TESSERACT_CMD` to its full path before starting the services.
   For website submission, set `SC_TRUCKERS_API_BASE_URL` and the optional
   `SC_TRUCKERS_API_TOKEN` in the repository `.env` file; the local launcher
   reads only these OCR-related values and `TESSERACT_CMD`.

2. Install Rust with the MSVC toolchain and WebView2, then install the UI
   packages and launch the Tauri development shell:

   ```powershell
   npm ci --prefix apps\desktop
   npm run tauri --prefix apps\desktop -- dev
   ```

The desktop client calls `http://127.0.0.1:8080`. Override this for a different
API with `VITE_SCIP_API_URL` at build time. The local API allows requests only
from the Vite and Tauri desktop origins.

## Build the Windows installer

With Rust, the Windows C++ build tools, and WebView2 installed:

```powershell
npm run tauri --prefix apps\desktop -- build
```

The Tauri configuration targets a per-user NSIS installer. The Rust toolchain
is required for the native Windows shell; the frontend can be type-checked and
production-built without it by running `npm run build --prefix apps\desktop`.

## Implemented desktop areas

- Dashboard and commodity table, separated by patch and shard
- Market source status and data confidence display
- Anonymous route requests to the existing route API
- Local fleet profiles, favorites, and notes
- Market-history replay chart when API history exists
- Appearance settings and support links nested inside Settings
- Restricted local API origins and no account sign-in UI
- OCR Upload page: F9 screen capture while SCIP is focused, screenshot-file OCR,
  editable value review, confidence-gated automatic upload, and a persistent
  local retry queue
- The local API proxies screenshots to the existing OCR service and forwards
  validated reports to the configured SC-Truckers `POST /api/ocr-submit`
  endpoint; the optional Bearer token stays on the API server

The forwarded JSON follows this report shape (the website must accept the
contract and return a JSON confirmation):

```json
{
  "client_report_id": "a-stable-unique-id",
  "location": "Area18",
  "patch_version": "live",
  "server_shard": "global",
  "observed_at": "2026-10-25T12:00:00Z",
  "ocr_confidence": 0.9,
  "source": "player-scan",
  "commodities": [
    { "commodity": "Silicon", "buy": 500, "sell": null, "stock": 100, "demand": null }
  ],
  "inventory": []
}
```

## Not production-connected yet

- Live OCR upload requires `SC_TRUCKERS_API_BASE_URL` on the API server. The
  server appends `/api/ocr-submit` and can send `SC_TRUCKERS_API_TOKEN` as a
  Bearer token. The SC-Truckers site must accept the documented
  `ReportCreate` JSON fields and return a JSON confirmation; no website schema
  or authorization behavior has been verified against a live endpoint.
- Market synchronization remains disabled until its separate Lovable read API
  contract is supplied. The backend provider registry reports external market
  feeds as disabled.
- OCR processing requires Tesseract installed locally. The F9 shortcut works
  only while SCIP has focus; screen-sharing prompts are provided by the
  platform webview. Screenshot images are processed locally and are not stored
  in the upload queue; the queue stores validated report fields in browser
  local storage.
- The Windows installer does not yet bundle or start FastAPI; run the local API
  separately as shown above.
- Auto-update signing/feed configuration, backups, local database encryption,
  and native WebSocket updates still need implementation.
- Empty views are expected until market data is synchronized into the API.
