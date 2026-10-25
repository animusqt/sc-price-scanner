# SC Price Scanner Version 2

## Scope and repository boundary

Version 2 is intended to grow the existing desktop scanner into a player-powered
commodity intelligence platform. This repository currently contains the
Electron desktop overlay, Python capture/OCR engine, and example website
integration files. It does not contain the hosted Lovable website, its
Supabase project, credentials, or a production API. Server-side and website
changes must be integrated in those projects; this repository must not pretend
that local JSON files have been uploaded or verified.

The supplied `SCIP_Master_Architecture_Specification.docx` has headings for
architecture areas, but its requirement text repeats the same generic sentence
instead of defining behavior or interfaces. `StarCitizen_Commodity_Platform_Blueprint.docx`
provides the actual high-level direction used here. This document records
concrete contracts to agree on when the hosted application is available.

## V2 foundations in this repository

`scanner-engine/sc_analytics.py` provides dependency-free, testable primitives:

- Report confidence combines OCR quality, reporter reputation, and exponential
  recency decay. Patch or shard mismatches receive no weight.
- Cross-player numeric consensus uses a weighted median and returns an explicit
  confidence/support measure. Reports from different patches or shards are
  never combined.
- Compatible-context price outliers can be flagged with their reference median
  and deviation for human review; they are not silently discarded.
- Route recommendations account for buy price, sell price, finite stock, cargo
  capacity, fuel, fees, route risk, confidence, and travel time. Results expose
  gross profit, net profit, and risk-adjusted profit per hour separately.
- Inventory depletion estimates require multiple fresh snapshots from the
  same station, commodity, shard, and patch. An estimate is not presented when
  evidence is stale or its context changes.

These are analytics building blocks, not a hosted service, verified consensus,
or a promise that a route is safe or profitable in-game.

## Proposed data model

The hosted platform should retain reports as immutable observations and derive
current market views from them. Suggested entities:

| Entity | Important fields |
|---|---|
| `users` | auth subject, display name, account state, created time |
| `locations` | stable id, name, system, type, parent location |
| `commodities` | stable id, canonical name, aliases, active state |
| `reports` | id, submitter, client report id, location, shard, patch, observed time, received time, review state |
| `market_observations` | report id, commodity, buy, sell, stock, demand, tab, OCR quality |
| `inventory_snapshots` | report id, commodity, quantity, optional profit direction |
| `confidence_scores` | observation id, algorithm version, score components, calculated time |
| `route_history` | user, route inputs, estimated costs/risk, result, calculation version |
| `patch_versions` | stable patch id, release label, effective interval |
| `server_shards` | stable shard id, region/label, lifecycle state |
| `audit_events` | actor, action, target, safe metadata, timestamp |

Use database constraints for non-negative prices/stock, valid ranges, foreign
keys, and idempotent client report ids. Never merge reports across shard or
patch boundaries. Keep original observations so recalculating confidence does
not rewrite the evidence.

## Proposed versioned API

- `POST /api/v2/reports`: authenticated, idempotent player report submission.
- `GET /api/v2/markets`: current derived observations with age, patch, shard,
  confidence, and review status.
- `GET /api/v2/commodities`, `/locations`, and `/routes`: reference data and
  route recommendations with explicit input assumptions.
- `GET /api/v2/history`: time-bounded observation history for one commodity,
  location, patch, and shard.
- `POST /api/v2/telemetry`: opt-in, minimized client diagnostics; no screenshots
  or personal data by default.
- A live feed may be added only after authentication, authorization, rate
  limits, and reconnect semantics are defined.

All endpoints should use a documented schema version, request limits, explicit
validation errors, pagination, and structured error responses. The desktop
client should upload only after user action and should report upload failures
without deleting or marking local files as submitted.

## Trust, confidence, and safety

- A local OCR scan is a player report, not confirmed truth. Keep reports
  reviewable and preserve their source and timestamps.
- Compute report confidence from OCR quality, reporter reputation, and
  recency; expose components and the algorithm version.
- Aggregate only compatible shard/patch observations. Use robust aggregation
  and flag outliers for review rather than silently deleting them.
- Require independent evidence before marking a market value verified.
- Keep raw screenshots local unless the player explicitly opts in to upload
  one for review; redact or avoid unrelated screen content.
- Use the site's existing identity provider, server-side authorization,
  transport encryption, secret storage, audit events, and per-user/IP rate
  limits. Never embed a privileged database key in Electron or browser code.

## Delivery sequence

1. **Client analytics foundation**: confidence, consensus, route scoring,
   inventory trends, and deterministic tests. Implemented in this branch.
2. **Hosted API and schema**: implement in the actual website/backend
   repository, with migrations, authentication, moderation states,
   idempotency, audit events, and integration tests.
3. **Desktop submission**: add an explicit preview/confirm/upload workflow,
   retries, and a local submission queue once the API contract is deployed.
4. **Market dashboard**: show freshness, confidence, patch/shard context,
   confirmation status, and route assumptions in the website.
5. **Prediction and live feed**: add only after sufficient compatible history
   exists; evaluate forecasts against held-out observations and publish their
   uncertainty.
6. **Operations and extensions**: monitoring, backups, incident handling,
   Discord integration, and mobile clients after the core service is stable.

The hosted API, database, live feed, Discord bot, mobile app, and actual website
dashboard are not implemented by this repository's current V2 foundation.
They require the corresponding deployment target, credentials, and product
contracts rather than a desktop-only code change.
