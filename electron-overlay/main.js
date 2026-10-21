const { app, BrowserWindow, ipcMain, screen, nativeImage, Tray, Menu } = require('electron');
const path = require('path');
const { spawn } = require('child_process');

let overlayWindow = null;
let tray = null;
let scannerProcess = null;
const isDev = process.argv.includes('--dev');

// Scanner executable name per platform
const SCANNER_EXE = {
  win32: 'SC-Price-Scanner.exe',
  darwin: 'SC-Price-Scanner-mac',
  linux: 'SC-Price-Scanner-linux'
}[process.platform];

function createOverlayWindow() {
  const { width, height } = screen.getPrimaryDisplay().workAreaSize;
  
  overlayWindow = new BrowserWindow({
    width: 340,
    height: 480,
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
  
  // Dev tools in dev mode
  if (isDev) overlayWindow.webContents.openDevTools({ mode: 'detach' });

  // Prevent window from being closed, just hide
  overlayWindow.on('close', (e) => {
    if (!app.isQuitting) {
      e.preventDefault();
      overlayWindow.hide();
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
  if (scannerProcess && !scannerProcess.killed) {
    scannerProcess.stdin.write(JSON.stringify({ action }) + '\n');
  }
}

function launchScanner() {
  const scannerPath = path.join(process.resourcesPath || __dirname, '..', 'scanner', SCANNER_EXE);
  
  try {
    scannerProcess = spawn(scannerPath, [], {
      stdio: ['pipe', 'pipe', 'pipe'],
      detached: false
    });
    
    scannerProcess.stdout.on('data', (data) => {
      const lines = data.toString().trim().split('\n');
      lines.forEach(line => {
        if (line.startsWith('{')) {
          try {
            const msg = JSON.parse(line);
            overlayWindow?.webContents.send('scanner-output', msg);
          } catch {}
        } else if (line) {
          overlayWindow?.webContents.send('scanner-log', line);
        }
      });
    });
    
    scannerProcess.stderr.on('data', (data) => {
      overlayWindow?.webContents.send('scanner-log', `ERR: ${data}`);
    });
    
    scannerProcess.on('exit', (code) => {
      overlayWindow?.webContents.send('scanner-exit', code);
    });
    
    console.log(`Scanner launched: ${scannerPath}`);
  } catch (err) {
    console.error('Failed to launch scanner:', err);
    overlayWindow?.webContents.send('scanner-log', `Failed to launch scanner: ${err.message}`);
  }
}

app.whenReady().then(() => {
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
ipcMain.on('overlay-minimize', () => overlayWindow?.hide());
ipcMain.on('overlay-close', () => { app.isQuitting = true; app.quit(); });
ipcMain.on('trigger-scan', () => sendToScanner('scan'));
ipcMain.on('trigger-calibrate', () => sendToScanner('calibrate'));
ipcMain.on('set-click-through', (_, enabled) => {
  if (overlayWindow) {
    overlayWindow.setIgnoreMouseEvents(enabled, { forward: true });
  }
});