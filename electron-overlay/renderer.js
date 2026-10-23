// Renderer process for SC Price Scanner Overlay

const api = window.scannerAPI;

let clickThrough = false;
let lastScanData = null;

// DOM elements
const elements = {
  scannerStatus: document.getElementById('scannerStatus'),
  lastScan: document.getElementById('lastScan'),
  commodityCount: document.getElementById('commodityCount'),
  stationName: document.getElementById('stationName'),
  scanBtn: document.getElementById('scanBtn'),
  calibrateBtn: document.getElementById('calibrateBtn'),
  clickThroughBtn: document.getElementById('clickThroughBtn'),
  minimizeBtn: document.getElementById('minimizeBtn'),
  closeBtn: document.getElementById('closeBtn'),
  previewCard: document.getElementById('previewCard'),
  previewTable: document.getElementById('previewTable'),
  openScanFolder: document.getElementById('openScanFolder'),
  uploadToSite: document.getElementById('uploadToSite'),
  logConsole: document.getElementById('logConsole'),
  titleBar: document.getElementById('titleBar'),
  resizeHandle: document.getElementById('resizeHandle'),
  settingsBtn: document.getElementById('settingsBtn'),
  settingsPanel: document.getElementById('settingsPanel'),
  closeSettings: document.getElementById('closeSettings'),
  settingsDone: document.getElementById('settingsDone'),
  settingsOpenFolder: document.getElementById('settingsOpenFolder'),
  lockWindow: document.getElementById('lockWindow'),
  minToTray: document.getElementById('minToTray'),
  stationInput: document.getElementById('stationInput'),
  themeChips: Array.from(document.querySelectorAll('[data-theme-choice]'))
};

// Initialize
document.addEventListener('DOMContentLoaded', init);

function init() {
  initSettings();
  setupEventListeners();
  setupScannerListeners();
  log('info', 'Overlay initialized');
  log('info', `Platform: ${navigator.platform}`);
  checkScannerConnection();
  if (api.rendererReady) api.rendererReady();
}

/* ------------------------------------------------------------------
   Settings
   ------------------------------------------------------------------ */
const SETTINGS_KEY = 'scanner.settings';
const DEFAULT_SETTINGS = { theme: null, locked: false, minToTray: true, station: '' };
let settings = { ...DEFAULT_SETTINGS, theme: null };
let collapsed = false;

function detectTheme() {
  const h = new Date().getHours();
  if (h >= 6 && h < 16) return 'morning';
  if (h >= 16 && h < 20) return 'evening';
  return 'night';
}

function initSettings() {
  try {
    settings = { ...DEFAULT_SETTINGS, ...JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}') };
  } catch {
    settings = { ...DEFAULT_SETTINGS, theme: null };
  }
  if (!settings.theme) settings.theme = detectTheme();

  elements.lockWindow.checked = !!settings.locked;
  elements.minToTray.checked = settings.minToTray !== false;
  elements.stationInput.value = settings.station || '';

  applyTheme(settings.theme);
  applyLock(!!settings.locked);
  if (api.setCloseToTray) api.setCloseToTray(elements.minToTray.checked);
  if (api.setStation) api.setStation(settings.station || '');
}

function saveSettings() {
  settings.theme = document.documentElement.dataset.theme || settings.theme;
  settings.locked = elements.lockWindow.checked;
  settings.minToTray = elements.minToTray.checked;
  settings.station = elements.stationInput.value.trim();
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  } catch {}
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  elements.themeChips.forEach((chip) => {
    chip.classList.toggle('active', chip.dataset.themeChoice === theme);
  });
}

function applyLock(locked) {
  document.body.classList.toggle('locked', locked);
  if (api.setLocked) api.setLocked(locked);
}

function toggleCollapse(force) {
  collapsed = typeof force === 'boolean' ? force : !collapsed;
  document.body.classList.toggle('collapsed', collapsed);
  elements.minimizeBtn.title = collapsed ? 'Expand' : 'Collapse';
  if (api.setCollapsed) api.setCollapsed(collapsed);
  if (collapsed) elements.settingsPanel.hidden = true;
}

function openSettings() {
  if (collapsed) toggleCollapse(false);
  elements.settingsPanel.hidden = false;
}

function closeSettingsPanel() {
  elements.settingsPanel.hidden = true;
}

