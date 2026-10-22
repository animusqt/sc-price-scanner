const { app, BrowserWindow, ipcMain, screen, nativeImage, Tray, Menu, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');

let overlayWindow = null;
let tray = null;
let scannerProcess = null;
let closeToTray = true;
let expandedHeight = 620;
let isCollapsed = false;
let isLocked = false;
const isDev = process.argv.includes('--dev');

// Buffer scanner output until the renderer has registered its listeners,
// otherwise the Bridge's startup lines are emitted before the window is ready.
let rendererReady = false;
const pendingMessages = [];

function toRenderer(channel, payload) {
  try {
    const text = typeof payload === 'string' ? payload : JSON.stringify(payload);
    fs.appendFileSync(
      path.join(scansFolder(), 'overlay.log'),
      `${new Date().toISOString()} ${channel} ${text}\n`
    );
  } catch {}
  if (rendererReady && overlayWindow && !overlayWindow.isDestroyed()) {
    overlayWindow.webContents.send(channel, payload);
    return;
  }
  pendingMessages.push([channel, payload]);
  if (pendingMessages.length > 500) pendingMessages.shift();
}

function flushPending() {
  if (!overlayWindow) return;
  while (pendingMessages.length) {
    const [channel, payload] = pendingMessages.shift();
    overlayWindow.webContents.send(channel, payload);
  }
}

// The Bridge build speaks the JSON stdin/stdout protocol. Prefer it; fall back
// to the CLI build (which just responds to hotkeys) if that's all we have.
const SCANNER_CANDIDATES = {
  win32: ['SC-Price-Scanner-Bridge.exe', 'SC-Price-Scanner.exe'],
  darwin: ['SC-Price-Scanner-Bridge-mac', 'SC-Price-Scanner-mac'],
  linux: ['SC-Price-Scanner-Bridge-linux', 'SC-Price-Scanner-linux'],
}[process.platform] || [];

function resolveScannerPath() {
  const roots = [
    process.resourcesPath ? path.join(process.resourcesPath, 'scanner') : null,
    process.resourcesPath || null,
    path.join(__dirname, '..', 'dist'),
    path.join(__dirname, '..', 'scanner'),
    path.join(app.getAppPath(), 'scanner'),
    path.join(app.getAppPath(), '..', 'dist'),
    __dirname,
  ].filter(Boolean);

  for (const root of roots) {
    for (const name of SCANNER_CANDIDATES) {
      const candidate = path.join(root, name);
      try {
        if (fs.existsSync(candidate)) return candidate;
      } catch (_) {}
    }
  }
  return null;
}

function createOverlayWindow() {
  const { width, height } = screen.getPrimaryDisplay().workAreaSize;
  
  overlayWindow = new BrowserWindow({
    width: 340,
    height: 620,
    x: width - 360,
    y: 100,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: true,
    minimizable: false,
    maximizable: false,
    focusable: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
    },
    // Rounded corners on Windows 11
    ...(process.platform === 'win32' ? { roundedCorners: true } : {})
  });

  overlayWindow.loadFile(path.join(__dirname, 'overlay.html'));

  overlayWindow.webContents.on('preload-error', (_e, preloadPath, error) => {
    toRenderer('scanner-log', `Preload error (${preloadPath}): ${error && error.message}`);
  });

  // Optional UI self-test: SCANNER_SELFTEST=1 exercises settings/collapse/theme
  if (process.env.SCANNER_SELFTEST === '1') {
    overlayWindow.webContents.on('did-finish-load', async () => {
      try {
        const result = await overlayWindow.webContents.executeJavaScript(`(async () => {
          await new Promise((r) => setTimeout(r, 600));
          const $ = (id) => document.getElementById(id);
          const out = {};
          out.theme = document.documentElement.dataset.theme;
          out.panelHidden = $('settingsPanel').hidden;
          $('settingsBtn').click();
          out.panelOpened = !$('settingsPanel').hidden;
          document.querySelector('[data-theme-choice="morning"]').click();
          out.themeAfter = document.documentElement.dataset.theme;
          out.bgAfter = getComputedStyle($('app')).backgroundColor;
          const A = window.scannerAPI;
          const before = await A.getBounds();
          A.resizeWindow(480, 520);
          await new Promise((r) => setTimeout(r, 250));
          const after = await A.getBounds();
          out.resized = !!after && after.width === 480 && after.height === 520;
          A.resizeWindow(before.width, before.height);
          await new Promise((r) => setTimeout(r, 150));
          $('minimizeBtn').click();
          out.collapsed = document.body.classList.contains('collapsed');
          $('minimizeBtn').click();
          out.expanded = !document.body.classList.contains('collapsed');
          $('lockWindow').click();
          out.locked = document.body.classList.contains('locked');
          $('lockWindow').click();
          out.unlocked = !document.body.classList.contains('locked');
          $('closeSettings').click();
          out.panelClosed = $('settingsPanel').hidden;
          document.querySelector('[data-theme-choice="' + out.theme + '"]').click();
          out.themeRestored = document.documentElement.dataset.theme;
          return JSON.stringify(out);
        })()`);
        toRenderer('scanner-log', 'selftest ' + result);
      } catch (e) {
        toRenderer('scanner-log', 'selftest failed: ' + e.message);
      }
    });
  }

  // Exclude the overlay from screen capture so it can never occlude the OCR
  // region (Windows 10 2004+ / macOS). It stays visible on screen.
  try { overlayWindow.setContentProtection(true); } catch {}

  // Stay above fullscreen games (Windows/macOS compositor levels)
  overlayWindow.setAlwaysOnTop(true, 'screen-saver');
  if (overlayWindow.setVisibleOnAllWorkspaces) {
    overlayWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  }
  
  // Dev tools in dev mode
  if (isDev) overlayWindow.webContents.openDevTools({ mode: 'detach' });

  // Close honours the "minimize to tray" setting
  overlayWindow.on('close', (e) => {
    if (app.isQuitting) return;
    e.preventDefault();
    if (closeToTray) {
      overlayWindow.hide();
    } else {
      app.isQuitting = true;
      app.quit();
    }
  });

  return overlayWindow;
}

