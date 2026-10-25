import asyncio
import json
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

import sc_analytics
from .auth import current_user, router as auth_router
from .database import Base, engine, get_session
from .discord_interactions import (
    MAX_INTERACTION_BODY,
    handle_interaction,
    parse_interaction_body,
    verify_discord_signature,
)
from .limiter import RateLimitMiddleware
from .models import (
    AuditEvent,
    BugReport,
    Commodity,
    ConfidenceScore,
    DiscordInteraction,
    Device,
    ForecastRecord,
    InventorySnapshot,
    Location,
    MarketObservation,
    PatchVersion,
    Report,
    RouteHistory,
    RoutePenalty,
    ServerShard,
    SupporterProfile,
    TelemetryEvent,
    User,
    WebSocketTicket,
    utcnow,
)
from .schemas import (
    BugReportCreate,
    BugReview,
    DeviceCreate,
    ReportCreate,
    RouteRequest,
    TelemetryCreate,
)
from .security import hash_refresh_token, new_refresh_token, utc_after, verify_access_token

optional_bearer = HTTPBearer(auto_error=False)


class MarketFeed:
    def __init__(self):
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, socket: WebSocket):
        await socket.accept()
        async with self._lock:
            self._connections.add(socket)

    async def disconnect(self, socket: WebSocket):
        async with self._lock:
            self._connections.discard(socket)

    async def publish(self, message: dict):
        async with self._lock:
            connections = list(self._connections)
        stale = []
        for socket in connections:
            try:
                await socket.send_json(message)
            except (OSError, RuntimeError, WebSocketDisconnect):
                stale.append(socket)
        for socket in stale:
            await self.disconnect(socket)


market_feed = MarketFeed()


def _report_values(rows: list[MarketObservation], field: str) -> list[dict]:
    grouped: dict[tuple[str, str, str, str], list[dict]] = {}
    for row in rows:
        report = row.report
        value = getattr(row, field)
        if value is None:
            continue
        key = (
            report.location.name,
            report.patch_version,
            report.server_shard,
            report.review_status,
        )
        grouped.setdefault(key, []).append({
            "value": value,
            "observed_at": row.observed_at or report.observed_at,
            "ocr_confidence": report.ocr_confidence,
            "reporter_reputation": report.user.reputation,
            "patch_version": report.patch_version,
            "server_shard": report.server_shard,
        })

    result = []
    for (location, patch, shard, review_status), observations in grouped.items():
        aggregate = sc_analytics.aggregate_observations(
            observations,
            expected_patch=patch,
            expected_shard=shard,
        )
        if aggregate is not None:
            latest_observed = max(
                observation["observed_at"] for observation in observations
            )
            result.append({
                "location": location,
                "patch_version": patch,
                "server_shard": shard,
                "review_status": review_status,
                "latest_observed_at": _utc_iso(latest_observed),
                **aggregate,
            })
    return result


