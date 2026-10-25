import type {
  HistoryPoint,
  CatalogCommodity,
  CatalogComponent,
  CatalogImporter,
  CatalogLocation,
  CatalogShip,
  FleetProfile,
  ImportHistoryEntry,
  ImportResult,
  MarketRow,
  OcrReport,
  OcrScanResult,
  Route,
  SourceStatus,
} from "./types";

export const API_BASE = import.meta.env.VITE_SCIP_API_URL || "http://127.0.0.1:8080";
const cacheDatabaseName = "scip-offline-cache";
const cacheStoreName = "responses";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(new URL(path, API_BASE), {
      ...init,
      headers: { Accept: "application/json", ...init?.headers },
    });
  } catch (error) {
    throw new Error(
      `SCIP API is unavailable at ${API_BASE}. Start the local API and try again.`,
      { cause: error },
    );
  }

  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail =
      typeof body === "object" && body !== null && "detail" in body
        ? String(body.detail)
        : `API returned ${response.status}`;
    throw new Error(detail.startsWith(`${response.status}:`)
      ? detail
      : `${response.status}: ${detail}`);
  }
  return body as T;
}

function openCacheDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const opening = indexedDB.open(cacheDatabaseName, 1);
    opening.onupgradeneeded = () => {
      opening.result.createObjectStore(cacheStoreName);
    };
    opening.onsuccess = () => resolve(opening.result);
    opening.onerror = () => reject(opening.error ?? new Error("Offline cache could not be opened."));
    opening.onblocked = () => reject(new Error("Offline cache upgrade is blocked by another SCIP window."));
  });
}

async function readCache<T>(key: string): Promise<T | null> {
  const database = await openCacheDatabase();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(cacheStoreName, "readonly");
    const reading = transaction.objectStore(cacheStoreName).get(key);
    reading.onsuccess = () => resolve((reading.result as T | undefined) ?? null);
    reading.onerror = () => reject(reading.error ?? new Error("Offline cache could not be read."));
    transaction.oncomplete = () => database.close();
    transaction.onerror = () => reject(transaction.error ?? new Error("Offline cache read failed."));
    transaction.onabort = () => reject(transaction.error ?? new Error("Offline cache read was aborted."));
  });
}

async function writeCache<T>(key: string, value: T): Promise<void> {
  const database = await openCacheDatabase();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(cacheStoreName, "readwrite");
    transaction.objectStore(cacheStoreName).put(value, key);
    transaction.oncomplete = () => {
      database.close();
      resolve();
    };
    transaction.onerror = () => reject(transaction.error ?? new Error("Offline cache write failed."));
    transaction.onabort = () => reject(transaction.error ?? new Error("Offline cache write was aborted."));
  });
}

async function clearCache(key: string): Promise<void> {
  const database = await openCacheDatabase();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(cacheStoreName, "readwrite");
    transaction.objectStore(cacheStoreName).delete(key);
    transaction.oncomplete = () => {
      database.close();
      resolve();
    };
    transaction.onerror = () => reject(transaction.error ?? new Error("Offline cache could not be cleared."));
    transaction.onabort = () => reject(transaction.error ?? new Error("Offline cache clear was aborted."));
  });
}

async function clearAllCache(): Promise<void> {
  const database = await openCacheDatabase();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(cacheStoreName, "readwrite");
    transaction.objectStore(cacheStoreName).clear();
    transaction.oncomplete = () => {
      database.close();
      resolve();
    };
    transaction.onerror = () => reject(transaction.error ?? new Error("Offline cache could not be cleared."));
    transaction.onabort = () => reject(transaction.error ?? new Error("Offline cache clear was aborted."));
  });
}

async function requestCached<T>(path: string, cacheKey: string): Promise<T> {
  let response: T;
  try {
    response = await request<T>(path);
  } catch (error) {
    const cached = await readCache<T>(cacheKey);
    if (cached !== null) return cached;
    throw error;
  }
  await writeCache(cacheKey, response);
  return response;
}

export async function getCachedImage(url: string): Promise<string> {
  const cacheKey = `image:${url}`;
  const cached = await readCache<Blob>(cacheKey);
  const blob = cached ?? await (async () => {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`Ship image request failed with HTTP ${response.status}.`);
    const contentType = response.headers.get("content-type") ?? "";
    if (!contentType.startsWith("image/")) throw new Error("Ship image source did not return an image.");
    const image = await response.blob();
    if (image.size > 10 * 1024 * 1024) throw new Error("Ship images larger than 10 MiB are not cached.");
    await writeCache(cacheKey, image);
    return image;
  })();
  return URL.createObjectURL(blob);
}

