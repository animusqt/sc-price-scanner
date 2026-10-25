# Version 2 status

Version 2 extends the local scanner toward a player-powered Star Citizen
commodity intelligence platform. The repository now contains a runnable
service and dashboard foundation, but it is not a hosted production service:
real Discord credentials, a deployed server, operational monitoring, and
authorized third-party data contracts are still required.

## Implemented foundations

| Build area | Current state |
|---|---|
| Repository and shared contract | API, OCR service, dashboard, launcher copy, shared JSON report schema, CI and deployment files are in this repository. |
| Database | SQLAlchemy model and Alembic initial migration cover users, devices, locations, commodities, patches, shards, reports, observations, inventory, confidence, route history/penalties, bugs, audit events, refresh sessions, telemetry, forecasts, supporters, WebSocket tickets, and ingestion status. |
| Sign-in and authorization | Discord OAuth, guild/optional-role verification, signed short-lived access tokens, hashed rotating refresh tokens, moderator allow-list, and device registration. Requires operator-provided Discord configuration. |
| Player reports and market API | Strict, bounded, idempotent report submission; per-commodity observation timestamps; reports await moderation; market views preserve patch, shard, confidence, and review context. |
| OCR service | Bounded screenshot input and OCR result endpoint. Results require player confirmation and are never uploaded as market reports automatically. |
| Live feed | Short-lived, one-use authenticated WebSocket tickets and in-process market report notifications. Run one API instance until a shared pub/sub transport is configured. |
| Routes and trends | Context-matched route recommendations use confirmed, fresh reports. Stock depletion and linear forecast endpoints expose uncertainty and require sufficiently recent evidence. |
| Bug intelligence | Community bug reports, moderator decisions, and review-linked route-risk penalties. Resolving or rejecting a bug deactivates its penalties. |
| Dashboard and report import | Responsive web dashboard, Discord sign-in entry point, market display, and local scan JSON preview/explicit submission. It does not modify the scanner’s local file. |
| Security and operations | Request validation, rate limiting, audit events, security headers, TLS 1.3 Caddy configuration, PostgreSQL/Redis Compose services, and encrypted SQL dump backup script. |

`scanner-engine/sc_analytics.py` also contains dependency-free confidence,
weighted-median consensus, outlier, route scoring, depletion, and forecast
primitives with deterministic tests.

## Still requires real integrations or production work

- The hosted Lovable website and its backend are not part of this repository.
- Discord OAuth, guild verification, and production moderator identities need
  a real Discord application and securely configured environment values.
- Provider adapters are deliberately disabled. No UEX, Erkul, Hauler, SC
  Trade Tools, or other live source is queried until its authorized API
  contract and field mapping are configured.
- Discord interactions support `/ping`, confirmed `/market` lookup,
  `/bug-report`, and moderator-only `/review-bug`. The interactions endpoint
  and command registration script need a publicly reachable HTTPS API,
  configured public key, bot token, and guild/application setup.
- The supporter profile is data/API scaffolding only; payments and badge
  synchronization are not implemented.
- Forecasting is an explainable linear baseline, not a validated economic
  predictor. Historical reports remain player observations and should not be
  treated as guaranteed in-game truth.
- Multi-instance WebSocket fan-out, deployment monitoring/alerting, restore
  drills, and production operations are not configured. Container volume
  encryption depends on the deployment host; the backup script encrypts
  exported SQL separately.
- Scan import currently lives in the web dashboard, not in the Electron
  overlay. It requires an explicit player confirmation and reports remain
  pending until a moderator confirms them.

## Local service setup

1. Install Docker Compose and copy `.env.example` to `.env`.
2. Set unique URL-safe database/Redis passwords and a random `JWT_SECRET` of
   at least 32 bytes. Keep `.env` private.
3. Configure the Discord OAuth client, redirect URI
   (`http://localhost:8080/api/v2/auth/discord/callback` for local use), bot,
   guild, and optional verified role.
4. Start the stack with `docker compose up --build`.
5. Open `http://localhost:8080`. For production, configure `SITE_DOMAIN`,
   HTTPS callback/web URLs and production secrets, then use
   `docker compose --profile production up --build`.

The dashboard can render an empty market without Discord setup, but report
submission requires successful Discord sign-in. The production Caddy profile
obtains TLS certificates for the configured public domain. Do not expose the
development profile directly to the internet.

## Validation

Run Python tests with `python -m unittest discover -s tests -v`. Validate the
schema and launcher with the commands in the root README. Docker image and
Compose execution must be checked in an environment where Docker is installed.
