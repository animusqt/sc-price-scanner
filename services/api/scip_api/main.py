import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated
from urllib.parse import urlsplit

import httpx
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

import sc_analytics
from .catalog_importers import (
    API_IMPORTERS,
    MAX_IMPORT_BYTES,
    CatalogImportBatch,
    CsvImporter,
    JsonImporter,
    import_catalog_batch,
    importer_registry,
    record_ingestion,
)
from .access import current_user
from .database import Base, engine, get_session
from .limiter import RateLimitMiddleware
from .models import (
    AuditEvent,
    BugReport,
    Commodity,
    ConfidenceScore,
    CommodityPrice,
    Component,
    Device,
    ForecastRecord,
    FleetProfile,
    FleetShip,
    InventorySnapshot,
    Location,
    MarketObservation,
    PatchVersion,
    Report,
    RouteHistory,
    RoutePenalty,
    ServerShard,
    Ship,
    ShipComponent,
    SourceIngestion,
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
    FleetProfileCreate,
    FleetProfileUpdate,
    FleetShipUpdate,
    OcrImageRequest,
    OcrSubmitRequest,
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
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:1420",
            "http://localhost:1420",
            "http://tauri.localhost",
            "tauri://localhost",
        ],
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Accept", "Content-Type", "Authorization"],
    )
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

    @application.get("/")
    def service_info():
        return {"service": "scip-api", "desktop": "apps/desktop"}

    @application.post("/api/ocr/scan")
    async def scan_ocr_image(payload: OcrImageRequest):
        ocr_service_url = os.environ.get(
            "SCIP_OCR_SERVICE_URL", "http://127.0.0.1:8001"
        ).rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                response = await client.post(
                    f"{ocr_service_url}/ocr/scan",
                    json=payload.model_dump(mode="json"),
                )
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=503,
                detail="Local OCR service is unavailable. Start the SCIP OCR service and retry.",
            ) from exc

        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = {}
            detail = body.get("detail") if isinstance(body, dict) else None
            raise HTTPException(
                status_code=response.status_code if response.status_code < 500 else 503,
                detail=str(detail or f"OCR processing failed ({response.status_code})."),
            )
        try:
            return response.json()
        except ValueError as exc:
            raise HTTPException(
                status_code=502, detail="OCR service returned an invalid response."
            ) from exc

    @application.post("/api/ocr-submit")
    async def submit_ocr_report(payload: OcrSubmitRequest):
        base_url = os.environ.get("SC_TRUCKERS_API_BASE_URL", "").strip().rstrip("/")
        if not base_url:
            raise HTTPException(
                status_code=503,
                detail="SC-Truckers OCR submission is not configured on this installation.",
            )

        parsed_url = urlsplit(base_url)
        local_host = parsed_url.hostname in {"localhost", "127.0.0.1", "::1"}
        if (
            parsed_url.scheme != "https" and not (parsed_url.scheme == "http" and local_host)
        ) or not parsed_url.hostname or parsed_url.username or parsed_url.password:
            raise HTTPException(
                status_code=503,
                detail="SC-Truckers API must use HTTPS (HTTP is allowed only for localhost).",
            )
        if parsed_url.query or parsed_url.fragment:
            raise HTTPException(
                status_code=503, detail="SC-Truckers API base URL cannot contain a query or fragment."
            )

        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        api_token = os.environ.get("SC_TRUCKERS_API_TOKEN", "").strip()
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    f"{base_url}/api/ocr-submit",
                    json=payload.model_dump(mode="json"),
                    headers=headers,
                )
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=503,
                detail="SC-Truckers could not be reached. The upload is safe to retry.",
            ) from exc

        try:
            confirmation = response.json()
        except ValueError:
            confirmation = None
        if response.status_code == 422:
            detail = (
                confirmation.get("detail")
                if isinstance(confirmation, dict)
                else "SC-Truckers rejected the OCR data."
            )
            raise HTTPException(status_code=422, detail=str(detail))
        if response.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail=f"SC-Truckers upload failed with HTTP {response.status_code}.",
            )
        if confirmation is None:
            raise HTTPException(
                status_code=502,
                detail="SC-Truckers accepted no readable confirmation; retry with the same report ID.",
            )
        return {"status": "accepted", "confirmation": confirmation}

    @application.get("/health/ready")
    def readiness(session: Session = Depends(get_session)):
        session.execute(select(1))
        return {"status": "ready", "database": "ok"}

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

    @application.get("/api/v2/importers")
    def list_importers():
        return importer_registry()

    def apply_catalog_import(source: str, batch: CatalogImportBatch, session: Session):
        try:
            counts = import_catalog_batch(session, source, batch)
            record_ingestion(session, source, batch, counts)
        except ValueError as exc:
            session.rollback()
            record_ingestion(session, source, batch, None, str(exc))
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "source": source,
            **counts,
            "records_seen": sum(
                len(getattr(batch, key)) for key in CatalogImportBatch.model_fields
            ),
        }

    @application.post("/api/v2/imports/json")
    async def import_json(request: Request, session: Session = Depends(get_session)):
        body = await request.body()
        if len(body) > MAX_IMPORT_BYTES:
            raise HTTPException(status_code=413, detail="JSON import exceeds the 10 MiB size limit.")
        try:
            batch = JsonImporter().parse_json(await request.json())
        except (ValueError, UnicodeDecodeError) as exc:
            record_ingestion(session, "json-import", None, None, str(exc))
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return apply_catalog_import("json-import", batch, session)

    @application.post("/api/v2/imports/csv")
    async def import_csv(
        request: Request,
        record_type: str = Query(min_length=1, max_length=40),
        session: Session = Depends(get_session),
    ):
        body = await request.body()
        if len(body) > MAX_IMPORT_BYTES:
            raise HTTPException(status_code=413, detail="CSV import exceeds the 10 MiB size limit.")
        try:
            content = body.decode("utf-8-sig")
            batch = CsvImporter().parse_csv(content, record_type)
        except (ValueError, UnicodeDecodeError) as exc:
            record_ingestion(session, "csv-import", None, None, str(exc))
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return apply_catalog_import("csv-import", batch, session)

    @application.post("/api/v2/imports/api/{source_key}")
    async def import_source_api(
        source_key: str,
        session: Session = Depends(get_session),
    ):
        importer = API_IMPORTERS.get(source_key)
        if importer is None:
            raise HTTPException(status_code=404, detail="Unknown catalog API source.")
        try:
            payload = await importer.fetch_json()
            batch = importer.parse_json(payload)
        except ValueError as exc:
            record_ingestion(session, source_key, None, None, str(exc))
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return apply_catalog_import(source_key, batch, session)

    @application.get("/api/v2/imports/history")
    def import_history(
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        session: Session = Depends(get_session),
    ):
        ingestions = session.scalars(
            select(SourceIngestion).order_by(SourceIngestion.started_at.desc()).limit(limit)
        ).all()
        return [
            {
                "id": item.id,
                "source": item.source,
                "status": item.status,
                "records_seen": item.records_seen,
                "result": item.result_json or {},
                "error": item.error_summary,
                "started_at": _utc_iso(item.started_at),
                "completed_at": _utc_iso(item.completed_at) if item.completed_at else None,
            }
            for item in ingestions
        ]

    @application.get("/api/v2/catalog/commodities")
    def browse_commodities(
        search: str | None = Query(default=None, max_length=160),
        category: str | None = Query(default=None, max_length=100),
        min_profit: int | None = Query(default=None, ge=0),
        min_supply: int | None = Query(default=None, ge=0),
        max_supply: int | None = Query(default=None, ge=0),
        min_demand: int | None = Query(default=None, ge=0),
        max_demand: int | None = Query(default=None, ge=0),
        patch_version: str = Query(default="live", min_length=1, max_length=80),
        server_shard: str = Query(default="global", min_length=1, max_length=100),
        limit: Annotated[int, Query(ge=1, le=5000)] = 5000,
        session: Session = Depends(get_session),
    ):
        statement = select(Commodity).where(Commodity.is_active.is_(True)).order_by(Commodity.name)
        if search:
            statement = statement.where(Commodity.name.ilike(f"%{search.strip()}%"))
        if category:
            statement = statement.where(func.lower(Commodity.category) == category.strip().casefold())
        commodity_rows = session.scalars(statement.limit(limit)).all()
        if not commodity_rows:
            return []

        prices = session.scalars(
            select(CommodityPrice)
            .where(
                CommodityPrice.commodity_id.in_([row.id for row in commodity_rows]),
                CommodityPrice.patch_version == patch_version,
                CommodityPrice.server_shard == server_shard,
                CommodityPrice.observed_at >= utcnow() - timedelta(days=7),
            )
            .options(joinedload(CommodityPrice.location))
            .order_by(CommodityPrice.observed_at.desc())
            .limit(50_000)
        ).all()
        latest_by_market: dict[tuple[str, str], CommodityPrice] = {}
        for price in prices:
            latest_by_market.setdefault((price.commodity_id, price.location_id), price)
        by_commodity: dict[str, list[CommodityPrice]] = {}
        for price in latest_by_market.values():
            by_commodity.setdefault(price.commodity_id, []).append(price)

        result = []
        for commodity in commodity_rows:
            observations = by_commodity.get(commodity.id, [])
            buy_values = [price for price in observations if price.buy_price is not None]
            sell_values = [price for price in observations if price.sell_price is not None]
            best_buy = min(buy_values, key=lambda price: price.buy_price) if buy_values else None
            best_sell = max(sell_values, key=lambda price: price.sell_price) if sell_values else None
            supply_values = [price.supply for price in observations if price.supply is not None]
            demand_values = [price.demand for price in observations if price.demand is not None]
            average_price_values = [
                value
                for price in observations
                for value in (price.buy_price, price.sell_price)
                if value is not None
            ]
            profit = (
                best_sell.sell_price - best_buy.buy_price
                if best_buy and best_sell
                else None
            )
            supply = round(sum(supply_values) / len(supply_values)) if supply_values else None
            demand = round(sum(demand_values) / len(demand_values)) if demand_values else None
            if min_profit is not None and (profit is None or profit < min_profit):
                continue
            if min_supply is not None and (supply is None or supply < min_supply):
                continue
            if max_supply is not None and (supply is None or supply > max_supply):
                continue
            if min_demand is not None and (demand is None or demand < min_demand):
                continue
            if max_demand is not None and (demand is None or demand > max_demand):
                continue
            result.append({
                "id": commodity.id,
                "name": commodity.name,
                "category": commodity.category,
                "description": commodity.description,
                "legal_status": commodity.legal_status,
                "base_price": commodity.base_price,
                "current_price": (
                    round(sum(average_price_values) / len(average_price_values))
                    if average_price_values else commodity.base_price
                ),
                "average_price": (
                    round(sum(average_price_values) / len(average_price_values))
                    if average_price_values else None
                ),
                "supply": supply,
                "demand": demand,
                "profit": profit,
                "best_buy_location": best_buy.location.name if best_buy else None,
                "best_buy_price": best_buy.buy_price if best_buy else None,
                "best_sell_location": best_sell.location.name if best_sell else None,
                "best_sell_price": best_sell.sell_price if best_sell else None,
                "confidence": (
                    round(sum(price.confidence_score for price in observations) / len(observations), 4)
                    if observations else None
                ),
                "updated_at": (
                    _utc_iso(max(price.observed_at for price in observations))
                    if observations else None
                ),
            })
        return result

    @application.get("/api/v2/ships")
    def browse_ships(
        search: str | None = Query(default=None, max_length=160),
        manufacturer: str | None = Query(default=None, max_length=120),
        role: str | None = Query(default=None, max_length=100),
        min_cargo: float | None = Query(default=None, ge=0),
        max_cargo: float | None = Query(default=None, ge=0),
        patch_version: str = Query(default="live", min_length=1, max_length=80),
        limit: Annotated[int, Query(ge=1, le=5000)] = 5000,
        session: Session = Depends(get_session),
    ):
        statement = select(Ship).where(
            Ship.is_active.is_(True),
            Ship.patch_version == patch_version,
        ).order_by(Ship.name)
        if search:
            statement = statement.where(Ship.name.ilike(f"%{search.strip()}%"))
        if manufacturer:
            statement = statement.where(Ship.manufacturer.ilike(f"%{manufacturer.strip()}%"))
        if role:
            statement = statement.where(func.lower(Ship.role) == role.strip().casefold())
        if min_cargo is not None:
            statement = statement.where(Ship.cargo_capacity_scu >= min_cargo)
        if max_cargo is not None:
            statement = statement.where(Ship.cargo_capacity_scu <= max_cargo)
        rows = session.scalars(statement.limit(limit)).all()
        return [
            {
                "id": row.id,
                "name": row.name,
                "manufacturer": row.manufacturer,
                "role": row.role,
                "cargo_capacity_scu": row.cargo_capacity_scu,
                "quantum_fuel": row.quantum_fuel,
                "hydrogen_fuel": row.hydrogen_fuel,
                "crew_min": row.crew_min,
                "crew_max": row.crew_max,
                "price_auec": row.price_auec,
                "price_usd": row.price_usd,
                "is_flyable": row.is_flyable,
                "image_url": row.image_url,
                "description": row.description,
                "specifications": row.specifications or {},
            }
            for row in rows
        ]

    @application.get("/api/v2/components")
    def browse_components(
        search: str | None = Query(default=None, max_length=160),
        component_type: str | None = Query(default=None, max_length=80),
        limit: Annotated[int, Query(ge=1, le=5000)] = 5000,
        session: Session = Depends(get_session),
    ):
        statement = select(Component).order_by(Component.name, Component.size)
        if search:
            statement = statement.where(Component.name.ilike(f"%{search.strip()}%"))
        if component_type:
            statement = statement.where(
                func.lower(Component.type) == component_type.strip().casefold()
            )
        rows = session.scalars(statement.limit(limit)).all()
        return [
            {
                "id": row.id,
                "name": row.name,
                "type": row.type,
                "size": row.size,
                "grade": row.grade,
                "manufacturer": row.manufacturer,
            }
            for row in rows
        ]

    def fleet_response(fleet: FleetProfile):
        ships = [
            {
                "ship_id": entry.ship.id,
                "quantity": entry.quantity,
                "name": entry.ship.name,
                "manufacturer": entry.ship.manufacturer,
                "cargo_capacity_scu": entry.ship.cargo_capacity_scu,
                "price_auec": entry.ship.price_auec,
            }
            for entry in fleet.ships
        ]
        return {
            "id": fleet.id,
            "fleet_name": fleet.fleet_name,
            "description": fleet.description,
            "ships": ships,
            "total_scu": sum(ship["cargo_capacity_scu"] * ship["quantity"] for ship in ships),
            "total_value_auec": sum((ship["price_auec"] or 0) * ship["quantity"] for ship in ships),
        }

    @application.get("/api/v2/fleets")
    def list_fleets(session: Session = Depends(get_session)):
        rows = session.scalars(
            select(FleetProfile).options(joinedload(FleetProfile.ships).joinedload(FleetShip.ship))
            .order_by(FleetProfile.fleet_name)
        ).unique().all()
        return [fleet_response(row) for row in rows]

    @application.post("/api/v2/fleets", status_code=201)
    def create_fleet(payload: FleetProfileCreate, session: Session = Depends(get_session)):
        existing = session.scalar(
            select(FleetProfile).where(
                func.lower(FleetProfile.fleet_name) == payload.fleet_name.casefold()
            )
        )
        if existing is not None:
            raise HTTPException(status_code=409, detail="A fleet with that name already exists.")
        fleet = FleetProfile(fleet_name=payload.fleet_name, description=payload.description)
        session.add(fleet)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail="A fleet with that name already exists.") from exc
        session.refresh(fleet)
        return fleet_response(fleet)

    @application.put("/api/v2/fleets/{fleet_id}")
    def update_fleet(
        fleet_id: str,
        payload: FleetProfileUpdate,
        session: Session = Depends(get_session),
    ):
        fleet = session.scalar(
            select(FleetProfile).where(FleetProfile.id == fleet_id)
            .options(joinedload(FleetProfile.ships).joinedload(FleetShip.ship))
        )
        if fleet is None:
            raise HTTPException(status_code=404, detail="Fleet not found.")
        duplicate = session.scalar(
            select(FleetProfile).where(
                FleetProfile.id != fleet.id,
                func.lower(FleetProfile.fleet_name) == payload.fleet_name.casefold(),
            )
        )
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="A fleet with that name already exists.")
        fleet.fleet_name = payload.fleet_name
        fleet.description = payload.description
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail="A fleet with that name already exists.") from exc
        return fleet_response(fleet)

    @application.delete("/api/v2/fleets/{fleet_id}", status_code=204)
    def delete_fleet(fleet_id: str, session: Session = Depends(get_session)):
        fleet = session.get(FleetProfile, fleet_id)
        if fleet is None:
            raise HTTPException(status_code=404, detail="Fleet not found.")
        session.delete(fleet)
        session.commit()

    @application.post("/api/v2/fleets/{fleet_id}/ships")
    def add_fleet_ship(
        fleet_id: str,
        payload: FleetShipUpdate,
        session: Session = Depends(get_session),
    ):
        fleet = session.get(FleetProfile, fleet_id)
        ship = session.get(Ship, payload.ship_id)
        if fleet is None:
            raise HTTPException(status_code=404, detail="Fleet not found.")
        if ship is None or not ship.is_active:
            raise HTTPException(status_code=404, detail="Active ship not found.")
        entry = session.scalar(select(FleetShip).where(
            FleetShip.fleet_id == fleet.id,
            FleetShip.ship_id == ship.id,
        ))
        if entry is None:
            session.add(FleetShip(fleet_id=fleet.id, ship_id=ship.id, quantity=payload.quantity))
        else:
            entry.quantity = payload.quantity
        session.commit()
        fleet = session.scalar(
            select(FleetProfile).where(FleetProfile.id == fleet_id)
            .options(joinedload(FleetProfile.ships).joinedload(FleetShip.ship))
        )
        return fleet_response(fleet)

    @application.delete("/api/v2/fleets/{fleet_id}/ships/{ship_id}", status_code=204)
    def remove_fleet_ship(fleet_id: str, ship_id: str, session: Session = Depends(get_session)):
        entry = session.scalar(select(FleetShip).where(
            FleetShip.fleet_id == fleet_id,
            FleetShip.ship_id == ship_id,
        ))
        if entry is None:
            raise HTTPException(status_code=404, detail="Ship is not in this fleet.")
        session.delete(entry)
        session.commit()

    @application.get("/api/v2/locations")
    def list_locations(
        session: Session = Depends(get_session),
        limit: Annotated[int, Query(ge=1, le=5000)] = 5000,
    ):
        rows = session.scalars(
            select(Location).where(Location.is_active.is_(True)).order_by(Location.name).limit(limit)
        ).all()
        return [
            {
                "id": row.id,
                "name": row.name,
                "planet": row.planet,
                "moon": row.moon,
                "system": row.system,
                "type": row.location_type,
            }
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
                func.coalesce(
                    MarketObservation.observed_at,
                    Report.observed_at,
                ) >= utcnow() - timedelta(days=7),
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
        imported_prices = session.scalars(
            select(CommodityPrice)
            .join(CommodityPrice.commodity)
            .join(CommodityPrice.location)
            .where(
                CommodityPrice.patch_version == patch_version,
                CommodityPrice.server_shard == server_shard,
                CommodityPrice.observed_at >= utcnow() - timedelta(days=7),
            )
            .options(
                joinedload(CommodityPrice.commodity),
                joinedload(CommodityPrice.location),
            )
            .order_by(CommodityPrice.observed_at.desc())
            .limit(50_000)
        ).all()
        merged = {(row["location"], row["commodity"]): row for row in response}
        latest_imported: dict[tuple[str, str], CommodityPrice] = {}
        for price in imported_prices:
            latest_imported.setdefault((price.location.name, price.commodity.name), price)
        for (location_name, commodity_name), price in latest_imported.items():
            row = merged.setdefault((location_name, commodity_name), {
                "location": location_name,
                "commodity": commodity_name,
                "patch_version": patch_version,
                "server_shard": server_shard,
                "buy": [],
                "sell": [],
                "stock": [],
                "demand": [],
            })
            current_timestamps = [
                value.get("latest_observed_at")
                for field in ("buy", "sell", "stock", "demand")
                for value in row[field]
                if value.get("latest_observed_at")
            ]
            if current_timestamps and _utc_iso(price.observed_at) < max(current_timestamps):
                continue
            for key, value in (
                ("buy", price.buy_price),
                ("sell", price.sell_price),
                ("stock", price.supply),
                ("demand", price.demand),
            ):
                row[key] = [{
                        "value": value,
                        "confidence": price.confidence_score,
                        "latest_observed_at": _utc_iso(price.observed_at),
                        "review_status": "live",
                    }] if value is not None else []
        response = list(merged.values())
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
                func.coalesce(
                    MarketObservation.observed_at,
                    Report.observed_at,
                ) >= utcnow() - timedelta(days=days),
            )
            .options(joinedload(MarketObservation.report))
            .order_by(func.coalesce(MarketObservation.observed_at, Report.observed_at).desc())
            .limit(limit)
        )
        history = [
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
        imported = session.scalars(
            select(CommodityPrice)
            .join(CommodityPrice.commodity)
            .join(CommodityPrice.location)
            .where(
                Commodity.name == commodity,
                Location.name == location,
                CommodityPrice.patch_version == patch_version,
                CommodityPrice.server_shard == server_shard,
                CommodityPrice.observed_at >= utcnow() - timedelta(days=days),
            )
            .order_by(CommodityPrice.observed_at.desc())
            .limit(limit)
        ).all()
        history.extend({
            "observed_at": _utc_iso(row.observed_at),
            "buy": row.buy_price,
            "sell": row.sell_price,
            "stock": row.supply,
            "demand": row.demand,
            "ocr_confidence": row.confidence_score,
            "review_status": "live",
        } for row in imported)
        history.sort(key=lambda row: row["observed_at"], reverse=True)
        return history[:limit]

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
                func.coalesce(
                    MarketObservation.observed_at,
                    Report.observed_at,
                ) >= utcnow() - timedelta(days=7),
            )
            .order_by(func.coalesce(MarketObservation.observed_at, Report.observed_at).desc())
            .options(
                joinedload(MarketObservation.report).joinedload(Report.location),
                joinedload(MarketObservation.commodity),
            )
        )
        latest_market: dict[tuple[str, str], tuple[str, dict]] = {}
        for row in session.scalars(statement).all():
            observed_at = row.observed_at or row.report.observed_at
            market = {
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
            }
            key = (market["station"], market["commodity"])
            latest_market.setdefault(key, (_utc_iso(observed_at), market))
        imported_prices = session.scalars(
            select(CommodityPrice)
            .where(
                CommodityPrice.patch_version == payload.patch_version,
                CommodityPrice.server_shard == payload.server_shard,
                CommodityPrice.observed_at >= utcnow() - timedelta(days=7),
            )
            .options(
                joinedload(CommodityPrice.commodity),
                joinedload(CommodityPrice.location),
            )
            .order_by(CommodityPrice.observed_at.desc())
            .limit(50_000)
        ).all()
        for price in imported_prices:
            key = (price.location.name, price.commodity.name)
            observed_at = _utc_iso(price.observed_at)
            if key in latest_market and latest_market[key][0] >= observed_at:
                continue
            latest_market[key] = (observed_at, {
                "station": price.location.name,
                "commodity": price.commodity.name,
                "buy": price.buy_price,
                "sell": price.sell_price,
                "stock": price.supply or 0,
                "demand": price.demand or 0,
                "confidence": price.confidence_score,
                "patch_version": price.patch_version,
                "server_shard": price.server_shard,
            })
        markets = [value for _, value in latest_market.values()]
        options = payload.model_dump(exclude={"current_location", "available_credits"})
        routes = sc_analytics.recommend_routes(markets, **options)
        if payload.current_location:
            routes = [
                route for route in routes
                if route["origin"].casefold() == payload.current_location.casefold()
            ]
        if payload.available_credits is not None:
            routes = [
                route for route in routes
                if route["buy_price"] * route["quantity"] <= payload.available_credits
            ]
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

    return application


app = create_app()
