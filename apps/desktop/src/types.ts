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

export type CatalogCommodity = {
  id: string;
  name: string;
  category: string | null;
  description: string | null;
  legal_status: string | null;
  base_price: number | null;
  current_price: number | null;
  average_price: number | null;
  supply: number | null;
  demand: number | null;
  profit: number | null;
  best_buy_location: string | null;
  best_buy_price: number | null;
  best_sell_location: string | null;
  best_sell_price: number | null;
  confidence: number | null;
  updated_at: string | null;
};

export type CatalogShip = {
  id: string;
  name: string;
  manufacturer: string;
  role: string | null;
  cargo_capacity_scu: number;
  quantum_fuel: number | null;
  hydrogen_fuel: number | null;
  crew_min: number;
  crew_max: number;
  price_auec: number | null;
  price_usd: number | null;
  is_flyable: boolean;
  image_url: string | null;
  description: string | null;
  specifications: Record<string, string | number | boolean | null>;
};

export type CatalogLocation = {
  id: string;
  name: string;
  planet: string | null;
  moon: string | null;
  system: string;
  type: string;
};

export type CatalogComponent = {
  id: string;
  name: string;
  type: string;
  size: number;
  grade: string | null;
  manufacturer: string;
};

export type FleetShipEntry = {
  ship_id: string;
  quantity: number;
  name: string;
  manufacturer: string;
  cargo_capacity_scu: number;
  price_auec: number | null;
};

export type FleetProfile = {
  id: string;
  fleet_name: string;
  description: string;
  ships: FleetShipEntry[];
  total_scu: number;
  total_value_auec: number;
};

export type CatalogImporter = {
  key: string;
  name: string;
  configured: boolean;
  kind: "json" | "csv" | "api";
};

export type ImportHistoryEntry = {
  id: string;
  source: string;
  status: string;
  records_seen: number;
  result: Record<string, number>;
  error: string | null;
  started_at: string;
  completed_at: string | null;
};

export type ImportResult = {
  source: string;
  created: number;
  updated: number;
  skipped: number;
  records_seen: number;
};
