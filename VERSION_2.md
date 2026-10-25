# Version 2 status

SCIP V2 is transitioning from the original player-report dashboard to the
account-free desktop intelligence client described in the product brief.
The current React + TypeScript UI is packaged by Tauri 2 and uses the existing
FastAPI REST API for market and route reads.

## Implemented

| Area | Current state |
|---|---|
| Desktop shell | Tauri 2 project, React + TypeScript interface, dark Mobiglas/terminal theme, and per-user NSIS installer target. |
| Navigation | Dashboard, Commodities, Routes, Fleet, Analytics, Replay, and Settings, with Support SCIP contained in Settings. |
| Account-free use | The desktop UI has no sign-in flow. Fleet profiles and appearance preferences are stored on the local device. |
| Market display | Reads the existing API's patch/shard-separated market endpoint and shows price, stock, demand, observation freshness, and confidence. |
| Route analysis | Sends anonymous route requests to the existing API and displays profit, reliability, risk, and time-efficiency rankings. |
| Replay | Requests API history and renders buy/sell values as a basic timeline when history is available. |
| Local service | Windows PowerShell startup script runs FastAPI with SQLite; narrow CORS origins support the Vite/Tauri desktop shell. |
| Existing backend | SQLAlchemy/Alembic, PostgreSQL/Redis deployment configuration, market report records, route scoring, history and forecast primitives, and API tests are retained. |

## Not production-ready

- **Lovable synchronization is not connected.** No API URL, authorization method,
  endpoint contract, or sample response was supplied. Existing external source
  adapters are deliberately disabled; no endpoint behavior has been guessed.
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