function setupEventListeners() {
  // Window controls
  elements.minimizeBtn.addEventListener('click', () => toggleCollapse());
  elements.closeBtn.addEventListener('click', () => api.closeOverlay());

  // Settings
  elements.settingsBtn.addEventListener('click', openSettings);
  elements.closeSettings.addEventListener('click', closeSettingsPanel);
  elements.settingsDone.addEventListener('click', closeSettingsPanel);
  elements.settingsOpenFolder.addEventListener('click', openScanFolder);
  elements.themeChips.forEach((chip) => {
    chip.addEventListener('click', () => {
      applyTheme(chip.dataset.themeChoice);
      saveSettings();
    });
  });
  elements.lockWindow.addEventListener('change', () => {
    applyLock(elements.lockWindow.checked);
    saveSettings();
  });
  elements.minToTray.addEventListener('change', () => {
    if (api.setCloseToTray) api.setCloseToTray(elements.minToTray.checked);
    saveSettings();
  });
  elements.stationInput.addEventListener('change', () => {
    settings.station = elements.stationInput.value.trim();
    if (api.setStation) api.setStation(settings.station);
    saveSettings();
  });
  
  // Actions
  elements.scanBtn.addEventListener('click', () => {
    api.triggerScan();
    setStatus('scanning', 'Scanning…');
  });
  
  elements.calibrateBtn.addEventListener('click', () => {
    api.triggerCalibrate();
    log('info', 'Calibration mode triggered (F8)');
  });
  
  // Click-through toggle
  elements.clickThroughBtn.addEventListener('click', toggleClickThrough);
  
  // Preview actions
  elements.openScanFolder.addEventListener('click', openScanFolder);
  elements.uploadToSite.addEventListener('click', uploadToSite);
  
  // Drag handling for title bar (when not click-through)
  let isDragging = false;
  let dragOffset = { x: 0, y: 0 };
  
  elements.titleBar.addEventListener('mousedown', (e) => {
    if (clickThrough || e.target.closest('.icon-btn')) return;
    isDragging = true;
    dragOffset.x = e.clientX;
    dragOffset.y = e.clientY;
  });
  
  document.addEventListener('mousemove', (e) => {
    if (!isDragging) return;
    // Handled by -webkit-app-region: drag in CSS
  });
  
  document.addEventListener('mouseup', () => { isDragging = false; });
  
  // Keyboard shortcuts (global via scanner, but also local)
  document.addEventListener('keydown', (e) => {
    if (e.key === 'F9') api.triggerScan();
    if (e.key === 'F8') api.triggerCalibrate();
    if (e.key === 'Escape') {
      if (!elements.settingsPanel.hidden) {
        closeSettingsPanel();
      } else {
        toggleCollapse();
      }
    }
  });
  
  // Resize handling
  setupResize();
}

function setupScannerListeners() {
  api.onScannerOutput(handleScannerOutput);
  api.onScannerLog(handleScannerLog);
  api.onScannerExit(handleScannerExit);
}

function handleScannerOutput(data) {
  if (data.type === 'scan_complete') {
    if (collapsed) toggleCollapse(false);
    lastScanData = data.payload;
    updateScanPreview(data.payload);
    setStatus('connected', 'Connected');
    log('success', `Scan complete: ${data.payload.commodities.length} commodities from ${data.payload.station}`);
  } else if (data.type === 'calibration_complete') {
    log('success', `Calibration saved: ${JSON.stringify(data.payload)}`);
  } else if (data.type === 'status') {
    log('info', data.message);
    if (/bridge ready|^region /i.test(data.message)) setStatus('connected', 'Connected');
  }
}

function handleScannerLog(line) {
  const lower = line.toLowerCase();
  let type = 'info';
  if (lower.includes('error') || lower.includes('failed')) type = 'error';
  else if (lower.includes('warn')) type = 'warn';
  else if (lower.includes('saved') || lower.includes('complete') || lower.includes('✅')) type = 'success';
  else if (lower.includes('scanning') || lower.includes('capturing')) type = 'info';
  log(type, line);
}

function handleScannerExit(code) {
  setStatus('disconnected', 'Scanner stopped');
  log('error', `Scanner process exited with code ${code}`);
  // Could auto-restart here
}

function checkScannerConnection() {
  setStatus('connecting', 'Starting scanner…');
  // Connection status will update via scanner logs
}

