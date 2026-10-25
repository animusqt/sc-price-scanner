import { useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import {
  createFleet,
  deleteFleet,
  getCachedImage,
  getCatalogCommodities,
  getCatalogComponents,
  getCatalogImporters,
  getCatalogLocations,
  getCatalogShips,
  getFleets,
  getImportHistory,
  importCatalogApi,
  importCatalogCsv,
  importCatalogJson,
  removeFleetShip,
  setFleetShip,
  updateFleet,
} from "./api";
import type {
  ImportHistoryEntry,
  ImportResult,
} from "./types";

const numberFormat = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const importExample = JSON.stringify({
  commodities: [{ name: "Commodity name", category: "Mineral" }],
  locations: [{ name: "Terminal name", planet: "Planet", system: "Stanton" }],
  prices: [{
    commodity: "Commodity name",
    location: "Terminal name",
    buy_price: 100,
    sell_price: 150,
    supply: 100,
    demand: 50,
    confidence_score: 0.8,
    patch_version: "live",
    server_shard: "global",
  }],
  ships: [{ name: "Ship name", manufacturer: "Manufacturer", cargo_capacity_scu: 100 }],
  components: [{ name: "Component name", type: "Weapon", size: 1 }],
}, null, 2);

function useLoaded<T>(load: () => Promise<T>, dependencies: unknown[]): {
  value: T | null;
  error: string;
  loading: boolean;
  refresh(): Promise<void>;
} {
  const [value, setValue] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const refresh = async () => {
    setLoading(true);
    setError("");
    try {
      setValue(await load());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load catalog data.");
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void refresh();
  // `load` is deliberately represented by its stable inputs to prevent refresh loops.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, dependencies);
  return { value, error, loading, refresh };
}

function ErrorMessage({ message }: { message: string }) {
  return message ? <div className="error-banner" role="alert">{message}</div> : null;
}

export function CommodityBrowserPage({ patch, shard }: { patch: string; shard: string }) {
  const catalog = useLoaded(() => getCatalogCommodities(patch, shard), [patch, shard]);
  const locations = useLoaded(getCatalogLocations, []);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [minProfit, setMinProfit] = useState("");
  const [minSupply, setMinSupply] = useState("");
  const [maxSupply, setMaxSupply] = useState("");
  const [minDemand, setMinDemand] = useState("");
  const [maxDemand, setMaxDemand] = useState("");
  const categories = useMemo(
    () => [...new Set((catalog.value ?? []).map((row) => row.category).filter((item): item is string => Boolean(item)))].sort(),
    [catalog.value],
  );
  const rows = (catalog.value ?? []).filter((row) => {
    const text = `${row.name} ${row.category ?? ""} ${row.best_buy_location ?? ""} ${row.best_sell_location ?? ""}`.toLowerCase();
    return text.includes(search.trim().toLowerCase())
      && (!category || row.category === category)
      && (minProfit === "" || (row.profit !== null && row.profit >= Number(minProfit)))
      && (minSupply === "" || (row.supply !== null && row.supply >= Number(minSupply)))
      && (maxSupply === "" || (row.supply !== null && row.supply <= Number(maxSupply)))
      && (minDemand === "" || (row.demand !== null && row.demand >= Number(minDemand)))
      && (maxDemand === "" || (row.demand !== null && row.demand <= Number(maxDemand)));
  });

  return (
    <section className="page-content">
      <div className="section-heading">
        <div><span className="eyebrow">LIVE MARKET · CATALOG CACHE</span><h2>Commodity browser</h2></div>
        <div className="data-actions">
          <span className="confidence">{locations.value?.length ?? 0} cached locations</span>
          <button className="secondary-button" onClick={() => void catalog.refresh()}>Refresh catalog</button>
        </div>
      </div>
      <div className="panel catalog-filters">
        <label>SEARCH<input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Commodity or station" /></label>
        <label>CATEGORY<select value={category} onChange={(event) => setCategory(event.target.value)}><option value="">All categories</option>{categories.map((item) => <option key={item}>{item}</option>)}</select></label>
        <NumberFilter label="MIN PROFIT" value={minProfit} onChange={setMinProfit} />
        <NumberFilter label="MIN SUPPLY" value={minSupply} onChange={setMinSupply} />
        <NumberFilter label="MAX SUPPLY" value={maxSupply} onChange={setMaxSupply} />
        <NumberFilter label="MIN DEMAND" value={minDemand} onChange={setMinDemand} />
        <NumberFilter label="MAX DEMAND" value={maxDemand} onChange={setMaxDemand} />
      </div>
      <ErrorMessage message={catalog.error || locations.error} />
      {catalog.loading ? <p className="muted">Loading commodity catalog…</p> : <div className="table-wrap">
        <table><thead><tr><th>COMMODITY</th><th>CATEGORY</th><th>CURRENT</th><th>AVERAGE</th><th>SUPPLY</th><th>DEMAND</th><th>BEST BUY</th><th>BEST SELL</th><th>SPREAD</th><th>CONFIDENCE</th></tr></thead>
          <tbody>{rows.map((row) => <tr key={row.id}>
            <td className="commodity-name">{row.name}</td><td>{row.category ?? "—"}</td>
            <td>{formatMoney(row.current_price)}</td><td>{formatMoney(row.average_price)}</td>
            <td>{formatMoney(row.supply)}</td><td>{formatMoney(row.demand)}</td>
            <td>{row.best_buy_location ?? "—"}{row.best_buy_price !== null && <small className="table-subline">{formatMoney(row.best_buy_price)}</small>}</td>
            <td className="sell-value">{row.best_sell_location ?? "—"}{row.best_sell_price !== null && <small className="table-subline">{formatMoney(row.best_sell_price)}</small>}</td>
            <td>{formatMoney(row.profit)}</td><td>{row.confidence === null ? "—" : `${Math.round(row.confidence * 100)}%`}</td>
          </tr>)}</tbody>
        </table>
        {!rows.length && <p className="muted table-empty">{catalog.value?.length ? "No commodities match these filters." : "No imported commodity data is available yet. Use Data Import to populate the catalog."}</p>}
      </div>}
      <p className="muted">Market prices are synchronized snapshots, not static source data. Values older than seven days are excluded from the live catalog.</p>
    </section>
  );
}

export function ShipBrowserPage({
  patch,
  onRoutePlan,
}: {
  patch: string;
  onRoutePlan(capacity: number, shipName: string, shipId: string): void;
}) {
  const catalog = useLoaded(() => getCatalogShips(patch), [patch]);
  const components = useLoaded(getCatalogComponents, []);
  const [search, setSearch] = useState("");
  const [manufacturer, setManufacturer] = useState("");
  const [role, setRole] = useState("");
  const [minimumCargo, setMinimumCargo] = useState("");
  const [maximumCargo, setMaximumCargo] = useState("");
  const rows = (catalog.value ?? []).filter((ship) => {
    const text = `${ship.name} ${ship.manufacturer} ${ship.role ?? ""}`.toLowerCase();
    return text.includes(search.trim().toLowerCase())
      && (!manufacturer || ship.manufacturer === manufacturer)
      && (!role || ship.role === role)
      && (minimumCargo === "" || ship.cargo_capacity_scu >= Number(minimumCargo))
      && (maximumCargo === "" || ship.cargo_capacity_scu <= Number(maximumCargo));
  });
  const manufacturers = [...new Set((catalog.value ?? []).map((ship) => ship.manufacturer).filter(Boolean))].sort();
  const roles = [...new Set((catalog.value ?? []).map((ship) => ship.role).filter((item): item is string => Boolean(item)))].sort();

  return (
    <section className="page-content">
      <div className="section-heading">
        <div><span className="eyebrow">DATABASE · SPECIFICATIONS · OFFLINE CACHE</span><h2>Ship database</h2></div>
        <div className="data-actions"><span className="confidence">{components.value?.length ?? 0} components cached</span><button className="secondary-button" onClick={() => void catalog.refresh()}>Refresh ships</button></div>
      </div>
      <div className="panel catalog-filters ship-filters">
        <label>SEARCH<input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Partial ship name" /></label>
        <label>MANUFACTURER<select value={manufacturer} onChange={(event) => setManufacturer(event.target.value)}><option value="">All manufacturers</option>{manufacturers.map((item) => <option key={item}>{item}</option>)}</select></label>
        <label>ROLE<select value={role} onChange={(event) => setRole(event.target.value)}><option value="">All roles</option>{roles.map((item) => <option key={item}>{item}</option>)}</select></label>
        <NumberFilter label="MIN CARGO SCU" value={minimumCargo} onChange={setMinimumCargo} />
        <NumberFilter label="MAX CARGO SCU" value={maximumCargo} onChange={setMaximumCargo} />
      </div>
      <ErrorMessage message={catalog.error || components.error} />
      {catalog.loading ? <p className="muted">Loading ship database…</p> : rows.length ? (
        <div className="ship-catalog-grid">{rows.map((ship) => <article className="panel catalog-ship-card" key={ship.id}>
          {ship.image_url && <CachedShipImage url={ship.image_url} name={ship.name} />}
          <div className="ship-top"><div><span className="eyebrow">{ship.manufacturer || "UNKNOWN MANUFACTURER"}</span><h3>{ship.name}</h3></div><span className="confidence">{ship.is_flyable ? "FLYABLE" : "NOT FLYABLE"}</span></div>
          <p className="muted">{ship.description || ship.role || "No description supplied by the catalog source."}</p>
          <div className="spec-grid">
            <span>CARGO<strong>{numberFormat.format(ship.cargo_capacity_scu)} SCU</strong></span>
            <span>CREW<strong>{ship.crew_min}–{ship.crew_max}</strong></span>
            <span>ROLE<strong>{ship.role || "—"}</strong></span>
            <span>PRICE<strong>{ship.price_auec === null ? "—" : `${numberFormat.format(ship.price_auec)} aUEC`}</strong></span>
            <span>QUANTUM FUEL<strong>{ship.quantum_fuel ?? "—"}</strong></span>
            <span>HYDROGEN FUEL<strong>{ship.hydrogen_fuel ?? "—"}</strong></span>
          </div>
          {Object.entries(ship.specifications).length > 0 && <details><summary>Additional specifications</summary><dl>{Object.entries(ship.specifications).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{String(value ?? "—")}</dd></div>)}</dl></details>}
          <button className="primary-button" disabled={ship.cargo_capacity_scu <= 0} onClick={() => onRoutePlan(ship.cargo_capacity_scu, ship.name, ship.id)}>Plan routes with this ship</button>
        </article>)}</div>
      ) : <div className="empty-state"><span>◇</span><h3>No ships match this view</h3><p>Import ship records to build the database. Catalog records remain available offline after synchronization.</p></div>}
    </section>
  );
}

export function FleetManagerPage({
  patch,
  onRoutePlan,
}: {
  patch: string;
  onRoutePlan(capacity: number, shipName: string, shipId?: string): void;
}) {
  const fleets = useLoaded(getFleets, []);
  const ships = useLoaded(() => getCatalogShips(patch), [patch]);
  const [fleetName, setFleetName] = useState("");
  const [description, setDescription] = useState("");
  const [selectedFleetId, setSelectedFleetId] = useState("");
  const [selectedShipId, setSelectedShipId] = useState("");
  const [quantity, setQuantity] = useState(1);
  const [quantityDrafts, setQuantityDrafts] = useState<Record<string, number>>({});
  const [editName, setEditName] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const selectedFleet = fleets.value?.find((item) => item.id === selectedFleetId) ?? fleets.value?.[0] ?? null;
  const reload = async () => {
    await fleets.refresh();
  };
  useEffect(() => {
    if (selectedFleet && selectedFleetId !== selectedFleet.id) setSelectedFleetId(selectedFleet.id);
    if (selectedFleet) {
      setEditName(selectedFleet.fleet_name);
      setEditDescription(selectedFleet.description);
    }
  }, [fleets.value, selectedFleet]);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await action();
      await reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Fleet operation failed.");
    } finally {
      setBusy(false);
    }
  }

  function submitCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void run(async () => {
      const created = await createFleet({ fleet_name: fleetName, description });
      setFleetName("");
      setDescription("");
      setSelectedFleetId(created.id);
    });
  }

  return (
    <section className="page-content">
      <div className="section-heading"><div><span className="eyebrow">PERSISTENT PROFILES · FLEET AGGREGATES</span><h2>Fleet manager</h2></div><button className="secondary-button" onClick={() => void reload()}>Refresh fleets</button></div>
      <ErrorMessage message={error || fleets.error || ships.error} />
      <form className="panel fleet-create-form" onSubmit={submitCreate}>
        <label>FLEET NAME<input required maxLength={120} value={fleetName} onChange={(event) => setFleetName(event.target.value)} placeholder="e.g. Stanton Haulers" /></label>
        <label>DESCRIPTION<input maxLength={4000} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Optional notes" /></label>
        <button className="primary-button" disabled={busy}>Create fleet</button>
      </form>
      {fleets.value?.length ? <>
        <div className="panel fleet-editor">
          <label>SELECT FLEET<select value={selectedFleet?.id ?? ""} onChange={(event) => setSelectedFleetId(event.target.value)}>{fleets.value.map((item) => <option key={item.id} value={item.id}>{item.fleet_name}</option>)}</select></label>
          {selectedFleet && <>
            <label>FLEET NAME<input value={editName} maxLength={120} onChange={(event) => setEditName(event.target.value)} /></label>
            <label>DESCRIPTION<input value={editDescription} maxLength={4000} onChange={(event) => setEditDescription(event.target.value)} /></label>
            <button className="secondary-button" disabled={busy || !editName.trim()} onClick={() => void run(() => updateFleet(selectedFleet.id, { fleet_name: editName, description: editDescription }))}>Save fleet</button>
            <button className="danger-button" disabled={busy} onClick={() => void run(async () => {
              await deleteFleet(selectedFleet.id);
              setSelectedFleetId("");
            })}>Delete fleet</button>
            <button className="primary-button" disabled={!selectedFleet.total_scu} onClick={() => onRoutePlan(selectedFleet.total_scu, selectedFleet.fleet_name)}>Plan with fleet capacity</button>
          </>}
        </div>
        {selectedFleet && <div className="metric-grid">
          <MetricCard label="FLEET CARGO" value={`${numberFormat.format(selectedFleet.total_scu)} SCU`} />
          <MetricCard label="FLEET VALUE" value={`${numberFormat.format(selectedFleet.total_value_auec)} aUEC`} />
          <MetricCard label="VESSEL TYPES" value={String(selectedFleet.ships.length)} />
          <MetricCard label="TOTAL VESSELS" value={String(selectedFleet.ships.reduce((total, entry) => total + entry.quantity, 0))} />
        </div>}
        {selectedFleet && <div className="panel fleet-ships">
          <h3>Fleet vessels</h3>
          {selectedFleet.ships.length ? <div className="table-wrap"><table><thead><tr><th>SHIP</th><th>MANUFACTURER</th><th>QUANTITY</th><th>CAPACITY</th><th>VALUE</th><th></th></tr></thead>
            <tbody>{selectedFleet.ships.map((entry) => <tr key={entry.ship_id}>
              <td>{entry.name}</td><td>{entry.manufacturer || "—"}</td>
              <td><input className="quantity-input" type="number" min="1" max="100000" value={quantityDrafts[entry.ship_id] ?? entry.quantity} onChange={(event) => {
                const next = Number(event.target.value);
                if (Number.isInteger(next) && next > 0) setQuantityDrafts((current) => ({ ...current, [entry.ship_id]: next }));
              }} onBlur={() => {
                const next = quantityDrafts[entry.ship_id];
                if (next !== undefined && next !== entry.quantity) {
                  void run(async () => {
                    const updated = await setFleetShip(selectedFleet.id, entry.ship_id, next);
                    setQuantityDrafts((current) => {
                      const remaining = { ...current };
                      delete remaining[entry.ship_id];
                      return remaining;
                    });
                    return updated;
                  });
                }
              }} /></td>
              <td>{numberFormat.format(entry.cargo_capacity_scu * entry.quantity)} SCU</td>
              <td>{entry.price_auec === null ? "—" : `${numberFormat.format(entry.price_auec * entry.quantity)} aUEC`}</td>
              <td><button className="danger-button" disabled={busy} onClick={() => void run(() => removeFleetShip(selectedFleet.id, entry.ship_id))}>Remove</button></td>
            </tr>)}</tbody></table></div> : <p className="muted">Add ships from the catalog below.</p>}
          <form className="fleet-add-form" onSubmit={(event) => {
            event.preventDefault();
            if (selectedShipId) void run(() => setFleetShip(selectedFleet.id, selectedShipId, quantity));
          }}>
            <label>SHIP<select value={selectedShipId} onChange={(event) => setSelectedShipId(event.target.value)}><option value="">Choose a catalog ship</option>{(ships.value ?? []).map((ship) => <option key={ship.id} value={ship.id}>{ship.name} · {ship.cargo_capacity_scu} SCU</option>)}</select></label>
            <label>QUANTITY<input type="number" min="1" max="100000" value={quantity} onChange={(event) => setQuantity(Number(event.target.value))} /></label>
            <button className="primary-button" disabled={busy || !selectedShipId}>Add or update ship</button>
          </form>
        </div>}
      </> : !fleets.loading && <div className="empty-state"><span>◇</span><h3>No fleet profiles yet</h3><p>Create a fleet, then add ships from the imported ship database.</p></div>}
      <p className="muted">Fleet profiles are stored in this SCIP API database. Your cached ship catalog stays available offline; fleet edits require the local API connection.</p>
    </section>
  );
}