function createTray() {
  const iconPath = path.join(__dirname, 'icon.png');
  const icon = nativeImage.createFromPath(iconPath).resize({ width: 16, height: 16 });
  
  tray = new Tray(icon);
  
  const contextMenu = Menu.buildFromTemplate([
    { label: 'Show Overlay', click: () => overlayWindow?.show() },
    { label: 'Hide Overlay', click: () => overlayWindow?.hide() },
    { type: 'separator' },
    { label: 'Calibrate Scanner (F8)', click: () => sendToScanner('calibrate') },
    { label: 'Scan Now (F9)', click: () => sendToScanner('scan') },
    { type: 'separator' },
    { label: 'Quit', click: () => { app.isQuitting = true; app.quit(); } }
  ]);
  
  tray.setToolTip('SC Price Scanner Overlay');
  tray.setContextMenu(contextMenu);
  
  tray.on('double-click', () => {
    overlayWindow?.isVisible() ? overlayWindow.hide() : overlayWindow?.show();
  });
}

function sendToScanner(action) {
  if (scannerProcess && !scannerProcess.killed && scannerProcess.stdin.writable) {
    scannerProcess.stdin.write(JSON.stringify({ action }) + '\n');
  }
}

function launchScanner() {
  const scannerPath = resolveScannerPath();

  if (!scannerPath) {
    const msg = `Scanner not found. Build it first (python build_scanner.py) or place the exe in resources/scanner/.`;
    console.error(msg);
    toRenderer('scanner-log', msg);
    return;
  }

  try {
    scannerProcess = spawn(scannerPath, [], {
      stdio: ['pipe', 'pipe', 'pipe'],
      detached: false,
      windowsHide: true
    });

    scannerProcess.on('error', (err) => {
      toRenderer('scanner-log', `Failed to launch scanner: ${err.message}`);
    });

    scannerProcess.stdout.on('data', (data) => {
      const lines = data.toString().trim().split(/\r?\n/);
      lines.forEach(line => {
        if (!line) return;
        if (line.startsWith('{')) {
          try {
            const msg = JSON.parse(line);
            toRenderer('scanner-output', msg);
          } catch {}
        } else {
          toRenderer('scanner-log', line);
        }
      });
    });

    scannerProcess.stderr.on('data', (data) => {
      toRenderer('scanner-log', `ERR: ${data}`);
    });

    scannerProcess.on('exit', (code) => {
      toRenderer('scanner-exit', code);
    });

    console.log(`Scanner launched: ${scannerPath}`);
  } catch (err) {
    console.error('Failed to launch scanner:', err);
    toRenderer('scanner-log', `Failed to launch scanner: ${err.message}`);
  }
}

