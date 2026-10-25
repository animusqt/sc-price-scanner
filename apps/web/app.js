const statusNode = document.getElementById('status');
const marketsNode = document.getElementById('markets');
let accessToken = null;
let pendingReport = null;
let pendingClientReportId = null;
const reportFile = document.getElementById('scan-file');
const submitReportButton = document.getElementById('submit-report');
const confirmReport = document.getElementById('confirm-report');

const fragment = new URLSearchParams(window.location.hash.slice(1));
if (fragment.has('access_token')) {
  accessToken = fragment.get('access_token');
  history.replaceState(null, '', window.location.pathname + window.location.search);
  statusNode.textContent = 'Signed in with Discord. Scan submission is available.';
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[char]);
}

function rowsFor(value) {
  if (!Array.isArray(value) || value.length === 0) return '<td>—</td><td>—</td>';
  const confirmed = value.filter((item) => item.review_status === 'confirmed');
  const candidates = confirmed.length ? confirmed : value;
  const best = candidates.reduce((left, right) => right.confidence > left.confidence ? right : left);
  const review = best.review_status === 'confirmed' ? 'confirmed' : 'pending review';
  return `<td>${Number(best.value).toLocaleString()} <small>${esc(review)}</small></td><td>${Math.round(best.confidence * 100)}%</td>`;
}

async function loadMarkets() {
  const patch = document.getElementById('patch').value.trim();
  const shard = document.getElementById('shard').value.trim();
  if (!patch || !shard) {
    statusNode.textContent = 'Patch and shard are required to keep market data separate.';
    return;
  }
  statusNode.textContent = 'Loading reports…';
  try {
    const params = new URLSearchParams({ patch_version: patch, server_shard: shard });
    const response = await apiRequest(`/api/v2/markets?${params}`);
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || body.error?.message || `API returned ${response.status}`);
    marketsNode.innerHTML = body.map((market) => `
      <article class="market">
        <h2>${esc(market.location)} · ${esc(market.commodity)}</h2>
        <table><thead><tr><th>Side</th><th>Value</th><th>Confidence</th></tr></thead><tbody>
          <tr><td>Buy</td>${rowsFor(market.buy)}</tr>
          <tr><td>Sell</td>${rowsFor(market.sell)}</tr>
          <tr><td>Stock</td>${rowsFor(market.stock)}</tr>
        </tbody></table>
      </article>`).join('');
    statusNode.textContent = `${body.length} market rows for patch ${patch}, shard ${shard}.`;
  } catch (error) {
    statusNode.textContent = `Could not load market: ${error.message}`;
  }
}

document.getElementById('refresh').addEventListener('click', loadMarkets);

function updateSubmitState() {
  submitReportButton.disabled = !(
    accessToken
    && pendingReport
    && confirmReport.checked
    && document.getElementById('report-patch').value.trim()
    && document.getElementById('report-shard').value.trim()
  );
}

