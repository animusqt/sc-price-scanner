import type {
  HistoryPoint,
  MarketRow,
  OcrReport,
  OcrScanResult,
  Route,
  SourceStatus,
} from "./types";

export const API_BASE = import.meta.env.VITE_SCIP_API_URL || "http://127.0.0.1:8080";

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

export function getMarkets(patch: string, shard: string): Promise<MarketRow[]> {
  const query = new URLSearchParams({ patch_version: patch, server_shard: shard });
  return request(`/api/v2/markets?${query}`);
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
