import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import {
  API_BASE,
  getCatalogShips,
  getHistory,
  getMarkets,
  getRoutes,
  getSources,
  scanOcrImage,
  submitOcrReport,
} from "./api";
import {
  CommodityBrowserPage,
  DataImportPage,
  FleetManagerPage,
  ShipBrowserPage,
} from "./DataPages";
import type {
  HistoryPoint,
  CatalogShip,
  MarketRow,
  OcrCommodity,
  OcrReport,
  OcrScanResult,
  PendingOcrUpload,
  Route,
  Ship,
  SourceStatus,
} from "./types";

type Page = "Dashboard" | "Commodities" | "Ships" | "Routes" | "Fleet" | "OCR Upload" | "Analytics" | "Replay" | "Data Import" | "Settings";
type Theme = "dark" | "light";

const pages: Page[] = [
  "Dashboard",
  "Commodities",
  "Ships",
  "Routes",
  "Fleet",
  "OCR Upload",
  "Analytics",
  "Replay",
  "Data Import",
  "Settings",
];
const pageIcons = ["◈", "▦", "◇", "⌁", "⌘", "⌗", "▤", "◷", "⇧", "⚙"];
const ocrQueueKey = "scip.ocr.queue";
const money = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
function readOcrQueue(): { items: PendingOcrUpload[]; error: string } {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(ocrQueueKey) || "[]");
    if (Array.isArray(saved) && saved.every(isPendingOcrUpload)) {
      return { items: saved, error: "" };
    }
    return { items: [], error: "The saved OCR queue is invalid. Its local data was left untouched." };
  } catch {
    return { items: [], error: "The saved OCR queue could not be read. Its local data was left untouched." };
  }
}

function readShips(): Ship[] {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem("scip.fleet") || "null");
    if (Array.isArray(saved) && saved.every(isShip)) return saved;
  } catch {
    localStorage.removeItem("scip.fleet");
  }
  return [];
}

function readTheme(): Theme {
  return localStorage.getItem("scip.theme") === "light" ? "light" : "dark";
}

function marketPrice(values: MarketRow["buy"]): number | null {
  if (!values.length) return null;
  return values.reduce((total, value) => total + value.value, 0) / values.length;
}

function isPendingOcrUpload(value: unknown): value is PendingOcrUpload {
  if (typeof value !== "object" || value === null) return false;
  const item = value as Record<string, unknown>;
  return typeof item.id === "string"
    && typeof item.created_at === "string"
    && typeof item.last_error === "string"
    && typeof item.report === "object"
    && item.report !== null;
}

function imageFileToBase64(file: File): Promise<string> {
  if (file.size > 20 * 1024 * 1024) {
    return Promise.reject(new Error("Screenshots must be 20 MiB or smaller."));
  }
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("The screenshot file could not be read."));
    reader.onload = () => {
      if (typeof reader.result !== "string") {
        reject(new Error("The screenshot could not be encoded."));
        return;
      }
      resolve(reader.result.slice(reader.result.indexOf(",") + 1));
    };
    reader.readAsDataURL(file);
  });
}

function captureScreenAsBase64(): Promise<string> {
  if (!navigator.mediaDevices?.getDisplayMedia) {
    return Promise.reject(new Error("Screen capture is not available in this webview. Upload a screenshot file instead."));
  }
  return navigator.mediaDevices.getDisplayMedia({ video: true, audio: false }).then(async (stream) => {
    const video = document.createElement("video");
    video.srcObject = stream;
    try {
      await new Promise<void>((resolve, reject) => {
        video.onloadedmetadata = () => resolve();
        video.onerror = () => reject(new Error("The selected screen could not be read."));
      });
      await video.play();
      await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()));
      const canvas = document.createElement("canvas");
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      const context = canvas.getContext("2d");
      if (!context || canvas.width < 200 || canvas.height < 200) {
        throw new Error("The selected screen is too small to scan.");
      }
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise<Blob>((resolve, reject) => {
        canvas.toBlob((image) => image ? resolve(image) : reject(new Error("Could not capture the selected screen.")), "image/jpeg", 0.92);
      });
      return imageFileToBase64(new File([blob], "screen-capture.jpg", { type: "image/jpeg" }));
    } finally {
      stream.getTracks().forEach((track) => track.stop());
      video.srcObject = null;
    }
  });
}

