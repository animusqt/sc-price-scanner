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
