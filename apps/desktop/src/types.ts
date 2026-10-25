export type MarketValue = {
  value: number;
  confidence: number;
  latest_observed_at?: string;
  review_status?: string;
};

export type MarketRow = {
  location: string;
  commodity: string;
  patch_version: string;
  server_shard: string;
  buy: MarketValue[];
  sell: MarketValue[];
  stock: MarketValue[];
  demand: MarketValue[];
};

export type Route = {
  origin: string;
  destination: string;
  commodity: string;
  quantity: number;
  gross_profit: number;
  risk_adjusted_profit: number;
  risk_adjusted_profit_per_hour: number;
  confidence: number;
  score?: number;
  bug_risk_penalty?: number;
  travel_hours?: number;
};

export type SourceStatus = {
  key: string;
  name: string;
  configured: boolean;
  documentation_configured: boolean;
  enabled: boolean;
  reason: string;
};

export type HistoryPoint = {
  observed_at: string;
  buy: number | null;
  sell: number | null;
  stock: number | null;
  demand: number | null;
  ocr_confidence: number;
  review_status: string;
};

export type Ship = {
  id: string;
  name: string;
  capacity: number;
  favorite: boolean;
  notes: string;
};

export type OcrCommodity = {
  commodity: string;
  buy: number | null;
  sell: number | null;
  stock: number | null;
  demand: number | null;
  demand_label?: string | null;
  layout?: string;
};

export type OcrScanResult = {
  source: string;
  layout: string;
  tab: string | null;
  commodities: OcrCommodity[];
  inventory: { commodity: string; quantity: number; profit?: string | null }[];
  quality: {
    ocr_confidence: number;
    review_recommended: boolean;
    warnings: string[];
    ocr_passes: number;
  };
  diagnostics?: { errors?: { error: string }[] };
  requires_user_confirmation: boolean;
};

export type OcrReport = {
  client_report_id: string;
  location: string;
  patch_version: string;
  server_shard: string;
  observed_at: string;
  ocr_confidence: number;
  source: "player-scan";
  commodities: OcrCommodity[];
  inventory: { commodity: string; quantity: number; profit_direction?: "up" | "down" }[];
};

export type PendingOcrUpload = {
  id: string;
  report: OcrReport;
  created_at: string;
  last_error: string;
};