async function apiRequest(url, options = {}) {
  const headers = new Headers(options.headers || {});
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`);
  let response = await fetch(url, { ...options, headers, credentials: 'same-origin' });
  if (response.status === 401) {
    const refresh = await fetch('/api/v2/auth/refresh', {
      method: 'POST',
      credentials: 'same-origin'
    });
    if (refresh.ok) {
      const tokens = await refresh.json();
      accessToken = tokens.access_token;
      headers.set('Authorization', `Bearer ${accessToken}`);
      response = await fetch(url, { ...options, headers, credentials: 'same-origin' });
    }
  }
  return response;
}

reportFile.addEventListener('change', async () => {
  pendingReport = null;
  pendingClientReportId = null;
  confirmReport.checked = false;
  document.getElementById('upload-status').textContent = '';
  const file = reportFile.files?.[0];
  if (!file) {
    document.getElementById('scan-preview').textContent = 'No scan selected.';
    updateSubmitState();
    return;
  }
  if (file.size > 2_000_000) {
    document.getElementById('scan-preview').textContent = 'This JSON file exceeds the 2 MB limit.';
    updateSubmitState();
    return;
  }
  try {
    const scan = JSON.parse(await file.text());
    if (!scan.station || !Array.isArray(scan.commodities) || scan.commodities.length === 0) {
      throw new Error('File must include a station and commodity rows.');
    }
    if (scan.commodities.length > 500) throw new Error('A report can include at most 500 commodities.');
    pendingReport = scan;
    pendingClientReportId = crypto.randomUUID();
    document.getElementById('scan-preview').innerHTML = `
      <p><strong>${esc(scan.station)}</strong>: ${scan.commodities.length} commodities,
      scanned ${esc(new Date((scan.timestamp || 0) * 1000).toLocaleString())}.
      ${scan.inventory?.length || 0} cargo rows.</p>
      <div class="preview-table"><table>
        <thead><tr><th>Commodity</th><th>Buy</th><th>Sell</th><th>Stock</th><th>Scanned</th></tr></thead>
        <tbody>${scan.commodities.map((row) => `
          <tr><td>${esc(row.commodity)}</td><td>${esc(row.buy ?? '—')}</td>
          <td>${esc(row.sell ?? '—')}</td><td>${esc(row.stock ?? '—')}</td>
          <td>${esc(new Date((row.scanned_at || scan.timestamp || 0) * 1000).toLocaleString())}</td></tr>
        `).join('')}</tbody>
      </table></div>`;
    document.getElementById('report-confidence').value =
      Number.isFinite(scan.quality?.ocr_confidence) ? scan.quality.ocr_confidence : '0.5';
  } catch (error) {
    document.getElementById('scan-preview').textContent = `Could not read scan: ${error.message}`;
  }
  updateSubmitState();
});

for (const id of ['report-patch', 'report-shard', 'report-confidence']) {
  document.getElementById(id).addEventListener('input', updateSubmitState);
}
confirmReport.addEventListener('change', updateSubmitState);

submitReportButton.addEventListener('click', async () => {
  if (!pendingReport || !confirmReport.checked || !accessToken) return;
  const confidence = Number(document.getElementById('report-confidence').value);
  if (!Number.isFinite(confidence) || confidence < 0 || confidence > 1) {
    document.getElementById('upload-status').textContent = 'OCR confidence must be between 0 and 1.';
    return;
  }

  const scan = pendingReport;
  const patch = document.getElementById('report-patch').value.trim();
  const shard = document.getElementById('report-shard').value.trim();
  const fallbackObservedAt = new Date((scan.timestamp || Date.now() / 1000) * 1000).toISOString();
  const body = {
    client_report_id: pendingClientReportId,
    location: scan.station,
    patch_version: patch,
    server_shard: shard,
    observed_at: fallbackObservedAt,
    ocr_confidence: confidence,
    source: 'player-scan',
    commodities: scan.commodities.map((row) => ({
      commodity: row.commodity,
      buy: row.buy ?? null,
      sell: row.sell ?? null,
      stock: row.stock ?? null,
      demand: row.demand ?? null,
      demand_label: row.demand_label ?? null,
      observed_at: new Date((row.scanned_at || scan.timestamp) * 1000).toISOString()
    })),
    inventory: (scan.inventory || []).map((row) => ({
      commodity: row.commodity,
      quantity: row.quantity,
      profit_direction: row.profit === 'up' || row.profit === 'down' ? row.profit : null
    }))
  };

  submitReportButton.disabled = true;
  document.getElementById('upload-status').textContent = 'Submitting after your confirmation…';
  try {
    const response = await apiRequest('/api/v2/reports', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || `API returned ${response.status}`);
    document.getElementById('upload-status').textContent =
      result.duplicate
        ? 'This scan was already submitted.'
        : `Submitted as ${result.status}; a moderator must confirm it before it affects routes.`;
    pendingReport = null;
    pendingClientReportId = null;
    confirmReport.checked = false;
  } catch (error) {
    document.getElementById('upload-status').textContent = `Submission failed; local file is unchanged: ${error.message}`;
  } finally {
    updateSubmitState();
  }
});

updateSubmitState();