function setStatus(state, label) {
  const dot = elements.scannerStatus.querySelector('.dot');
  const labelEl = elements.scannerStatus.querySelector('.label');
  
  dot.className = 'dot ' + state;
  labelEl.textContent = label;
}

function updateScanPreview(scan) {
  elements.lastScan.textContent = formatTimeAgo(scan.timestamp);
  elements.commodityCount.textContent = scan.commodities.length;
  elements.stationName.textContent = scan.station;
  
  // Build table
  const validRows = scan.commodities
    .filter(c => c.sell != null || c.buy != null)
    .sort((a, b) => String(a.commodity).localeCompare(String(b.commodity)))
    .slice(0, 15);
  
  if (validRows.length === 0) {
    elements.previewTable.innerHTML = '<p class="hint">No valid buy/sell data in scan</p>';
  } else {
    elements.previewTable.innerHTML = `
      <table>
        <thead>
          <tr>
            <th>Commodity</th>
            <th class="right">Buy</th>
            <th class="right">Sell</th>
            <th class="right">Stock</th>
            <th class="right">Demand</th>
          </tr>
        </thead>
        <tbody>
          ${validRows.map(c => `
            <tr>
              <td>${escapeHtml(c.commodity)}</td>
              <td class="right positive">${formatNumber(c.buy)}</td>
              <td class="right negative">${formatNumber(c.sell)}</td>
              <td class="right">${formatNumber(c.stock)}</td>
              <td class="right">${formatNumber(c.demand)}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;
  }
  
  elements.previewCard.style.display = 'block';
}

function log(type, message) {
  const time = new Date().toLocaleTimeString();
  const entry = document.createElement('div');
  entry.className = 'log-entry';
  entry.innerHTML = `
    <span class="log-time">${time}</span>
    <span class="log-message ${type}">${escapeHtml(message)}</span>
  `;
  elements.logConsole.appendChild(entry);
  elements.logConsole.scrollTop = elements.logConsole.scrollHeight;
  
  // Limit log entries
  while (elements.logConsole.children.length > 100) {
    elements.logConsole.removeChild(elements.logConsole.firstChild);
  }
}

function toggleClickThrough() {
  clickThrough = !clickThrough;
  api.setClickThrough(clickThrough);
  elements.clickThroughBtn.classList.toggle('active', clickThrough);
  elements.clickThroughBtn.title = clickThrough 
    ? 'Click-through ON (hold Shift + drag title bar to move)' 
    : 'Click-through OFF';
  log('info', `Click-through ${clickThrough ? 'enabled' : 'disabled'}`);
}

function openScanFolder() {
  api.openScansFolder();
  api.getScansFolder().then((dir) => log('info', `Scans folder: ${dir}`));
}

function uploadToSite() {
  if (!lastScanData) {
    log('warn', 'Scan something first (F9), then upload');
    return;
  }
  api.openSite();
  log('info', 'Opened SC-Truckers market - drag the JSON from the scans folder into Import Scan');
}

function setupResize() {
  let isResizing = false;
  let startWidth, startHeight, startX, startY;

  elements.resizeHandle.addEventListener('mousedown', async (e) => {
    if (clickThrough || collapsed || document.body.classList.contains('locked')) return;
    isResizing = true;
    startX = e.screenX;
    startY = e.screenY;
    try {
      const b = await api.getBounds();
      startWidth = b ? b.width : window.innerWidth;
      startHeight = b ? b.height : window.innerHeight;
    } catch {
      startWidth = window.innerWidth;
      startHeight = window.innerHeight;
    }
    e.preventDefault();
  });

  window.addEventListener('mousemove', (e) => {
    if (!isResizing) return;
    const width = Math.max(260, startWidth + (e.screenX - startX));
    const height = Math.max(300, startHeight + (e.screenY - startY));
    if (api.resizeWindow) api.resizeWindow(width, height);
  });

  window.addEventListener('mouseup', () => { isResizing = false; });
}

// Helpers
function formatTimeAgo(timestamp) {
  const secs = Math.floor(Date.now() / 1000 - timestamp);
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  return `${Math.floor(secs / 3600)}h ago`;
}

function formatNumber(n) {
  if (n == null) return '—';
  return n.toLocaleString();
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

// Cleanup on unload
window.addEventListener('beforeunload', () => {
  api.removeAllListeners('scanner-output');
  api.removeAllListeners('scanner-log');
  api.removeAllListeners('scanner-exit');
});