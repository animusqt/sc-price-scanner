# V2 Electron launcher

This is the Version 2 monorepo launcher package. The original
`electron-overlay/` directory is retained temporarily for existing running
instances and V1 compatibility. `Launch Overlay.bat`, CI packaging, and
subsequent launcher changes use this package.

Install and run with `npm ci` and `npm start`. Build distributables with
`npm run build`. The scanner engine remains in `scanner-engine/`.
