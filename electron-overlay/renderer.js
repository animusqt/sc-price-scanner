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
  resizeHandle: document.getElementById('resizeHandle')
};

// Initialize
document.addEventListener('DOMContentLoaded', init);

function init() {
  setupEventListeners();
  setupScannerListeners();
  log('info', 'Overlay initialized');
  log('info', `Platform: ${navigator.platform}`);
  checkScannerConnection();
  if (api.rendererReady) api.rendererReady();
}

function setupEventListeners() {
  // Window controls
  elements.minimizeBtn.addEventListener('click', () => api.minimizeOverlay());
  elements.closeBtn.addEventListener('click', () => api.closeOverlay());
  
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
    if (e.key === 'Escape') api.minimizeOverlay();
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
    .map(c => ({
      ...c,
      margin: (c.buy != null && c.sell != null && c.buy > 0)
        ? (((c.sell - c.buy) / c.buy) * 100).toFixed(1)
        : null
    }))
    .sort((a, b) => (parseFloat(b.margin) || -1e9) - (parseFloat(a.margin) || -1e9))
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
            <th class="right margin">Margin</th>
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
              <td class="right margin">${c.margin != null ? c.margin + '%' : '-'}</td>
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
  if (!lastScanData) return;
  log('info', 'Upload to SC-Truckers… (implement via fetch to your API)');
  // Implementation: POST to your backend /api/scans/import
}

function setupResize() {
  let isResizing = false;
  let startWidth, startHeight, startX, startY;
  
  elements.resizeHandle.addEventListener('mousedown', (e) => {
    if (clickThrough) return;
    isResizing = true;
    startX = e.clientX;
    startY = e.clientY;
    const rect = document.getElementById('app').getBoundingClientRect();
    startWidth = rect.width;
    startHeight = rect.height;
    e.preventDefault();
  });
  
  document.addEventListener('mousemove', (e) => {
    if (!isResizing) return;
    const newWidth = Math.max(300, startWidth + (e.clientX - startX));
    const newHeight = Math.max(400, startHeight + (e.clientY - startY));
    document.getElementById('app').style.width = newWidth + 'px';
    document.getElementById('app').style.height = newHeight + 'px';
  });
  
  document.addEventListener('mouseup', () => { isResizing = false; });
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