function reportValidationError(report: OcrReport): string {
  if (!report.location.trim()) return "Enter the market location before uploading.";
  if (!report.patch_version.trim() || !report.server_shard.trim()) return "Patch and server shard are required.";
  if (!Number.isFinite(report.ocr_confidence) || report.ocr_confidence < 0 || report.ocr_confidence > 1) {
    return "OCR confidence must be between 0 and 1.";
  }
  if (report.commodities.length === 0 || report.commodities.length > 500) {
    return "The scan must contain between 1 and 500 commodity rows.";
  }
  for (const [index, row] of report.commodities.entries()) {
    if (!row.commodity.trim() || row.commodity.length > 160) {
      return `Commodity row ${index + 1} needs a name no longer than 160 characters.`;
    }
    const values = [row.buy, row.sell, row.stock, row.demand];
    if (values.every((value) => value === null)) return `${row.commodity} has no numeric market value.`;
    if (values.some((value) => value !== null && (!Number.isInteger(value) || value < 0 || value > 1_000_000_000))) {
      return `${row.commodity} has an invalid price, supply, or demand value.`;
    }
  }
  return "";
}

function formatOcrFailure(cause: unknown, fallback: string): string {
  const message = cause instanceof Error ? cause.message : fallback;
  return /(?:^|\b)(400|413|422):|validation error/i.test(message)
    ? `Data Validation Error: ${message}`
    : `Upload Failed · ${message}`;
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
  const [ships, setShips] = useState(readShips);
  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const [historyCommodity, setHistoryCommodity] = useState("");
  const [historyLocation, setHistoryLocation] = useState("");
  const [days, setDays] = useState(7);
  const [capacity, setCapacity] = useState(1);
  const [fuelCost, setFuelCost] = useState(0);
  const [fees, setFees] = useState(0);
  const [risk, setRisk] = useState(0.25);
  const [travelHours, setTravelHours] = useState(1);
  const [minimumConfidence, setMinimumConfidence] = useState(0.5);
  const [currentLocation, setCurrentLocation] = useState("");
  const [routeShipLabel, setRouteShipLabel] = useState("");
  const [availableCredits, setAvailableCredits] = useState(100000);
  const [selectedShipId, setSelectedShipId] = useState("");
  const [catalogShips, setCatalogShips] = useState<CatalogShip[]>([]);
  const [catalogShipsError, setCatalogShipsError] = useState("");
  const [backupStatus, setBackupStatus] = useState("");
  const [ocrScan, setOcrScan] = useState<OcrScanResult | null>(null);
  const [ocrLocation, setOcrLocation] = useState("");
  const [autoOcrUpload, setAutoOcrUpload] = useState(
    () => localStorage.getItem("scip.ocr.autoUpload") === "true",
  );
  const [ocrMinimumConfidence, setOcrMinimumConfidence] = useState(() => {
    const value = Number(localStorage.getItem("scip.ocr.minimumConfidence") || "0.75");
    return Number.isFinite(value) && value >= 0 && value <= 1 ? value : 0.75;
  });
  const [ocrMessage, setOcrMessage] = useState("F9 captures the selected screen while SCIP is focused. You can also upload a screenshot.");
  const [ocrBusy, setOcrBusy] = useState(false);
  const [ocrUploadBusy, setOcrUploadBusy] = useState(false);
  const [ocrQueueState, setOcrQueueState] = useState(readOcrQueue);
  const ocrQueueRef = useRef(ocrQueueState);
  const pendingOcrUploads = ocrQueueState.items;

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
    let active = true;
    void getCatalogShips(patch.trim()).then((rows) => {
      if (active) {
        setCatalogShips(rows);
        setCatalogShipsError("");
      }
    }).catch((cause: unknown) => {
      if (active) setCatalogShipsError(cause instanceof Error ? cause.message : "Ship catalog is unavailable.");
    });
    return () => {
      active = false;
    };
  }, [patch]);

  useEffect(() => {
    localStorage.setItem("scip.fleet", JSON.stringify(ships));
  }, [ships]);

  useEffect(() => {
    localStorage.setItem("scip.notifications", String(notificationsEnabled));
  }, [notificationsEnabled]);

  useEffect(() => {
    localStorage.setItem("scip.ocr.autoUpload", String(autoOcrUpload));
    localStorage.setItem("scip.ocr.minimumConfidence", String(ocrMinimumConfidence));
  }, [autoOcrUpload, ocrMinimumConfidence]);

  useEffect(() => {
    localStorage.setItem("scip.patch", patch);
    localStorage.setItem("scip.shard", shard);
  }, [patch, shard]);

  const uniqueLocations = useMemo(
    () => [...new Set(markets.map((row) => row.location))].sort(),
    [markets],
  );

  function saveOcrQueue(items: PendingOcrUpload[]) {
    try {
      localStorage.setItem(ocrQueueKey, JSON.stringify(items));
      const next = { items, error: "" };
      ocrQueueRef.current = next;
      setOcrQueueState(next);
      return true;
    } catch {
      setOcrQueueState((current) => ({
        ...current,
        error: "Could not save the OCR upload queue locally. Upload was not attempted.",
      }));
      return false;
    }
  }

  function makeOcrReport(scan: OcrScanResult): OcrReport {
    return {
      client_report_id: crypto.randomUUID(),
      location: ocrLocation.trim(),
      patch_version: patch.trim(),
      server_shard: shard.trim(),
      observed_at: new Date().toISOString(),
      ocr_confidence: scan.quality.ocr_confidence,
      source: "player-scan",
      commodities: scan.commodities.map((row) => ({
        commodity: row.commodity,
        buy: row.buy,
        sell: row.sell,
        stock: row.stock,
        demand: row.demand,
        ...(row.demand_label ? { demand_label: row.demand_label } : {}),
      })),
      inventory: [],
    };
  }

  async function sendOcrReport(report: OcrReport, queuedId?: string) {
    const validationError = reportValidationError(report);
    if (validationError) {
      setOcrMessage(`Data Validation Error: ${validationError}`);
      return;
    }
    if (!queuedId) {
      const queueItem: PendingOcrUpload = {
        id: report.client_report_id,
        report,
        created_at: new Date().toISOString(),
        last_error: "",
      };
      if (!saveOcrQueue([...ocrQueueRef.current.items, queueItem])) return;
      queuedId = queueItem.id;
    }
    setOcrUploadBusy(true);
    setOcrMessage("Uploading scan to SC-Truckers…");
    try {
      const response = await submitOcrReport(report);
      saveOcrQueue(ocrQueueRef.current.items.filter((item) => item.id !== queuedId));
      setOcrMessage(`Upload Successful · ${JSON.stringify(response.confirmation)}`);
      void refresh();
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "The upload failed.";
      const updatedQueue = ocrQueueRef.current.items.map((item) =>
        item.id === queuedId ? { ...item, last_error: message } : item,
      );
      saveOcrQueue(updatedQueue);
      setOcrMessage(message.toLowerCase().includes("422") || message.toLowerCase().includes("validation")
        ? `Data Validation Error: ${message}`
        : `Upload Failed · ${message}. The scan is saved in the local retry queue.`);
    } finally {
      setOcrUploadBusy(false);
    }
  }

  async function processOcrImage(imageBase64: string) {
    setOcrBusy(true);
    setOcrScan(null);
    setOcrMessage("Extracting and checking market values…");
    try {
      const result = await scanOcrImage(imageBase64);
      setOcrScan(result);
      if (!result.commodities.length) {
        const ocrError = result.diagnostics?.errors?.[0]?.error;
        setOcrMessage(ocrError
          ? `Data Validation Error: OCR did not complete: ${ocrError}`
          : "Data Validation Error: No commodity rows were recognized. Try another screenshot.");
      } else if (autoOcrUpload) {
        if (result.quality.ocr_confidence < ocrMinimumConfidence) {
          setOcrMessage(`Data Validation Error: OCR confidence ${Math.round(result.quality.ocr_confidence * 100)}% is below the automatic upload threshold. Review and upload manually.`);
        } else {
          await sendOcrReport(makeOcrReport(result));
        }
      } else {
        setOcrMessage(`OCR complete · ${result.commodities.length} rows · ${Math.round(result.quality.ocr_confidence * 100)}% confidence. Review before uploading.`);
      }
    } catch (cause) {
      setOcrMessage(formatOcrFailure(cause, "OCR processing failed."));
    } finally {
      setOcrBusy(false);
    }
  }

  const captureAndScan = useCallback(async () => {
    setPage("OCR Upload");
    if (ocrBusy || ocrUploadBusy) return;
    try {
      await processOcrImage(await captureScreenAsBase64());
    } catch (cause) {
      setOcrMessage(cause instanceof Error ? cause.message : "Screen capture failed.");
    }
  }, [ocrBusy, ocrUploadBusy, autoOcrUpload, ocrMinimumConfidence, ocrLocation, patch, shard]);

  useEffect(() => {
    const onHotkey = (event: KeyboardEvent) => {
      if (event.key === "F9" && !event.repeat) {
        event.preventDefault();
        void captureAndScan();
      }
    };
    window.addEventListener("keydown", onHotkey);
    return () => window.removeEventListener("keydown", onHotkey);
  }, [captureAndScan]);

  async function uploadScreenshotFile(file: File | undefined) {
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      setOcrMessage("Data Validation Error: Choose a screenshot image file.");
      return;
    }
    setOcrBusy(true);
    try {
      await processOcrImage(await imageFileToBase64(file));
    } catch (cause) {
      setOcrMessage(formatOcrFailure(cause, "Screenshot OCR failed."));
    } finally {
      setOcrBusy(false);
    }
  }

  async function retryOcrUploads() {
    for (const item of ocrQueueRef.current.items) {
      if (ocrUploadBusy) return;
      await sendOcrReport(item.report, item.id);
    }
  }

  function updateOcrCommodity(index: number, field: keyof OcrCommodity, value: string) {
    setOcrScan((current) => current ? {
      ...current,
      commodities: current.commodities.map((row, rowIndex) => {
        if (rowIndex !== index) return row;
        if (field === "commodity") return { ...row, commodity: value };
        if (field === "demand_label") return { ...row, demand_label: value || null };
        return { ...row, [field]: value === "" ? null : Number(value) };
      }),
    } : current);
  }

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

  function downloadBackup() {
    const backup = {
      schemaVersion: 1,
      exportedAt: new Date().toISOString(),
      theme,
      notificationsEnabled,
      patch,
      shard,
      ships,
      autoOcrUpload,
      ocrMinimumConfidence,
    };
    const link = document.createElement("a");
    const url = URL.createObjectURL(new Blob([JSON.stringify(backup, null, 2)], { type: "application/json" }));
    link.href = url;
    link.download = `scip-settings-${new Date().toISOString().slice(0, 10)}.json`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    setBackupStatus("Settings and local ship preference backup downloaded. Fleet profiles remain in the API database.");
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
      if ("autoOcrUpload" in backup && typeof backup.autoOcrUpload === "boolean") {
        setAutoOcrUpload(backup.autoOcrUpload);
      }
      if (
        "ocrMinimumConfidence" in backup &&
        typeof backup.ocrMinimumConfidence === "number" &&
        Number.isFinite(backup.ocrMinimumConfidence) &&
        backup.ocrMinimumConfidence >= 0 &&
        backup.ocrMinimumConfidence <= 1
      ) {
        setOcrMinimumConfidence(backup.ocrMinimumConfidence);
      }
      setBackupStatus("Settings and local ship preferences restored.");
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
              <span className="nav-icon">{pageIcons[index]}</span>
              {item}
              {item === "Routes" && routes.length > 0 && <span className="nav-count">{routes.length}</span>}
              {item === "OCR Upload" && pendingOcrUploads.length > 0 && <span className="nav-count">{pendingOcrUploads.length}</span>}
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
            <div className="empty-hint">SCIP can capture, validate, and submit market OCR directly to SC-Truckers. The website remains the source of truth; synchronized market values appear here after the platform accepts and reviews reports.</div>
          </section>
        )}

        {page === "Commodities" && (
          <CommodityBrowserPage patch={patch.trim()} shard={shard.trim()} />
        )}

        {page === "Ships" && (
          <ShipBrowserPage
            patch={patch.trim()}
            onRoutePlan={(shipCapacity, shipName, shipId) => {
              setCapacity(shipCapacity);
              setSelectedShipId(shipId ?? "");
              setRouteShipLabel(shipName);
              setPage("Routes");
            }}
          />
        )}

        {page === "OCR Upload" && (
          <section className="page-content">
            <div className="section-heading">
              <div><span className="eyebrow">SCREEN CAPTURE · REVIEW · SUBMIT</span><h2>OCR uploader</h2></div>
              <span className="eyebrow">HOTKEY F9</span>
            </div>
            <div className="panel ocr-controls">
              <div className="ocr-actions">
                <button className="primary-button" disabled={ocrBusy || ocrUploadBusy} onClick={() => void captureAndScan()}>Capture screen and scan</button>
                <label className="secondary-button file-button">Upload screenshot<input type="file" accept="image/*" disabled={ocrBusy || ocrUploadBusy} onChange={(event) => {
                  void uploadScreenshotFile(event.target.files?.[0]);
                  event.currentTarget.value = "";
                }} /></label>
                <button className="secondary-button" disabled={!pendingOcrUploads.length || ocrUploadBusy} onClick={() => void retryOcrUploads()}>Retry failed uploads ({pendingOcrUploads.length})</button>
              </div>
              <div className="ocr-context">
                <label>MARKET LOCATION<input required maxLength={160} placeholder="e.g. Area18" value={ocrLocation} onChange={(event) => setOcrLocation(event.target.value)} /></label>
                <label>PATCH<input maxLength={80} value={patch} onChange={(event) => setPatch(event.target.value)} /></label>
                <label>SERVER SHARD<input maxLength={100} value={shard} onChange={(event) => setShard(event.target.value)} /></label>
              </div>
              <label className="setting-toggle"><input type="checkbox" checked={autoOcrUpload} onChange={(event) => setAutoOcrUpload(event.target.checked)} /> Automatically submit scans that pass validation and confidence checks</label>
              {autoOcrUpload && <label className="confidence-threshold">Minimum OCR confidence: {Math.round(ocrMinimumConfidence * 100)}%<input type="range" min="0.5" max="1" step="0.05" value={ocrMinimumConfidence} onChange={(event) => setOcrMinimumConfidence(Number(event.target.value))} /></label>}
              <p className="muted">Automatic uploads remain disabled by default. Every scan is validated locally; the SC-Truckers website remains the source of truth and applies its own review process.</p>
              <div className={`ocr-message ${ocrMessage.startsWith("Upload Successful") ? "success" : ocrMessage.startsWith("Data Validation Error") ? "validation" : ocrMessage.startsWith("Upload Failed") ? "failed" : ""}`} role="status" aria-live="polite">{ocrBusy ? "Scanning screenshot…" : ocrUploadBusy ? "Submitting scan…" : ocrMessage}</div>
              {ocrQueueState.error && <div className="error-banner" role="alert">{ocrQueueState.error}</div>}
            </div>

            {ocrScan && (
              <div className="panel ocr-review">
                <div className="section-heading">
                  <div><span className="eyebrow">OCR REVIEW · {ocrScan.layout.toUpperCase()} LAYOUT</span><h2>{ocrScan.commodities.length} extracted commodity rows</h2></div>
                  <div className="confidence">CONFIDENCE {Math.round(ocrScan.quality.ocr_confidence * 100)}% · {ocrScan.quality.ocr_passes} PASSES</div>
                </div>
                {ocrScan.quality.warnings.length > 0 && <ul className="ocr-warnings">{ocrScan.quality.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>}
                <div className="table-wrap"><table className="ocr-table"><thead><tr><th>COMMODITY</th><th>BUY</th><th>SELL</th><th>SUPPLY</th><th>DEMAND</th></tr></thead><tbody>{ocrScan.commodities.map((row, index) => (
                  <tr key={`${row.commodity}-${index}`}>
                    <td><input aria-label={`Commodity ${index + 1}`} maxLength={160} value={row.commodity} onChange={(event) => updateOcrCommodity(index, "commodity", event.target.value)} /></td>
                    {(["buy", "sell", "stock", "demand"] as const).map((field) => <td key={field}><input aria-label={`${field} for ${row.commodity}`} type="number" min="0" max="1000000000" step="1" value={row[field] ?? ""} onChange={(event) => updateOcrCommodity(index, field, event.target.value)} /></td>)}
                  </tr>
                ))}</tbody></table></div>
                <button className="primary-button" disabled={ocrBusy || ocrUploadBusy} onClick={() => void sendOcrReport(makeOcrReport(ocrScan))}>Review complete · upload scan</button>
              </div>
            )}

            <div className="panel ocr-queue">
              <div className="section-heading"><div><span className="eyebrow">OFFLINE CACHE · RETRIES</span><h2>Upload queue</h2></div><span className="confidence">{pendingOcrUploads.length} pending</span></div>
              {pendingOcrUploads.length === 0 ? <p className="muted">No pending uploads. Failed network submissions are cached locally for retry.</p> : (
                <ul>{pendingOcrUploads.map((item) => <li key={item.id}>
                  <div><strong>{item.report.location || "Location required"}</strong><small>{item.report.commodities.length} rows · {new Date(item.created_at).toLocaleString()}</small>{item.last_error && <small className="queue-error">{item.last_error}</small>}</div>
                  <div className="queue-actions"><button className="secondary-button" disabled={ocrUploadBusy} onClick={() => void sendOcrReport(item.report, item.id)}>Retry</button><button className="danger-button" disabled={ocrUploadBusy} onClick={() => saveOcrQueue(ocrQueueRef.current.items.filter((queued) => queued.id !== item.id))}>Remove</button></div>
                </li>)}</ul>
              )}
            </div>
          </section>
        )}

        {page === "Routes" && (
          <section className="page-content">
            <div className="section-heading"><div><span className="eyebrow">MULTI-FACTOR OPTIMIZATION</span><h2>Route planner</h2></div></div>
            <form className="panel route-form" onSubmit={calculateRoutes}>
              <label>Current location<select value={currentLocation} onChange={(event) => setCurrentLocation(event.target.value)}><option value="">Any market</option>{uniqueLocations.map((location) => <option key={location}>{location}</option>)}</select></label>
              <label>Ship<select value={selectedShipId} onChange={(event) => {
                const ship = catalogShips.find((item) => item.id === event.target.value);
                setSelectedShipId(event.target.value);
                if (ship) {
                  setCapacity(ship.cargo_capacity_scu);
                  setRouteShipLabel(ship.name);
                } else {
                  setRouteShipLabel("");
                }
              }}><option value="">{routeShipLabel || "Select catalog ship or enter capacity"}</option>{catalogShips.map((ship) => <option key={ship.id} value={ship.id}>{ship.name} · {ship.cargo_capacity_scu} SCU</option>)}</select></label>
              {catalogShipsError && <div className="route-catalog-error" role="status">{catalogShipsError}</div>}
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
          <FleetManagerPage
            patch={patch.trim()}
            onRoutePlan={(fleetCapacity, fleetName) => {
              setCapacity(fleetCapacity);
              setSelectedShipId("");
              setRouteShipLabel(fleetName);
              setPage("Routes");
            }}
          />
        )}

        {page === "Data Import" && (
          <DataImportPage />
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
            <div className="panel"><span className="eyebrow">FLEET SETTINGS</span><h2>Catalog fleet profiles</h2><p>Fleet profiles, vessels, capacity, and value totals are saved in the local API database.</p><button className="secondary-button" onClick={() => setPage("Fleet")}>Manage fleet</button></div>
            <div className="panel"><span className="eyebrow">MARKET SETTINGS</span><h2>Market context</h2><label>PATCH<input value={patch} maxLength={80} onChange={(event) => setPatch(event.target.value)} /></label><label>SERVER SHARD<input value={shard} maxLength={100} onChange={(event) => setShard(event.target.value)} /></label></div>
            <div className="panel"><span className="eyebrow">DATA SOURCES</span><h2>Market connection</h2><p>Local API: <code>{API_BASE}</code></p><SourceList sources={sources} /></div>
            <div className="panel"><span className="eyebrow">BACKUP AND RESTORE</span><h2>Local preferences</h2><p className="muted">Export or restore theme, market context, notification preference, and legacy local ship preferences. Fleet profiles remain in the API database.</p><button className="secondary-button" onClick={downloadBackup}>Download backup</button><label>RESTORE BACKUP<input type="file" accept=".json,application/json" onChange={(event) => void restoreBackup(event.target.files?.[0])} /></label>{backupStatus && <small role="status">{backupStatus}</small>}</div>
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
