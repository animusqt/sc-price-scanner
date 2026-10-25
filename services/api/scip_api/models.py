from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    username: Mapped[str] = mapped_column(String(100), default="")
    reputation: Mapped[float] = mapped_column(Float, default=0.5)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_moderator: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    devices: Mapped[list["Device"]] = relationship(back_populates="user")
    reports: Mapped[list["Report"]] = relationship(back_populates="user")


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("user_id", "device_id", name="uq_user_device"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[str] = mapped_column(String(128))
    device_name: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    user: Mapped[User] = relationship(back_populates="devices")


class Location(Base):
    __tablename__ = "locations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    planet: Mapped[str | None] = mapped_column(String(100))
    moon: Mapped[str | None] = mapped_column(String(100))
    system: Mapped[str] = mapped_column(String(100), default="Stanton")
    location_type: Mapped[str] = mapped_column(String(40), default="terminal")
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("locations.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Commodity(Base):
    __tablename__ = "commodities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    category: Mapped[str | None] = mapped_column(String(100), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    legal_status: Mapped[str | None] = mapped_column(String(40))
    base_price: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CommodityPrice(Base):
    __tablename__ = "commodity_prices"
    __table_args__ = (
        CheckConstraint("buy_price IS NULL OR buy_price >= 0", name="ck_catalog_buy_nonnegative"),
        CheckConstraint("sell_price IS NULL OR sell_price >= 0", name="ck_catalog_sell_nonnegative"),
        CheckConstraint("supply IS NULL OR supply >= 0", name="ck_catalog_supply_nonnegative"),
        CheckConstraint("demand IS NULL OR demand >= 0", name="ck_catalog_demand_nonnegative"),
        CheckConstraint(
            "confidence_score >= 0 AND confidence_score <= 1",
            name="ck_catalog_confidence_range",
        ),
        UniqueConstraint("source", "external_id", name="uq_commodity_price_source_external"),
        Index("ix_commodity_price_latest", "commodity_id", "location_id", "observed_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    commodity_id: Mapped[str] = mapped_column(ForeignKey("commodities.id", ondelete="CASCADE"), index=True)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"), index=True)
    buy_price: Mapped[int | None] = mapped_column(Integer)
    sell_price: Mapped[int | None] = mapped_column(Integer)
    supply: Mapped[int | None] = mapped_column(Integer)
    demand: Mapped[int | None] = mapped_column(Integer)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.5)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    patch_version: Mapped[str] = mapped_column(String(80), default="live", index=True)
    server_shard: Mapped[str] = mapped_column(String(100), default="global", index=True)
    source: Mapped[str] = mapped_column(String(60), index=True)
    external_id: Mapped[str | None] = mapped_column(String(180))
    commodity: Mapped[Commodity] = relationship()
    location: Mapped[Location] = relationship()


class Ship(Base):
    __tablename__ = "ships"
    __table_args__ = (
        UniqueConstraint("manufacturer", "name", name="uq_ship_manufacturer_name"),
        CheckConstraint("cargo_capacity_scu >= 0", name="ck_ship_capacity_nonnegative"),
        CheckConstraint("crew_min >= 0 AND crew_max >= crew_min", name="ck_ship_crew_range"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    manufacturer: Mapped[str] = mapped_column(String(120), default="")
    name: Mapped[str] = mapped_column(String(160), index=True)
    role: Mapped[str | None] = mapped_column(String(100), index=True)
    cargo_capacity_scu: Mapped[float] = mapped_column(Float, default=0)
    quantum_fuel: Mapped[float | None] = mapped_column(Float)
    hydrogen_fuel: Mapped[float | None] = mapped_column(Float)
    crew_min: Mapped[int] = mapped_column(Integer, default=1)
    crew_max: Mapped[int] = mapped_column(Integer, default=1)
    price_auec: Mapped[int | None] = mapped_column(Integer)
    price_usd: Mapped[float | None] = mapped_column(Float)
    is_flyable: Mapped[bool] = mapped_column(Boolean, default=True)
    image_url: Mapped[str | None] = mapped_column(String(2048))
    description: Mapped[str | None] = mapped_column(Text)
    specifications: Mapped[dict] = mapped_column(JSON, default=dict)
    patch_version: Mapped[str] = mapped_column(String(80), default="live", index=True)
    source: Mapped[str] = mapped_column(String(60), default="manual")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Component(Base):
    __tablename__ = "components"
    __table_args__ = (
        UniqueConstraint("name", "type", "size", "manufacturer", name="uq_component_identity"),
        CheckConstraint("size >= 0", name="ck_component_size_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(160), index=True)
    type: Mapped[str] = mapped_column(String(80), index=True)
    size: Mapped[int] = mapped_column(Integer, default=0)
    grade: Mapped[str | None] = mapped_column(String(20))
    manufacturer: Mapped[str] = mapped_column(String(120), default="")
    source: Mapped[str] = mapped_column(String(60), default="manual")


class ShipComponent(Base):
    __tablename__ = "ship_components"
    __table_args__ = (UniqueConstraint("ship_id", "component_id", name="uq_ship_component"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    ship_id: Mapped[str] = mapped_column(ForeignKey("ships.id", ondelete="CASCADE"), index=True)
    component_id: Mapped[str] = mapped_column(ForeignKey("components.id", ondelete="CASCADE"), index=True)


class FleetProfile(Base):
    __tablename__ = "fleet_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    fleet_name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    ships: Mapped[list["FleetShip"]] = relationship(
        back_populates="fleet", cascade="all, delete-orphan"
    )


class FleetShip(Base):
    __tablename__ = "fleet_ships"
    __table_args__ = (
        UniqueConstraint("fleet_id", "ship_id", name="uq_fleet_ship"),
        CheckConstraint("quantity > 0", name="ck_fleet_ship_quantity_positive"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    fleet_id: Mapped[str] = mapped_column(ForeignKey("fleet_profiles.id", ondelete="CASCADE"), index=True)
    ship_id: Mapped[str] = mapped_column(ForeignKey("ships.id", ondelete="CASCADE"), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    fleet: Mapped[FleetProfile] = relationship(back_populates="ships")
    ship: Mapped[Ship] = relationship()


class PatchVersion(Base):
    __tablename__ = "patch_versions"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    label: Mapped[str] = mapped_column(String(120), default="")
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ServerShard(Base):
    __tablename__ = "server_shards"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    label: Mapped[str] = mapped_column(String(120), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (
        UniqueConstraint("user_id", "client_report_id", name="uq_report_client_id"),
        CheckConstraint("ocr_confidence >= 0 AND ocr_confidence <= 1", name="ck_report_ocr_confidence"),
        Index("ix_reports_context_time", "patch_version", "server_shard", "observed_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    client_report_id: Mapped[str] = mapped_column(String(100))
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"), index=True)
    patch_version: Mapped[str] = mapped_column(ForeignKey("patch_versions.id"), index=True)
    server_shard: Mapped[str] = mapped_column(ForeignKey("server_shards.id"), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ocr_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(40), default="player-scan")
    review_status: Mapped[str] = mapped_column(String(30), default="pending")
    user: Mapped[User] = relationship(back_populates="reports")
    location: Mapped[Location] = relationship()
    observations: Mapped[list["MarketObservation"]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )


class MarketObservation(Base):
    __tablename__ = "market_observations"
    __table_args__ = (
        CheckConstraint("buy_price IS NULL OR buy_price >= 0", name="ck_market_buy_nonnegative"),
        CheckConstraint("sell_price IS NULL OR sell_price >= 0", name="ck_market_sell_nonnegative"),
        CheckConstraint("stock IS NULL OR stock >= 0", name="ck_market_stock_nonnegative"),
        Index("ix_market_commodity_report", "commodity_id", "report_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), index=True)
    commodity_id: Mapped[str] = mapped_column(ForeignKey("commodities.id"), index=True)
    buy_price: Mapped[int | None] = mapped_column(Integer)
    sell_price: Mapped[int | None] = mapped_column(Integer)
    stock: Mapped[int | None] = mapped_column(Integer)
    demand: Mapped[int | None] = mapped_column(Integer)
    demand_label: Mapped[str | None] = mapped_column(String(80))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    report: Mapped[Report] = relationship(back_populates="observations")
    commodity: Mapped[Commodity] = relationship()


class InventorySnapshot(Base):
    __tablename__ = "inventory_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), index=True)
    commodity_id: Mapped[str] = mapped_column(ForeignKey("commodities.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    profit_direction: Mapped[str | None] = mapped_column(String(12))


class ConfidenceScore(Base):
    __tablename__ = "confidence_scores"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    observation_id: Mapped[str] = mapped_column(
        ForeignKey("market_observations.id", ondelete="CASCADE"), index=True
    )
    algorithm_version: Mapped[str] = mapped_column(String(30))
    score: Mapped[float] = mapped_column(Float)
    components: Mapped[dict] = mapped_column(JSON, default=dict)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RouteHistory(Base):
    __tablename__ = "route_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    request: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)
    algorithm_version: Mapped[str] = mapped_column(String(30), default="2.0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BugReport(Base):
    __tablename__ = "bug_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    reporter_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    category: Mapped[str] = mapped_column(String(40), index=True)
    location_id: Mapped[str | None] = mapped_column(ForeignKey("locations.id"))
    patch_version: Mapped[str | None] = mapped_column(ForeignKey("patch_versions.id"))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="open", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RoutePenalty(Base):
    __tablename__ = "route_penalties"
    __table_args__ = (
        CheckConstraint("penalty >= 0 AND penalty <= 1", name="ck_route_penalty_range"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    bug_report_id: Mapped[str | None] = mapped_column(
        ForeignKey("bug_reports.id", ondelete="CASCADE"), index=True
    )
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"), index=True)
    patch_version: Mapped[str] = mapped_column(ForeignKey("patch_versions.id"), index=True)
    category: Mapped[str] = mapped_column(String(40))
    penalty: Mapped[float] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    target_type: Mapped[str] = mapped_column(String(80))
    target_id: Mapped[str] = mapped_column(String(100))
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RefreshSession(Base):
    __tablename__ = "refresh_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WebSocketTicket(Base):
    __tablename__ = "websocket_tickets"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TelemetryEvent(Base):
    __tablename__ = "telemetry_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    event_name: Mapped[str] = mapped_column(String(80), index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SupporterProfile(Base):
    __tablename__ = "supporter_profiles"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    badge_name: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(30), default="inactive", index=True)
    provider: Mapped[str | None] = mapped_column(String(40))
    provider_reference: Mapped[str | None] = mapped_column(String(160), unique=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ForecastRecord(Base):
    __tablename__ = "forecast_records"
    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_forecast_confidence"),
        Index("ix_forecasts_context", "location_id", "commodity_id", "patch_version", "server_shard"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id"), index=True)
    commodity_id: Mapped[str] = mapped_column(ForeignKey("commodities.id"), index=True)
    patch_version: Mapped[str] = mapped_column(ForeignKey("patch_versions.id"), index=True)
    server_shard: Mapped[str] = mapped_column(ForeignKey("server_shards.id"), index=True)
    metric: Mapped[str] = mapped_column(String(20))
    predicted_value: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    algorithm_version: Mapped[str] = mapped_column(String(30))
    target_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SourceIngestion(Base):
    __tablename__ = "source_ingestions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source: Mapped[str] = mapped_column(String(60), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    records_seen: Mapped[int] = mapped_column(Integer, default=0)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error_summary: Mapped[str | None] = mapped_column(String(500))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
