# Version 2 status

SCIP V2 is transitioning from the original player-report dashboard to the
account-free desktop intelligence client described in the product brief.
The current React + TypeScript UI is packaged by Tauri 2 and uses the existing
FastAPI REST API for market and route reads.

## Implemented

| Area | Current state |
|---|---|
| Desktop shell | Tauri 2 project, React + TypeScript interface, dark Mobiglas/terminal theme, and per-user NSIS installer target. |
| Navigation | Dashboard, Commodities, OCR Upload, Routes, Fleet, Analytics, Replay, and Settings, with Support SCIP contained in Settings. |
| Account-free use | The desktop UI has no sign-in flow. Fleet profiles and appearance preferences are stored on the local device. |
| Market display | Reads the existing API's patch/shard-separated market endpoint and shows price, stock, demand, observation freshness, and confidence. |
| Route analysis | Sends anonymous route requests to the existing API and displays profit, reliability, risk, and time-efficiency rankings. |
| Replay | Requests API history and renders buy/sell values as a basic timeline when history is available. |
| OCR uploader | F9 screen capture while the app is focused, screenshot-file OCR, editable extracted rows, local validation, optional confidence-gated auto upload, and a browser-local retry queue. |
| OCR submission API | Local API proxies screenshots to the OCR service and forwards validated `ReportCreate` payloads to the configured website `POST /api/ocr-submit`; failed posts remain queued on the desktop. |
| Local service | Windows PowerShell startup script runs FastAPI with SQLite; narrow CORS origins support the Vite/Tauri desktop shell. |
| Existing backend | SQLAlchemy/Alembic, PostgreSQL/Redis deployment configuration, market report records, route scoring, history and forecast primitives, and API tests are retained. |

## Not production-ready

- **Live Lovable connections still require configuration and verification.**
  OCR submission forwards to `/api/ocr-submit` when
  `SC_TRUCKERS_API_BASE_URL` is configured, with optional server-side Bearer
  token via `SC_TRUCKERS_API_TOKEN`. The forwarded body uses the existing
  validated `ReportCreate` schema and expects JSON confirmation, but this
  website contract has not been verified against a live endpoint. Market read
  synchronization still has no API URL, authorization method, or response
  schema; external market adapters remain disabled.
- **Local OCR requires Tesseract.** The local launcher starts the OCR service,
  but Tesseract must be installed and available on `PATH` or set with
  `TESSERACT_CMD`. F9 capture is available only while SCIP is focused, and the
  webview's screen-sharing support has not been verified in a native installer.
- **The app is not a self-contained installer yet.** Tauri's NSIS packaging is
  configured, but it does not bundle/start FastAPI. The local service currently
  must be started separately. Rust/MSVC/WebView2 are required to produce the
  Windows installer.
- Discord OAuth, guild/role verification, interaction handling, bot command
  registration, and stored Discord user IDs have been removed. A database
  migration retires the old interaction table and identity column; the
  migration is intentionally irreversible because deleted identities cannot
  be reconstructed.
- The old protected player-report and moderator-review APIs remain as
  transition code, but the desktop has no account flow and does not call them.
  Replace these with the documented Lovable synchronization contract before
  deploying a complete account-free backend.
- Auto-updater feed/signing, local database encryption, backup/restore UI,
  WebSocket client updates, risk-event feed integration, and production
  forecasting validation are not implemented.
- The desktop's route ranking views are alternate presentations of the
  existing route response, not four independent optimization strategies.
- Data views are empty until data is synchronized to the API. The attached
  local scanner JSON is not imported or treated as verified market data.

## Development checks

```powershell
python -m pytest -q
npm ci --prefix apps\desktop
npm run build --prefix apps\desktop
```

With Rust and Windows desktop prerequisites installed, build the Windows
installer using:

```powershell
npm run tauri --prefix apps\desktop -- build
```

See [apps/desktop/README.md](apps/desktop/README.md) for local run instructions
and current limitations.
