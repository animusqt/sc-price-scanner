import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { API_BASE, getHistory, getMarkets, getRoutes, getSources } from "./api";
import type { HistoryPoint, MarketRow, Route, Ship, SourceStatus } from "./types";

type Page = "Dashboard" | "Commodities" | "Routes" | "Fleet" | "Analytics" | "Replay" | "Settings";
type Theme = "dark" | "light";

const pages: Page[] = [
  "Dashboard",
  "Commodities",
  "Routes",
  "Fleet",
  "Analytics",
  "Replay",
  "Settings",
];
const money = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const defaultShip: Ship[] = [
  { id: "starter", name: "My ship", capacity: 100, favorite: true, notes: "" },
];

function readShips(): Ship[] {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem("scip.fleet") || "null");
    if (Array.isArray(saved) && saved.every(isShip)) return saved;
  } catch {
    localStorage.removeItem("scip.fleet");
  }
  return defaultShip;
}

function readTheme(): Theme {
  return localStorage.getItem("scip.theme") === "light" ? "light" : "dark";
}

function marketPrice(values: MarketRow["buy"]): number | null {
  if (!values.length) return null;
  return values.reduce((total, value) => total + value.value, 0) / values.length;
}

function App() {
  const [page, setPage] = useState<Page>("Dashboard");
  const [theme, setTheme] = useState<Theme>(readTheme);
  const [notificationsEnabled, setNotificationsEnabled] = useState(
    () => localStorage.getItem("scip.notifications") !== "false",
  );
  const [patch, setPatch] = useState(() => localStorage.getItem("scip.patch") || "live");
  const [shard, setShard] = useState(() => localStorage.getItem("scip.shard") || "global");
  const [markets, setMarkets] = useState<MarketRow[]>([]);
  const [sources, setSources] = useState<SourceStatus[]>([]);
  const [routes, setRoutes] = useState<Route[]>([]);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("Connecting to local SCIP data service");
  const [search, setSearch] = useState("");
  const [ships, setShips] = useState(readShips);
  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const [historyCommodity, setHistoryCommodity] = useState("");
  const [historyLocation, setHistoryLocation] = useState("");
  const [days, setDays] = useState(7);
  const [capacity, setCapacity] = useState(() => ships[0]?.capacity ?? 100);
  const [fuelCost, setFuelCost] = useState(0);
  const [fees, setFees] = useState(0);
  const [risk, setRisk] = useState(0.25);
  const [travelHours, setTravelHours] = useState(1);
  const [minimumConfidence, setMinimumConfidence] = useState(0.5);
  const [currentLocation, setCurrentLocation] = useState("");
  const [availableCredits, setAvailableCredits] = useState(100000);
  const [selectedShipId, setSelectedShipId] = useState(() => ships[0]?.id ?? "");
  const [newShipName, setNewShipName] = useState("");
  const [backupStatus, setBackupStatus] = useState("");

  const refresh = useCallback(async () => {
    setError("");
    try {
      const [marketRows, configuredSources] = await Promise.all([
        getMarkets(patch.trim(), shard.trim()),
        getSources(),
      ]);
      setMarkets(marketRows);
      setSources(configuredSources);
      setStatus(
        marketRows.length
          ? `${marketRows.length} market records · updated ${new Date().toLocaleTimeString()}`
          : "Connected · no market records for this patch and shard",
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Market refresh failed.");
      setStatus("Data service unavailable");
    }
  }, [patch, shard]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("scip.theme", theme);
  }, [theme]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    localStorage.setItem("scip.fleet", JSON.stringify(ships));
  }, [ships]);

  useEffect(() => {
    localStorage.setItem("scip.notifications", String(notificationsEnabled));
  }, [notificationsEnabled]);

  useEffect(() => {
    localStorage.setItem("scip.patch", patch);
    localStorage.setItem("scip.shard", shard);
  }, [patch, shard]);

  const commodities = useMemo(
    () =>
      markets.filter((row) =>
        `${row.commodity} ${row.location}`.toLowerCase().includes(search.toLowerCase()),
      ),
    [markets, search],
  );

  const uniqueLocations = useMemo(
    () => [...new Set(markets.map((row) => row.location))].sort(),
    [markets],
  );

  async function calculateRoutes(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    try {
      const result = await getRoutes({
        cargo_capacity: capacity,
        current_location: currentLocation,
        available_credits: availableCredits,
        fuel_cost: fuelCost,
        route_fees: fees,
        route_risk: risk,
        travel_hours: travelHours,
        minimum_confidence: minimumConfidence,
        server_shard: shard.trim(),
        patch_version: patch.trim(),
      });
      setRoutes(result.routes);
      if (!result.routes.length) setStatus("No fresh, trusted route candidates match these settings.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Route calculation failed.");
    }
  }

  async function loadHistory(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!historyCommodity || !historyLocation) {
      setError("Choose a commodity and location before loading market replay.");
      return;
    }
    setError("");
    try {
      setHistory(
        await getHistory({
          commodity: historyCommodity,
          location: historyLocation,
          patch_version: patch.trim(),
          server_shard: shard.trim(),
          days,
        }),
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load market history.");
    }
  }

  function addShip(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const name = newShipName.trim();
    if (!name) return;
    setShips((current) => [
      ...current,
      { id: crypto.randomUUID(), name, capacity: 100, favorite: false, notes: "" },
    ]);
    setNewShipName("");
  }

  function removeShip(id: string) {
    const updatedShips = ships.filter((ship) => ship.id !== id);
    setShips(updatedShips);
    if (selectedShipId === id) {
      setSelectedShipId(updatedShips[0]?.id ?? "");
      setCapacity(updatedShips[0]?.capacity ?? 100);
    }
  }

  function downloadBackup() {
    const backup = {
      schemaVersion: 1,
      exportedAt: new Date().toISOString(),
      theme,
      notificationsEnabled,
      patch,
      shard,
      ships,
    };
    const link = document.createElement("a");
    const url = URL.createObjectURL(new Blob([JSON.stringify(backup, null, 2)], { type: "application/json" }));
    link.href = url;
    link.download = `scip-settings-${new Date().toISOString().slice(0, 10)}.json`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    setBackupStatus("Settings and fleet backup downloaded.");
  }

  async function restoreBackup(file: File | undefined) {
    if (!file) return;
    try {
      if (file.size > 1_000_000) throw new Error("Backup files must be smaller than 1 MB.");
      const backup: unknown = JSON.parse(await file.text());
      if (
        typeof backup !== "object" ||
        backup === null ||
        !("schemaVersion" in backup) ||
        backup.schemaVersion !== 1 ||
        !("ships" in backup) ||
        !Array.isArray(backup.ships) ||
        !backup.ships.every(isShip)
      ) {
        throw new Error("This file is not a supported SCIP settings backup.");
      }
      setShips(backup.ships);
      setSelectedShipId(backup.ships[0]?.id ?? "");
      setCapacity(backup.ships[0]?.capacity ?? 100);
      if ("theme" in backup && (backup.theme === "dark" || backup.theme === "light")) setTheme(backup.theme);
      if ("notificationsEnabled" in backup && typeof backup.notificationsEnabled === "boolean") {
        setNotificationsEnabled(backup.notificationsEnabled);
      }
      if ("patch" in backup && typeof backup.patch === "string") setPatch(backup.patch.slice(0, 80));
      if ("shard" in backup && typeof backup.shard === "string") setShard(backup.shard.slice(0, 100));
      setBackupStatus("Settings and fleet restored from backup.");
    } catch (cause) {
      setBackupStatus(cause instanceof Error ? cause.message : "Could not restore this backup.");
    }
  }

  const averageConfidence = markets.length
    ? Math.round(
        (markets.reduce((sum, market) => {
          const values = [...market.buy, ...market.sell, ...market.stock];
          return sum + (values.length ? values.reduce((n, row) => n + row.confidence, 0) / values.length : 0);
        }, 0) /
          markets.length) *
          100,
      )
    : 0;

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">S</span>
          <div><strong>SCIP</strong><small>INTELLIGENCE PLATFORM</small></div>
        </div>
        <div className="nav-label">OPERATIONS</div>
        <nav aria-label="Main navigation">
          {pages.map((item, index) => (
            <button
              className={`nav-item ${page === item ? "active" : ""}`}
              key={item}
              onClick={() => setPage(item)}
            >
              <span className="nav-icon">{["◈", "▦", "⌁", "◇", "▤", "◷", "⚙"][index]}</span>
              {item}
              {item === "Routes" && routes.length > 0 && <span className="nav-count">{routes.length}</span>}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <span className={`pulse ${error ? "offline" : ""}`} />
          <div><strong>{error ? "SERVICE OFFLINE" : "LOCAL SYSTEM"}</strong><small>NO ACCOUNT REQUIRED</small></div>
        </div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div><span className="eyebrow">STAR CITIZEN · MARKET INTELLIGENCE</span><h1>{page}</h1></div>
          <div className="top-controls">
            <label>PATCH <input aria-label="Game patch" value={patch} onChange={(event) => setPatch(event.target.value)} /></label>
            <label>SHARD <input aria-label="Server shard" value={shard} onChange={(event) => setShard(event.target.value)} /></label>
            <button className="icon-button" aria-label="Refresh market data" onClick={() => void refresh()}>⟳</button>
          </div>
        </header>

        {error && <div className="error-banner" role="alert">{error}</div>}
        <div className="status-line"><span className={`pulse ${error ? "offline" : ""}`} />{status}</div>

        {page === "Dashboard" && (
          <section className="page-content">
            <div className="metric-grid">
              <Metric label="MARKET RECORDS" value={money.format(markets.length)} foot="Selected patch / shard" />
              <Metric label="COMMODITIES" value={money.format(new Set(markets.map((row) => row.commodity)).size)} foot="In local market cache" />
              <Metric label="ROUTE CANDIDATES" value={money.format(routes.length)} foot="Risk-adjusted rankings" />
              <Metric label="DATA CONFIDENCE" value={`${averageConfidence}%`} foot="Average observation confidence" />
            </div>
            <div className="section-heading"><div><span className="eyebrow">MARKET SNAPSHOT</span><h2>Market overview</h2></div><button className="text-button" onClick={() => setPage("Commodities")}>Explore commodities →</button></div>
            <MarketTable rows={markets.slice(0, 12)} />
            <div className="empty-hint">Market collection, OCR validation, and supply submissions are managed by the SC-Truckers platform. SCIP reads market data and builds logistics intelligence from it.</div>
          </section>
        )}

        {page === "Commodities" && (
          <section className="page-content">
            <div className="section-heading"><div><span className="eyebrow">PRICE · SUPPLY · DEMAND</span><h2>Commodity explorer</h2></div><input className="search" aria-label="Search commodities" placeholder="Search commodity or location" value={search} onChange={(event) => setSearch(event.target.value)} /></div>
            <MarketTable rows={commodities} />
          </section>
        )}

        {page === "Routes" && (
          <section className="page-content">
            <div className="section-heading"><div><span className="eyebrow">MULTI-FACTOR OPTIMIZATION</span><h2>Route planner</h2></div></div>
            <form className="panel route-form" onSubmit={calculateRoutes}>
              <label>Current location<select value={currentLocation} onChange={(event) => setCurrentLocation(event.target.value)}><option value="">Any market</option>{uniqueLocations.map((location) => <option key={location}>{location}</option>)}</select></label>
              <label>Ship<select value={selectedShipId} onChange={(event) => {
                const ship = ships.find((item) => item.id === event.target.value);
                setSelectedShipId(event.target.value);
                if (ship) setCapacity(ship.capacity);
              }}>{ships.map((ship) => <option key={ship.id} value={ship.id}>{ship.name} · {ship.capacity} SCU</option>)}</select></label>
              <NumberField label="Cargo capacity" value={capacity} min={1} onChange={setCapacity} />
              <NumberField label="Available credits" value={availableCredits} min={0} onChange={setAvailableCredits} />
              <NumberField label="Fuel cost" value={fuelCost} min={0} onChange={setFuelCost} />
              <NumberField label="Route fees" value={fees} min={0} onChange={setFees} />
              <NumberField label="Risk preference" value={risk} min={0} max={1} step={0.05} onChange={setRisk} />
              <NumberField label="Travel hours" value={travelHours} min={0.1} step={0.1} onChange={setTravelHours} />
              <NumberField label="Minimum confidence" value={minimumConfidence} min={0} max={1} step={0.05} onChange={setMinimumConfidence} />
              <button type="submit">Calculate ranked routes</button>
            </form>
            <div className="rank-tabs">
              <RankedRoutes title="BEST PROFIT" routes={routes} rank={(route) => route.risk_adjusted_profit} />
              <RankedRoutes title="BEST RELIABILITY" routes={routes} rank={(route) => route.confidence * (1 - (route.bug_risk_penalty || 0))} />
              <RankedRoutes title="LOWEST RISK" routes={routes} rank={(route) => -(route.bug_risk_penalty || 0) - (1 - route.confidence)} />
              <RankedRoutes title="TIME EFFICIENCY" routes={routes} rank={(route) => route.risk_adjusted_profit_per_hour} />
            </div>
          </section>
        )}

        {page === "Fleet" && (
          <section className="page-content">
            <div className="section-heading"><div><span className="eyebrow">LOCAL PROFILE</span><h2>Fleet manager</h2></div></div>
            <form className="panel add-ship" onSubmit={addShip}><input aria-label="New ship name" placeholder="Ship name" value={newShipName} onChange={(event) => setNewShipName(event.target.value)} /><button type="submit">Add ship</button></form>
            <div className="fleet-grid">
              {ships.map((ship) => (
                <article className="panel ship-card" key={ship.id}>
                  <div className="ship-top"><div><span className="eyebrow">VESSEL</span><h3>{ship.name}</h3></div><button className="star-button" aria-label={`Toggle favorite ${ship.name}`} onClick={() => setShips((items) => items.map((item) => item.id === ship.id ? { ...item, favorite: !item.favorite } : item))}>{ship.favorite ? "★" : "☆"}</button></div>
                  <label>CARGO CAPACITY <input type="number" min="1" value={ship.capacity} onChange={(event) => setShips((items) => items.map((item) => item.id === ship.id ? { ...item, capacity: Number(event.target.value) } : item))} /></label>
                  <label>NOTES <textarea value={ship.notes} onChange={(event) => setShips((items) => items.map((item) => item.id === ship.id ? { ...item, notes: event.target.value } : item))} /></label>
                  <button className="danger-button" onClick={() => removeShip(ship.id)}>Remove vessel</button>
                </article>
              ))}
            </div>
            <p className="muted">Fleet settings are saved locally on this device. Cloud sync can be added when optional account support is introduced.</p>
          </section>
        )}

        {page === "Analytics" && (
          <section className="page-content">
            <div className="section-heading"><div><span className="eyebrow">MARKET SIGNALS</span><h2>Analytics</h2></div></div>
            <div className="panel"><h3>Commodity movers</h3><MarketTable rows={[...markets].sort((a, b) => (marketPrice(b.sell) ?? 0) - (marketPrice(a.sell) ?? 0)).slice(0, 20)} /></div>
            <div className="panel"><h3>Data source status</h3><SourceList sources={sources} /></div>
          </section>
        )}

        {page === "Replay" && (
          <section className="page-content">
            <div className="section-heading"><div><span className="eyebrow">HISTORICAL PLAYBACK</span><h2>Market replay</h2></div></div>
            <form className="panel replay-form" onSubmit={loadHistory}>
              <label>COMMODITY<select value={historyCommodity} onChange={(event) => setHistoryCommodity(event.target.value)}><option value="">Select commodity</option>{[...new Set(markets.map((row) => row.commodity))].sort().map((name) => <option key={name}>{name}</option>)}</select></label>
              <label>LOCATION<select value={historyLocation} onChange={(event) => setHistoryLocation(event.target.value)}><option value="">Select location</option>{uniqueLocations.map((name) => <option key={name}>{name}</option>)}</select></label>
              <label>WINDOW<select value={days} onChange={(event) => setDays(Number(event.target.value))}><option value={1}>24 hours</option><option value={7}>7 days</option><option value={30}>30 days</option><option value={365}>Patch history</option></select></label>
              <button type="submit">Load replay</button>
            </form>
            <HistoryChart points={history} />
          </section>
        )}

        {page === "Settings" && (
          <section className="page-content settings-grid">
            <div className="panel"><span className="eyebrow">GENERAL · APPEARANCE</span><h2>Application preferences</h2><label>THEME<select value={theme} onChange={(event) => setTheme(event.target.value as Theme)}><option value="dark">Mobiglas dark</option><option value="light">Light</option></select></label><p className="muted">SCIP is ready to use without creating an account.</p></div>
            <div className="panel"><span className="eyebrow">NOTIFICATIONS</span><h2>Intelligence alerts</h2><label className="setting-toggle"><input type="checkbox" checked={notificationsEnabled} onChange={(event) => setNotificationsEnabled(event.target.checked)} /> Enable market and risk notifications</label><p className="muted">Notification delivery is local-only until a live feed is configured.</p></div>
            <div className="panel"><span className="eyebrow">FLEET SETTINGS</span><h2>Local fleet</h2><p>{ships.length} saved vessel{ships.length === 1 ? "" : "s"} · {ships.filter((ship) => ship.favorite).length} favorite</p><button className="secondary-button" onClick={() => setPage("Fleet")}>Manage fleet</button></div>
            <div className="panel"><span className="eyebrow">MARKET SETTINGS</span><h2>Market context</h2><label>PATCH<input value={patch} maxLength={80} onChange={(event) => setPatch(event.target.value)} /></label><label>SERVER SHARD<input value={shard} maxLength={100} onChange={(event) => setShard(event.target.value)} /></label></div>
            <div className="panel"><span className="eyebrow">DATA SOURCES</span><h2>Market connection</h2><p>Local API: <code>{API_BASE}</code></p><SourceList sources={sources} /></div>
            <div className="panel"><span className="eyebrow">BACKUP AND RESTORE</span><h2>Local preferences</h2><p className="muted">Export or restore your theme, market context, notification preference, and fleet profile.</p><button className="secondary-button" onClick={downloadBackup}>Download backup</button><label>RESTORE BACKUP<input type="file" accept=".json,application/json" onChange={(event) => void restoreBackup(event.target.files?.[0])} /></label>{backupStatus && <small role="status">{backupStatus}</small>}</div>
            <div className="panel"><span className="eyebrow">SUPPORT SCIP</span><h2>Support the project</h2><p className="muted">Support helps fund development. Supporter benefits are cosmetic and never provide gameplay advantages.</p><div className="support-links"><button disabled>PayPal</button><button disabled>Patreon</button><button disabled>Ko-fi</button></div><small className="muted">Project campaign links must be configured. Profile badges, custom themes, and beta access are not active yet.</small></div>
            <div className="panel"><span className="eyebrow">ABOUT</span><h2>SCIP 2.0</h2><p>Star Citizen Intelligence Platform</p><p className="muted">Desktop-first logistics, market analysis, and route intelligence.</p></div>
          </section>
        )}
      </main>

      <aside className="intel">
        <div className="intel-head"><span className="eyebrow">INTELLIGENCE FEED</span>        <span className="live-indicator">● LOCAL</span></div>
        <h2>Operational picture</h2>
        <div className="intel-card"><span className="feed-mark">◎</span><div><strong>Market data</strong><p>{markets.length ? `${markets.length} observations in current view` : "Waiting for synchronized market data"}</p></div></div>
        <div className="intel-card"><span className="feed-mark amber">⌁</span><div><strong>Risk context</strong><p>Route rankings account for confidence, travel, and reported risk penalties.</p></div></div>
        <div className="intel-card"><span className="feed-mark violet">◷</span><div><strong>Data freshness</strong><p>{markets.length ? "Inspect each observation timestamp in Commodities." : "No timestamped market records available."}</p></div></div>
        <div className="intel-status"><span className={`pulse ${error ? "offline" : ""}`} /><div><strong>{error ? "API DISCONNECTED" : "API STATUS"}</strong><p>{error ? "Check the local service." : "Connected to the local SCIP service."}</p></div></div>
        <div className="intel-foot">SC-TRUCKERS LOVABLE FEED<br />PATCH {patch.toUpperCase()} · {shard.toUpperCase()}</div>
      </aside>
    </div>
  );
}

function Metric({ label, value, foot }: { label: string; value: string; foot: string }) {
  return <article className="metric panel"><span className="eyebrow">{label}</span><strong>{value}</strong><small>{foot}</small></article>;
}

function NumberField({ label, value, min, max, step, onChange }: { label: string; value: number; min: number; max?: number; step?: number; onChange(value: number): void }) {
  return <label>{label}<input type="number" required min={min} max={max} step={step ?? 1} value={value} onChange={(event) => onChange(Number(event.target.value))} /></label>;
}

function MarketTable({ rows }: { rows: MarketRow[] }) {
  if (!rows.length) return <div className="empty-state"><span>◈</span><h3>No market data in this view</h3><p>When the configured SC-Truckers/Lovable feed is available, its synchronized and confirmed market values will appear here.</p></div>;
  return <div className="table-wrap"><table><thead><tr><th>COMMODITY</th><th>LOCATION</th><th>BUY</th><th>SELL</th><th>SUPPLY</th><th>DEMAND</th><th>CONFIDENCE</th><th>OBSERVED</th></tr></thead><tbody>{rows.map((row, index) => {
    const buy = marketPrice(row.buy);
    const sell = marketPrice(row.sell);
    const confidenceValues = [...row.buy, ...row.sell, ...row.stock];
    const confidence = confidenceValues.length ? Math.round(confidenceValues.reduce((sum, item) => sum + item.confidence, 0) / confidenceValues.length * 100) : 0;
    const observed = [...row.buy, ...row.sell, ...row.stock].map((item) => item.latest_observed_at).filter(Boolean).sort().at(-1);
    return <tr key={`${row.location}-${row.commodity}-${index}`}><td className="commodity-name">{row.commodity}</td><td>{row.location}</td><td>{buy === null ? "—" : money.format(buy)}</td><td className="sell-value">{sell === null ? "—" : money.format(sell)}</td><td>{row.stock.length ? money.format(marketPrice(row.stock) ?? 0) : "—"}</td><td>{row.demand.length ? money.format(marketPrice(row.demand) ?? 0) : "—"}</td><td><span className="confidence">{confidence}%</span></td><td>{observed ? new Date(observed).toLocaleString() : "—"}</td></tr>;
  })}</tbody></table></div>;
}

function RankedRoutes({ title, routes, rank }: { title: string; routes: Route[]; rank(route: Route): number }) {
  const best = [...routes].sort((left, right) => rank(right) - rank(left))[0];
  return <article className="panel rank-card"><span className="eyebrow">{title}</span>{best ? <><strong>{best.origin} → {best.destination}</strong><p>{best.commodity}</p><div className="rank-value">{money.format(best.risk_adjusted_profit)} aUEC</div><small>{Math.round(best.confidence * 100)}% confidence · {best.travel_hours ?? "—"}h estimated</small></> : <p className="muted">Run route analysis to see ranked options.</p>}</article>;
}

function SourceList({ sources }: { sources: SourceStatus[] }) {
  if (!sources.length) return <p className="muted">Source status unavailable.</p>;
  return <ul className="source-list">{sources.map((source) => <li key={source.key}><span className={`source-dot ${source.enabled ? "enabled" : ""}`} /><div><strong>{source.name}</strong><small>{source.enabled ? "Connected" : "Not configured"} · {source.reason}</small></div></li>)}</ul>;
}

function HistoryChart({ points }: { points: HistoryPoint[] }) {
  if (!points.length) return <div className="empty-state"><span>◷</span><h3>No replay data selected</h3><p>Historical playback requires timestamped market history for the selected commodity and location.</p></div>;
  const maxValue = Math.max(...points.flatMap((point) => [point.buy ?? 0, point.sell ?? 0]), 1);
  return <div className="panel history-panel"><div className="chart-legend"><span className="buy-key">BUY</span><span className="sell-key">SELL</span></div><div className="chart">{points.map((point, index) => <div className="chart-point" key={`${point.observed_at}-${index}`} title={`${new Date(point.observed_at).toLocaleString()} · Buy ${point.buy ?? "—"} · Sell ${point.sell ?? "—"}`}><span className="bar-buy" style={{ height: `${Math.max(3, (point.buy ?? 0) / maxValue * 100)}%` }} /><span className="bar-sell" style={{ height: `${Math.max(3, (point.sell ?? 0) / maxValue * 100)}%` }} /><small>{new Date(point.observed_at).toLocaleDateString()}</small></div>)}</div></div>;
}

function isShip(value: unknown): value is Ship {
  if (typeof value !== "object" || value === null) return false;
  const ship = value as Record<string, unknown>;
  return typeof ship.id === "string"
    && ship.id.length <= 100
    && typeof ship.name === "string"
    && ship.name.length <= 100
    && typeof ship.capacity === "number"
    && Number.isFinite(ship.capacity)
    && ship.capacity > 0
    && typeof ship.favorite === "boolean"
    && typeof ship.notes === "string"
    && ship.notes.length <= 4000;
}

export default App;