def optional_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(optional_bearer),
    session: Session = Depends(get_session),
) -> User | None:
    if credentials is None:
        return None
    claims = verify_access_token(credentials.credentials)
    if claims is None:
        return None
    user = session.get(User, claims["sub"])
    return user if user is not None and user.is_active else None


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def create_app(*, create_tables: bool = False) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_application):
        if create_tables and os.environ.get("APP_ENV") != "production":
            Base.metadata.create_all(bind=engine)
        yield

    application = FastAPI(
        title="Star Citizen Intelligence Platform API",
        version="2.0.0",
        description="Player-submitted, reviewable commodity intelligence API.",
        docs_url="/docs" if os.environ.get("APP_ENV") != "production" else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    application.add_middleware(RateLimitMiddleware)
    application.include_router(auth_router, prefix="/api/v2")

    @application.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if os.environ.get("APP_ENV") == "production":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    @application.get("/health")
    def health():
        return {"status": "ok", "service": "scip-api", "version": "2.0.0"}

    @application.get("/health/ready")
    def readiness(session: Session = Depends(get_session)):
        session.execute(select(1))
        return {"status": "ready", "database": "ok"}

    @application.post("/api/v2/discord/interactions")
    async def discord_interactions(
        request: Request,
        session: Session = Depends(get_session),
    ):
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdecimal() and int(content_length) > MAX_INTERACTION_BODY:
            raise HTTPException(status_code=413, detail="Discord interaction payload is too large.")
        body = await request.body()
        if len(body) > MAX_INTERACTION_BODY:
            raise HTTPException(status_code=413, detail="Discord interaction payload is too large.")
        if not verify_discord_signature(
            body,
            request.headers.get("X-Signature-Ed25519"),
            request.headers.get("X-Signature-Timestamp"),
        ):
            raise HTTPException(status_code=401, detail="Invalid Discord request signature.")
        try:
            payload = parse_interaction_body(body)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Invalid Discord interaction payload.") from exc
        interaction_id = payload.get("id")
        if not isinstance(interaction_id, str) or not interaction_id.isdecimal():
            raise HTTPException(status_code=400, detail="Discord interaction ID is invalid.")
        if session.get(DiscordInteraction, interaction_id) is not None:
            return {
                "type": 4,
                "data": {
                    "content": "This interaction was already handled.",
                    "flags": 64,
                    "allowed_mentions": {"parse": []},
                },
            }
        session.add(DiscordInteraction(id=interaction_id))
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            return {
                "type": 4,
                "data": {
                    "content": "This interaction was already handled.",
                    "flags": 64,
                    "allowed_mentions": {"parse": []},
                },
            }
        result = handle_interaction(payload, session)
        session.commit()
        return result

    @application.post("/api/v2/devices", status_code=201)
    def register_device(
        payload: DeviceCreate,
        user: Annotated[User, Depends(current_user)],
        session: Session = Depends(get_session),
    ):
        device = session.scalar(select(Device).where(
            Device.user_id == user.id,
            Device.device_id == payload.device_id,
        ))
        if device is None:
            device = Device(
                user_id=user.id,
                device_id=payload.device_id,
                device_name=payload.device_name,
            )
            session.add(device)
        else:
            device.device_name = payload.device_name
            device.last_seen_at = utcnow()
        session.add(AuditEvent(
            actor_id=user.id,
            action="device.register",
            target_type="device",
            target_id=payload.device_id,
            metadata_json={},
        ))
        session.commit()
        return {"id": device.id, "device_id": device.device_id, "device_name": device.device_name}

    @application.get("/api/v2/commodities")
    def list_commodities(
        session: Session = Depends(get_session),
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
    ):
        rows = session.scalars(
            select(Commodity).where(Commodity.is_active.is_(True)).order_by(Commodity.name).limit(limit)
        ).all()
        return [{"id": row.id, "name": row.name, "aliases": row.aliases or []} for row in rows]

    @application.get("/api/v2/locations")
    def list_locations(
        session: Session = Depends(get_session),
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
    ):
        rows = session.scalars(
            select(Location).where(Location.is_active.is_(True)).order_by(Location.name).limit(limit)
        ).all()
        return [
            {"id": row.id, "name": row.name, "system": row.system, "type": row.location_type}
            for row in rows
        ]

    @application.post("/api/v2/reports", status_code=201)
    async def submit_report(
        payload: ReportCreate,
        user: Annotated[User, Depends(current_user)],
        session: Session = Depends(get_session),
    ):
        existing = session.scalar(select(Report).where(
            Report.user_id == user.id,
            Report.client_report_id == payload.client_report_id,
        ))
        if existing is not None:
            return {"id": existing.id, "status": existing.review_status, "duplicate": True}

        location = session.scalar(select(Location).where(Location.name == payload.location))
        if location is None:
            location = Location(name=payload.location)
            session.add(location)
        patch = session.get(PatchVersion, payload.patch_version)
        if patch is None:
            patch = PatchVersion(id=payload.patch_version, label=payload.patch_version)
            session.add(patch)
        shard = session.get(ServerShard, payload.server_shard)
        if shard is None:
            shard = ServerShard(id=payload.server_shard, label=payload.server_shard)
            session.add(shard)
        session.flush()

        report = Report(
            user_id=user.id,
            client_report_id=payload.client_report_id,
            location_id=location.id,
            patch_version=patch.id,
            server_shard=shard.id,
            observed_at=payload.observed_at,
            ocr_confidence=payload.ocr_confidence,
            source=payload.source,
            review_status="pending",
        )
        session.add(report)
        session.flush()
        for input_row in payload.commodities:
            if all(getattr(input_row, field) is None for field in ("buy", "sell", "stock", "demand")):
                raise HTTPException(
                    status_code=422,
                    detail=f"{input_row.commodity} has no numeric observation.",
                )
            commodity = session.scalar(select(Commodity).where(Commodity.name == input_row.commodity))
            if commodity is None:
                commodity = Commodity(name=input_row.commodity)
                session.add(commodity)
                session.flush()
            observation = MarketObservation(
                report_id=report.id,
                commodity_id=commodity.id,
                buy_price=input_row.buy,
                sell_price=input_row.sell,
                stock=input_row.stock,
                demand=input_row.demand,
                demand_label=input_row.demand_label,
                observed_at=input_row.observed_at or payload.observed_at,
            )
            session.add(observation)
            session.flush()
            score = sc_analytics.report_confidence(
                payload.ocr_confidence,
                user.reputation,
                observation.observed_at,
                expected_patch=patch.id,
                report_patch=patch.id,
                expected_shard=shard.id,
                report_shard=shard.id,
            )
            session.add(ConfidenceScore(
                observation_id=observation.id,
                algorithm_version="2.0",
                score=score["score"],
                components=score,
            ))

        for inventory_row in payload.inventory:
            commodity = session.scalar(
                select(Commodity).where(Commodity.name == inventory_row.commodity)
            )
            if commodity is None:
                commodity = Commodity(name=inventory_row.commodity)
                session.add(commodity)
                session.flush()
            session.add(InventorySnapshot(
                report_id=report.id,
                commodity_id=commodity.id,
                quantity=inventory_row.quantity,
                profit_direction=inventory_row.profit_direction,
            ))

        session.add(AuditEvent(
            actor_id=user.id,
            action="report.submit",
            target_type="report",
            target_id=report.id,
            metadata_json={"rows": len(payload.commodities), "status": "pending"},
        ))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            existing = session.scalar(select(Report).where(
                Report.user_id == user.id,
                Report.client_report_id == payload.client_report_id,
            ))
            if existing is not None:
                return {"id": existing.id, "status": existing.review_status, "duplicate": True}
            raise HTTPException(status_code=409, detail="Report conflicts with existing data.")

        await market_feed.publish({
            "type": "report_submitted",
            "report_id": report.id,
            "status": report.review_status,
            "location": location.name,
            "patch_version": patch.id,
            "server_shard": shard.id,
        })
        return {"id": report.id, "status": report.review_status, "duplicate": False}

    @application.get("/api/v2/markets")
    def get_markets(
        session: Session = Depends(get_session),
        location: str | None = None,
        commodity: str | None = None,
        patch_version: str = Query(min_length=1, max_length=80),
        server_shard: str = Query(min_length=1, max_length=100),
        limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    ):
        statement = (
            select(MarketObservation)
            .join(MarketObservation.report)
            .join(Report.location)
            .join(Report.user)
            .join(MarketObservation.commodity)
            .where(
                Report.patch_version == patch_version,
                Report.server_shard == server_shard,
                Report.review_status.in_(("pending", "confirmed")),
                MarketObservation.observed_at >= utcnow() - timedelta(days=7),
            )
            .options(
                joinedload(MarketObservation.report).joinedload(Report.location),
                joinedload(MarketObservation.report).joinedload(Report.user),
                joinedload(MarketObservation.commodity),
            )
            .order_by(Report.observed_at.desc())
            .limit(limit)
        )
        if location:
            statement = statement.where(Location.name == location)
        if commodity:
            statement = statement.where(Commodity.name == commodity)
        rows = session.scalars(statement).unique().all()

        groups: dict[tuple[str, str], list[MarketObservation]] = {}
        for row in rows:
            groups.setdefault((row.report.location.name, row.commodity.name), []).append(row)
        response = []
        for (location_name, commodity_name), group in groups.items():
            values = {
                field: _report_values(group, attr)
                for field, attr in (
                    ("buy", "buy_price"),
                    ("sell", "sell_price"),
                    ("stock", "stock"),
                    ("demand", "demand"),
                )
            }
            response.append({
                "location": location_name,
                "commodity": commodity_name,
                "patch_version": patch_version,
                "server_shard": server_shard,
                "buy": values["buy"],
                "sell": values["sell"],
                "stock": values["stock"],
                "demand": values["demand"],
            })
        return response

    @application.get("/api/v2/history")
    def get_history(
        commodity: str,
        location: str,
        patch_version: str,
        server_shard: str,
        session: Session = Depends(get_session),
        days: Annotated[int, Query(ge=1, le=365)] = 30,
        limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    ):
        statement = (
            select(MarketObservation)
            .join(MarketObservation.report)
            .join(Report.location)
            .join(MarketObservation.commodity)
            .where(
                Commodity.name == commodity,
                Location.name == location,
                Report.patch_version == patch_version,
                Report.server_shard == server_shard,
                Report.review_status.in_(("pending", "confirmed")),
                MarketObservation.observed_at >= utcnow() - timedelta(days=days),
            )
            .options(joinedload(MarketObservation.report))
            .order_by(Report.observed_at.desc())
            .limit(limit)
        )
        return [
            {
                "observed_at": _utc_iso(row.observed_at or row.report.observed_at),
                "buy": row.buy_price,
                "sell": row.sell_price,
                "stock": row.stock,
                "demand": row.demand,
                "ocr_confidence": row.report.ocr_confidence,
                "review_status": row.report.review_status,
            }
            for row in session.scalars(statement).all()
        ]

    @application.get("/api/v2/stock-trends")
    def get_stock_trends(
        location: str,
        commodity: str,
        patch_version: str,
        server_shard: str,
        session: Session = Depends(get_session),
    ):
        rows = session.scalars(
            select(MarketObservation)
            .join(MarketObservation.report)
            .join(Report.location)
            .join(MarketObservation.commodity)
            .where(
                Location.name == location,
                Commodity.name == commodity,
                Report.patch_version == patch_version,
                Report.server_shard == server_shard,
                Report.review_status == "confirmed",
                MarketObservation.observed_at >= utcnow() - timedelta(days=30),
            )
            .options(joinedload(MarketObservation.report))
            .order_by(MarketObservation.observed_at)
            .limit(500)
        ).all()
        snapshots = [
            {
                "station": location,
                "commodity": commodity,
                "stock": row.stock,
                "observed_at": row.observed_at or row.report.observed_at,
                "server_shard": row.report.server_shard,
                "patch_version": row.report.patch_version,
            }
            for row in rows
            if row.stock is not None
        ]
        estimate = sc_analytics.estimate_stock_depletion(snapshots)
        return estimate or {
            "station": location,
            "commodity": commodity,
            "patch_version": patch_version,
            "server_shard": server_shard,
            "status": "insufficient_fresh_confirmed_history",
        }

    @application.post("/api/v2/routes")
    def calculate_routes(
        payload: RouteRequest,
        session: Session = Depends(get_session),
        user: User | None = Depends(optional_current_user),
    ):
        statement = (
            select(MarketObservation)
            .join(MarketObservation.report)
            .join(Report.location)
            .join(MarketObservation.commodity)
            .where(
                Report.patch_version == payload.patch_version,
                Report.server_shard == payload.server_shard,
                Report.review_status == "confirmed",
                MarketObservation.observed_at >= utcnow() - timedelta(days=7),
            )
            .options(
                joinedload(MarketObservation.report).joinedload(Report.location),
                joinedload(MarketObservation.commodity),
            )
        )
        markets = []
        for row in session.scalars(statement).all():
            markets.append({
                "station": row.report.location.name,
                "commodity": row.commodity.name,
                "buy": row.buy_price,
                "sell": row.sell_price,
                "stock": row.stock or 0,
                "confidence": sc_analytics.report_confidence(
                    row.report.ocr_confidence,
                    row.report.user.reputation,
                    row.observed_at or row.report.observed_at,
                    expected_patch=payload.patch_version,
                    report_patch=row.report.patch_version,
                    expected_shard=payload.server_shard,
                    report_shard=row.report.server_shard,
                )["score"],
                "patch_version": row.report.patch_version,
                "server_shard": row.report.server_shard,
            })
        options = payload.model_dump()
        routes = sc_analytics.recommend_routes(markets, **options)
        penalties = session.execute(
            select(Location.name, RoutePenalty.penalty)
            .join(RoutePenalty, RoutePenalty.location_id == Location.id)
            .where(
                RoutePenalty.patch_version == payload.patch_version,
                RoutePenalty.is_active.is_(True),
            )
        ).all()
        penalty_by_location: dict[str, float] = {}
        for location_name, penalty in penalties:
            penalty_by_location[location_name] = min(
                1.0,
                penalty_by_location.get(location_name, 0.0) + penalty,
            )
        for route in routes:
            route_penalty = max(
                penalty_by_location.get(route["origin"], 0.0),
                penalty_by_location.get(route["destination"], 0.0),
            )
            route["bug_risk_penalty"] = route_penalty
            route["risk_adjusted_profit"] *= 1.0 - route_penalty
            route["risk_adjusted_profit_per_hour"] *= 1.0 - route_penalty
        routes.sort(key=lambda route: -route["risk_adjusted_profit_per_hour"])
        if user is not None:
            session.add(RouteHistory(
                user_id=user.id,
                request=payload.model_dump(),
                result={"routes": routes},
                algorithm_version="2.0",
            ))
            session.commit()
        return {"routes": routes, "algorithm_version": "2.0"}

    @application.post("/api/v2/bugs", status_code=201)
    def submit_bug_report(
        payload: BugReportCreate,
        session: Session = Depends(get_session),
        user: User | None = Depends(optional_current_user),
    ):
        location = None
        if payload.location:
            location = session.scalar(select(Location).where(Location.name == payload.location))
            if location is None:
                raise HTTPException(status_code=422, detail="Unknown location.")
        bug = BugReport(
            reporter_id=user.id if user else None,
            category=payload.category,
            location_id=location.id if location else None,
            patch_version=payload.patch_version,
            description=payload.description,
        )
        if payload.patch_version and session.get(PatchVersion, payload.patch_version) is None:
            session.add(PatchVersion(id=payload.patch_version, label=payload.patch_version))
        session.add(bug)
        session.flush()
        session.add(AuditEvent(
            actor_id=user.id if user else None,
            action="bug_report.submit",
            target_type="bug_report",
            target_id=bug.id,
            metadata_json={"category": bug.category},
        ))
        session.commit()
        return {"id": bug.id, "status": bug.status}

    @application.post("/api/v2/reports/{report_id}/review")
    def review_report(
        report_id: str,
        decision: str,
        user: Annotated[User, Depends(current_user)],
        session: Session = Depends(get_session),
    ):
        if not user.is_moderator:
            raise HTTPException(status_code=403, detail="Moderator privileges are required.")
        if decision not in {"confirmed", "rejected"}:
            raise HTTPException(status_code=422, detail="Decision must be confirmed or rejected.")
        report = session.get(Report, report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="Report not found.")
        report.review_status = decision
        session.add(AuditEvent(
            actor_id=user.id,
            action=f"report.review.{decision}",
            target_type="report",
            target_id=report.id,
            metadata_json={},
        ))
        session.commit()
        return {"id": report.id, "status": report.review_status}

    @application.post("/api/v2/bugs/{bug_id}/review")
    def review_bug_report(
        bug_id: str,
        payload: BugReview,
        user: Annotated[User, Depends(current_user)],
        session: Session = Depends(get_session),
    ):
        if not user.is_moderator:
            raise HTTPException(status_code=403, detail="Moderator privileges are required.")
        bug = session.get(BugReport, bug_id)
        if bug is None:
            raise HTTPException(status_code=404, detail="Bug report not found.")
        bug.status = payload.decision
        prior_penalties = session.scalars(
            select(RoutePenalty).where(RoutePenalty.bug_report_id == bug.id)
        ).all()
        for penalty in prior_penalties:
            penalty.is_active = False
        if payload.decision == "confirmed" and payload.route_penalty > 0 and bug.location_id and bug.patch_version:
            session.add(RoutePenalty(
                bug_report_id=bug.id,
                location_id=bug.location_id,
                patch_version=bug.patch_version,
                category=bug.category,
                penalty=payload.route_penalty,
            ))
        session.add(AuditEvent(
            actor_id=user.id,
            action=f"bug_report.review.{payload.decision}",
            target_type="bug_report",
            target_id=bug.id,
            metadata_json={"route_penalty": payload.route_penalty},
        ))
        session.commit()
        return {"id": bug.id, "status": bug.status, "route_penalty": payload.route_penalty}

    @application.get("/api/v2/sources")
    def list_sources():
        from .providers import provider_registry

        return provider_registry()

    @application.post("/api/v2/telemetry", status_code=202)
    def submit_telemetry(
        payload: TelemetryCreate,
        session: Session = Depends(get_session),
        user: User | None = Depends(optional_current_user),
    ):
        event = TelemetryEvent(
            user_id=user.id if user else None,
            event_name=payload.event_name,
            metadata_json=payload.metadata,
        )
        session.add(event)
        session.commit()
        return {"accepted": True}

    @application.get("/api/v2/forecasts")
    def get_forecasts(
        location: str,
        commodity: str,
        patch_version: str,
        server_shard: str,
        horizon_hours: float = 24,
        session: Session = Depends(get_session),
    ):
        if not 0 < horizon_hours <= 720:
            raise HTTPException(status_code=422, detail="horizon_hours must be greater than 0 and at most 720.")
        rows = session.scalars(
            select(ForecastRecord)
            .join(Location, ForecastRecord.location_id == Location.id)
            .join(Commodity, ForecastRecord.commodity_id == Commodity.id)
            .where(
                Location.name == location,
                Commodity.name == commodity,
                ForecastRecord.patch_version == patch_version,
                ForecastRecord.server_shard == server_shard,
                ForecastRecord.target_at > utcnow(),
            )
            .order_by(ForecastRecord.target_at)
            .limit(100)
        ).all()
        if rows:
            return [
                {
                    "metric": row.metric,
                    "predicted_value": row.predicted_value,
                    "confidence": row.confidence,
                    "target_at": row.target_at.isoformat(),
                    "algorithm_version": row.algorithm_version,
                }
                for row in rows
            ]

        observations = session.scalars(
            select(MarketObservation)
            .join(MarketObservation.report)
            .join(Report.location)
            .join(MarketObservation.commodity)
            .where(
                Location.name == location,
                Commodity.name == commodity,
                Report.patch_version == patch_version,
                Report.server_shard == server_shard,
                Report.review_status == "confirmed",
                MarketObservation.observed_at >= utcnow() - timedelta(days=30),
            )
            .options(joinedload(MarketObservation.report))
            .order_by(MarketObservation.observed_at)
            .limit(500)
        ).all()
        forecasts = []
        for metric, attribute in (("buy", "buy_price"), ("sell", "sell_price"), ("stock", "stock")):
            points = [
                {
                    "value": getattr(item, attribute),
                    "observed_at": item.observed_at or item.report.observed_at,
                    "confidence": item.report.ocr_confidence,
                }
                for item in observations
                if getattr(item, attribute) is not None
            ]
            prediction = sc_analytics.linear_forecast(
                points,
                horizon_hours=horizon_hours,
            )
            if prediction is not None:
                forecasts.append({
                    "metric": metric,
                    "predicted_value": prediction["predicted_value"],
                    "confidence": prediction["confidence"],
                    "target_at": datetime.fromtimestamp(
                        prediction["latest_observed_at"] + horizon_hours * 3600,
                        tz=timezone.utc,
                    ),
                    "sample_count": prediction["sample_count"],
                    "algorithm_version": "linear-baseline-2.0",
                    "is_prediction": True,
                })
        return forecasts

    @application.get("/api/v2/supporters/me")
    def supporter_profile(
        user: Annotated[User, Depends(current_user)],
        session: Session = Depends(get_session),
    ):
        profile = session.get(SupporterProfile, user.id)
        if profile is None or profile.status != "active":
            return {"active": False, "badge_name": None}
        return {"active": True, "badge_name": profile.badge_name}

    @application.post("/api/v2/auth/websocket-ticket")
    def create_websocket_ticket(
        user: Annotated[User, Depends(current_user)],
        session: Session = Depends(get_session),
    ):
        ticket = new_refresh_token()
        session.add(WebSocketTicket(
            token_hash=hash_refresh_token(ticket),
            user_id=user.id,
            expires_at=utc_after(60),
        ))
        session.commit()
        return {"ticket": ticket, "expires_in": 60}

    @application.websocket("/api/v2/ws/markets")
    async def market_websocket(socket: WebSocket, ticket: str):
        from .database import SessionLocal

        with SessionLocal() as session:
            now = utcnow()
            claimed = session.execute(
                update(WebSocketTicket)
                .where(
                    WebSocketTicket.token_hash == hash_refresh_token(ticket),
                    WebSocketTicket.consumed_at.is_(None),
                    WebSocketTicket.expires_at > now,
                )
                .values(consumed_at=now)
                .execution_options(synchronize_session=False)
            )
            if claimed.rowcount != 1:
                await socket.close(code=4401, reason="Invalid websocket ticket.")
                return
            session.commit()
        await market_feed.connect(socket)
        try:
            while True:
                await socket.receive_text()
        except WebSocketDisconnect:
            await market_feed.disconnect(socket)

    if os.environ.get("APP_ENV") != "production":
        web_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "apps", "web"))
        if os.path.isdir(web_root):
            application.mount("/", StaticFiles(directory=web_root, html=True), name="dashboard")

    return application


app = create_app()
