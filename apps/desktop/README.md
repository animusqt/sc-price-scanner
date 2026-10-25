# SCIP Desktop

The new desktop client is a React + TypeScript user interface packaged with
Tauri 2. It does not require a SCIP account or external sign-in. Fleet
profiles and theme preferences stay in this Windows user profile's local
browser storage.

## Run for development

1. Install Python dependencies from the repository's existing environment and
   start the local API in a separate PowerShell window:

   ```powershell
   .\scripts\start-local.ps1
   ```

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

## Not production-connected yet

- The SC-Truckers Lovable API endpoint and response/auth contract have not been
  supplied, so no live remote synchronization is enabled. The backend provider
  registry intentionally reports all external providers as disabled.
- The Windows installer does not yet bundle or start FastAPI; run the local API
  separately as shown above.
- Auto-update signing/feed configuration, backups, local database encryption,
  and native WebSocket updates still need implementation.
- Empty views are expected until market data is synchronized into the API.