export function DataImportPage() {
  const importers = useLoaded(getCatalogImporters, []);
  const history = useLoaded(getImportHistory, []);
  const [recordType, setRecordType] = useState("ships");
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState("");
  const [busySource, setBusySource] = useState("");
  const [busy, setBusy] = useState(false);
  async function runImport(operation: () => Promise<ImportResult>) {
    setBusy(true);
    setError("");
    setResult(null);
    setBusySource("file");
    try {
      setResult(await operation());
      await history.refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Catalog import failed.");
      await history.refresh();
    } finally {
      setBusy(false);
      setBusySource("");
    }
  }
  async function uploadFile() {
    if (!file) {
      setError("Choose a JSON or CSV file to import.");
      return;
    }
    try {
      const content = await file.text();
      if (file.name.toLowerCase().endsWith(".csv")) {
        await runImport(() => importCatalogCsv(recordType, content));
      } else if (file.name.toLowerCase().endsWith(".json")) {
        await runImport(() => importCatalogJson(content));
      } else {
        setError("Select a .json or .csv file.");
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The selected import file could not be read.");
    }
  }
  return (
    <section className="page-content">
      <div className="section-heading"><div><span className="eyebrow">JSON · CSV · CONFIGURED API SOURCES</span><h2>Catalog data import</h2></div><button className="secondary-button" onClick={() => void history.refresh()}>Refresh history</button></div>
      <div className="panel import-panel">
        <p className="muted">Imports validate records and upsert normalized catalog data. Price snapshots remain time-stamped market history. Proprietary provider responses are not guessed: configured API URLs must return SCIP's normalized JSON envelope.</p>
        <div className="import-file-row">
          <label>JSON OR CSV FILE<input type="file" accept=".json,.csv,application/json,text/csv" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></label>
          <label>CSV RECORD TYPE<select value={recordType} onChange={(event) => setRecordType(event.target.value)}>{["commodities", "locations", "ships", "components", "prices", "ship_components"].map((item) => <option key={item} value={item}>{item.replaceAll("_", " ")}</option>)}</select></label>
          <button className="primary-button" disabled={busy} onClick={() => void uploadFile()}>{busy && busySource === "file" ? "Importing…" : "Import file"}</button>
        </div>
        <details><summary>Normalized JSON format example</summary><pre className="code-sample">{importExample}</pre></details>
        <div className="provider-grid">{(importers.value ?? []).map((source) => <article className="provider-card" key={source.key}>
          <div><strong>{source.name}</strong><small>{source.kind === "api" ? source.configured ? "Configured on this API" : "Set API URL in server environment" : `${source.kind.toUpperCase()} upload enabled`}</small></div>
          {source.kind === "api" && <button className="secondary-button" disabled={!source.configured || busy} onClick={() => {
            setBusy(true);
            setBusySource(source.key);
            setError("");
            setResult(null);
            void importCatalogApi(source.key).then((response) => {
              setResult(response);
              return history.refresh();
            }).catch((cause: unknown) => {
              setError(cause instanceof Error ? cause.message : `${source.name} import failed.`);
              return history.refresh();
            }).finally(() => {
              setBusy(false);
              setBusySource("");
            });
          }}>{busySource === source.key ? "Syncing…" : "Sync now"}</button>}
        </article>)}</div>
      </div>
      {importers.error && <ErrorMessage message={importers.error} />}
      {error && <ErrorMessage message={error} />}
      {result && <div className="ocr-message success" role="status">Import successful · {result.records_seen} rows · {result.created} created · {result.updated} updated · {result.skipped} skipped.</div>}
      <div className="panel import-history"><div className="section-heading"><div><span className="eyebrow">AUDIT TRAIL</span><h2>Import history</h2></div></div>
        {history.error && <ErrorMessage message={history.error} />}
        {!history.value?.length ? <p className="muted">No import attempts have been recorded.</p> : <div className="table-wrap"><table><thead><tr><th>TIME</th><th>SOURCE</th><th>STATUS</th><th>RECORDS</th><th>RESULT</th></tr></thead><tbody>{history.value.map((item: ImportHistoryEntry) => <tr key={item.id}>
          <td>{new Date(item.started_at).toLocaleString()}</td><td>{item.source}</td><td>{item.status}</td><td>{item.records_seen}</td><td>{item.error || `${item.result.created ?? 0} created · ${item.result.updated ?? 0} updated`}</td>
        </tr>)}</tbody></table></div>}
      </div>
    </section>
  );
}

function NumberFilter({ label, value, onChange }: { label: string; value: string; onChange(value: string): void }) {
  return <label>{label}<input type="number" min="0" value={value} onChange={(event) => onChange(event.target.value)} /></label>;
}

function CachedShipImage({ url, name }: { url: string; name: string }) {
  const container = useRef<HTMLDivElement | null>(null);
  const [imageUrl, setImageUrl] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    let objectUrl = "";
    const cacheImage = () => {
      void getCachedImage(url).then((cachedUrl) => {
        objectUrl = cachedUrl;
        if (active) setImageUrl(cachedUrl);
        else URL.revokeObjectURL(cachedUrl);
      }).catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : "Ship image could not be cached.");
      });
    };
    const observer = "IntersectionObserver" in window
      ? new IntersectionObserver((entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          observer?.disconnect();
          cacheImage();
        }
      })
      : null;
    if (observer && container.current) observer.observe(container.current);
    else cacheImage();
    return () => {
      active = false;
      observer?.disconnect();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [url]);
  return <div ref={container} className="ship-image-wrap">
    {error
      ? <div className="ship-image-missing" role="img" aria-label={`${name} image unavailable`}>Image unavailable offline · {error}</div>
      : imageUrl
        ? <img className="ship-image" src={imageUrl} alt={`${name} manufacturer image`} />
        : <div className="ship-image-missing" aria-hidden="true">Image cached when visible</div>}
  </div>;
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return <article className="metric panel"><span className="eyebrow">{label}</span><strong>{value}</strong></article>;
}

function formatMoney(value: number | null): string {
  return value === null ? "—" : numberFormat.format(value);
}