app.whenReady().then(() => {
  try {
    fs.mkdirSync(scansFolder(), { recursive: true });
    fs.writeFileSync(path.join(scansFolder(), 'overlay.log'), '');
  } catch {}
  createOverlayWindow();
  createTray();
  launchScanner();
  
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createOverlayWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  app.isQuitting = true;
  scannerProcess?.kill();
});

// IPC handlers
ipcMain.on('renderer-ready', () => {
  rendererReady = true;
  flushPending();
});
setTimeout(() => {
  if (!rendererReady) {
    rendererReady = true;
    flushPending();
  }
}, 4000);

ipcMain.on('overlay-minimize', () => overlayWindow?.hide());
ipcMain.on('overlay-close', () => {
  if (closeToTray) {
    overlayWindow?.hide();
    return;
  }
  app.isQuitting = true;
  app.quit();
});
ipcMain.on('set-close-to-tray', (_, enabled) => { closeToTray = !!enabled; });
ipcMain.on('set-locked', (_, locked) => {
  isLocked = !!locked;
  if (overlayWindow) overlayWindow.setMovable(!isLocked);
  if (overlayWindow) overlayWindow.setResizable(!isLocked && !isCollapsed);
});
ipcMain.on('set-collapsed', (_, collapsedState) => {
  if (!overlayWindow) return;
  isCollapsed = !!collapsedState;
  const b = overlayWindow.getBounds();
  if (isCollapsed) {
    if (b.height > 60) expandedHeight = b.height;
    overlayWindow.setResizable(false);
    overlayWindow.setBounds({ x: b.x, y: b.y, width: b.width, height: 38 });
  } else {
    overlayWindow.setResizable(!isLocked);
    overlayWindow.setBounds({ x: b.x, y: b.y, width: b.width, height: expandedHeight });
  }
});
ipcMain.on('trigger-scan', () => sendToScanner('scan'));
ipcMain.on('trigger-calibrate', () => sendToScanner('calibrate'));
ipcMain.on('set-click-through', (_, enabled) => {
  if (overlayWindow) {
    overlayWindow.setIgnoreMouseEvents(enabled, { forward: true });
  }
});

function scansFolder() {
  return path.join(app.getPath('documents'), 'SC-Truckers Scans');
}

ipcMain.handle('get-bounds', () => (overlayWindow ? overlayWindow.getBounds() : null));
ipcMain.on('resize-window', (_, size) => {
  if (!overlayWindow || isCollapsed) return;
  const b = overlayWindow.getBounds();
  const width = Math.max(260, Math.round(size.width));
  const height = Math.max(300, Math.round(size.height));
  overlayWindow.setBounds({ x: b.x, y: b.y, width, height });
  expandedHeight = height;
});
ipcMain.handle('get-scans-folder', () => scansFolder());
ipcMain.on('open-site', () => {
  shell.openExternal('https://sc-truckers.lovable.app/market');
});
ipcMain.on('open-scans-folder', async () => {
  const dir = scansFolder();
  try { fs.mkdirSync(dir, { recursive: true }); } catch {}
  const err = await shell.openPath(dir);
  if (err) toRenderer('scanner-log', `Could not open ${dir}: ${err}`);
});