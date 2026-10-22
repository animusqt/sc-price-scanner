const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('scannerAPI', {
  onScannerOutput: (callback) => ipcRenderer.on('scanner-output', (_, data) => callback(data)),
  onScannerLog: (callback) => ipcRenderer.on('scanner-log', (_, data) => callback(data)),
  onScannerExit: (callback) => ipcRenderer.on('scanner-exit', (_, code) => callback(code)),
  
  triggerScan: () => ipcRenderer.send('trigger-scan'),
  triggerCalibrate: () => ipcRenderer.send('trigger-calibrate'),
  openScansFolder: () => ipcRenderer.send('open-scans-folder'),
  getScansFolder: () => ipcRenderer.invoke('get-scans-folder'),
  rendererReady: () => ipcRenderer.send('renderer-ready'),
  minimizeOverlay: () => ipcRenderer.send('overlay-minimize'),
  closeOverlay: () => ipcRenderer.send('overlay-close'),
  setClickThrough: (enabled) => ipcRenderer.send('set-click-through', enabled),
  
  // Remove listeners
  removeAllListeners: (channel) => ipcRenderer.removeAllListeners(channel)
});