export function getMarkets(patch: string, shard: string): Promise<MarketRow[]> {
  const query = new URLSearchParams({ patch_version: patch, server_shard: shard });
  return request(`/api/v2/markets?${query}`);
}

export function getCatalogCommodities(patch: string, shard: string): Promise<CatalogCommodity[]> {
  const query = new URLSearchParams({
    patch_version: patch,
    server_shard: shard,
    limit: "5000",
  });
  return requestCached(`/api/v2/catalog/commodities?${query}`, `commodities:${patch}:${shard}`);
}

export function getCatalogShips(patch: string): Promise<CatalogShip[]> {
  const query = new URLSearchParams({ patch_version: patch, limit: "5000" });
  return requestCached(`/api/v2/ships?${query}`, `ships:${patch}`);
}

export function getCatalogLocations(): Promise<CatalogLocation[]> {
  return requestCached("/api/v2/locations?limit=5000", "locations");
}

export function getCatalogComponents(): Promise<CatalogComponent[]> {
  return requestCached("/api/v2/components?limit=5000", "components");
}

export function getFleets(): Promise<FleetProfile[]> {
  return requestCached("/api/v2/fleets", "fleets");
}

export async function createFleet(input: { fleet_name: string; description: string }): Promise<FleetProfile> {
  const fleet = await request<FleetProfile>("/api/v2/fleets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  await clearCache("fleets");
  return fleet;
}

export async function updateFleet(
  fleetId: string,
  input: { fleet_name: string; description: string },
): Promise<FleetProfile> {
  const fleet = await request<FleetProfile>(`/api/v2/fleets/${encodeURIComponent(fleetId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  await clearCache("fleets");
  return fleet;
}

export async function deleteFleet(fleetId: string): Promise<void> {
  await request<void>(`/api/v2/fleets/${encodeURIComponent(fleetId)}`, { method: "DELETE" });
  await clearCache("fleets");
}

export async function setFleetShip(
  fleetId: string,
  shipId: string,
  quantity: number,
): Promise<FleetProfile> {
  const fleet = await request<FleetProfile>(
    `/api/v2/fleets/${encodeURIComponent(fleetId)}/ships`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ship_id: shipId, quantity }),
    },
  );
  await clearCache("fleets");
  return fleet;
}

export async function removeFleetShip(fleetId: string, shipId: string): Promise<void> {
  await request<void>(
    `/api/v2/fleets/${encodeURIComponent(fleetId)}/ships/${encodeURIComponent(shipId)}`,
    { method: "DELETE" },
  );
  await clearCache("fleets");
}

export function getCatalogImporters(): Promise<CatalogImporter[]> {
  return request("/api/v2/importers");
}

export function getImportHistory(): Promise<ImportHistoryEntry[]> {
  return request("/api/v2/imports/history");
}

export function importCatalogJson(json: string): Promise<ImportResult> {
  return request<ImportResult>("/api/v2/imports/json", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: json,
  }).then(async (result) => {
    await clearAllCache();
    return result;
  });
}

export function importCatalogCsv(recordType: string, csv: string): Promise<ImportResult> {
  const query = new URLSearchParams({ record_type: recordType });
  return request<ImportResult>(`/api/v2/imports/csv?${query}`, {
    method: "POST",
    headers: { "Content-Type": "text/csv" },
    body: csv,
  }).then(async (result) => {
    await clearAllCache();
    return result;
  });
}

export function importCatalogApi(source: string): Promise<ImportResult> {
  return request<ImportResult>(`/api/v2/imports/api/${encodeURIComponent(source)}`, { method: "POST" })
    .then(async (result) => {
      await clearAllCache();
      return result;
    });
}

export function getSources(): Promise<SourceStatus[]> {
  return request("/api/v2/sources");
}

export function getRoutes(input: {
  cargo_capacity: number;
  current_location: string;
  available_credits: number;
  fuel_cost: number;
  route_fees: number;
  route_risk: number;
  travel_hours: number;
  minimum_confidence: number;
  server_shard: string;
  patch_version: string;
}): Promise<{ routes: Route[]; algorithm_version: string }> {
  return request("/api/v2/routes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}

export function getHistory(input: {
  commodity: string;
  location: string;
  patch_version: string;
  server_shard: string;
  days: number;
}): Promise<HistoryPoint[]> {
  const query = new URLSearchParams({
    ...input,
    days: String(input.days),
  });
  return request(`/api/v2/history?${query}`);
}

export function scanOcrImage(imageBase64: string): Promise<OcrScanResult> {
  return request("/api/ocr/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ image_base64: imageBase64 }),
  });
}

export function submitOcrReport(report: OcrReport): Promise<{
  status: "accepted";
  confirmation: Record<string, unknown>;
}> {
  return request("/api/ocr-submit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(report),
  });
}
