from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


def validate_observation_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    if value > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("observation time cannot be more than five minutes in the future")
    return value


class MarketRowInput(StrictSchema):
    commodity: str = Field(min_length=1, max_length=160)
    observed_at: datetime | None = None
    buy: int | None = Field(default=None, ge=0, le=1_000_000_000)
    sell: int | None = Field(default=None, ge=0, le=1_000_000_000)
    stock: int | None = Field(default=None, ge=0, le=1_000_000_000)
    demand: int | None = Field(default=None, ge=0, le=1_000_000_000)
    demand_label: str | None = Field(default=None, max_length=80)

    @field_validator("observed_at")
    @classmethod
    def validate_row_observed_at(cls, value: datetime | None) -> datetime | None:
        return validate_observation_time(value) if value is not None else None

    @field_validator("commodity")
    @classmethod
    def trim_commodity(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("commodity cannot be blank")
        return value

    @field_validator("buy", "sell", "stock", "demand")
    @classmethod
    def require_observation_value(cls, value: int | None, info):
        return value


class InventoryRowInput(StrictSchema):
    commodity: str = Field(min_length=1, max_length=160)
    quantity: int = Field(gt=0, le=1_000_000)
    profit_direction: Literal["up", "down"] | None = None


class ReportCreate(StrictSchema):
    client_report_id: str = Field(min_length=8, max_length=100)
    location: str = Field(min_length=1, max_length=160)
    patch_version: str = Field(min_length=1, max_length=80)
    server_shard: str = Field(min_length=1, max_length=100)
    observed_at: datetime
    ocr_confidence: float = Field(ge=0.0, le=1.0)
    source: Literal["player-scan", "manual"] = "player-scan"
    commodities: list[MarketRowInput] = Field(min_length=1, max_length=500)
    inventory: list[InventoryRowInput] = Field(default_factory=list, max_length=100)

    @field_validator("observed_at")
    @classmethod
    def validate_report_observed_at(cls, value: datetime) -> datetime:
        return validate_observation_time(value)

    @field_validator("location", "patch_version", "server_shard")
    @classmethod
    def trim_context(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("context fields cannot be blank")
        return value


class OcrSubmitRequest(ReportCreate):
    source: Literal["player-scan"] = "player-scan"

    @model_validator(mode="after")
    def require_numeric_market_data(self):
        for row in self.commodities:
            if all(getattr(row, field) is None for field in ("buy", "sell", "stock", "demand")):
                raise ValueError(f"{row.commodity} has no numeric observation.")
        return self


class OcrImageRequest(StrictSchema):
    image_base64: str = Field(min_length=1, max_length=28_000_000)


class TelemetryCreate(StrictSchema):
    event_name: str = Field(min_length=1, max_length=80)
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict, max_length=20)

    @field_validator("metadata")
    @classmethod
    def limit_telemetry_data(cls, value):
        forbidden = ("token", "password", "secret", "email", "ip", "screenshot", "credential")
        for key, item in value.items():
            if len(key) > 40 or any(word in key.casefold() for word in forbidden):
                raise ValueError("telemetry metadata contains a disallowed key")
            if isinstance(item, str) and len(item) > 200:
                raise ValueError("telemetry string values are limited to 200 characters")
        return value


class DeviceCreate(StrictSchema):
    device_id: str = Field(min_length=16, max_length=128)
    device_name: str = Field(default="", max_length=100)


class RefreshRequest(StrictSchema):
    refresh_token: str = Field(min_length=32, max_length=256)


class RouteRequest(StrictSchema):
    cargo_capacity: float = Field(gt=0, le=1_000_000)
    current_location: str | None = Field(default=None, max_length=160)
    available_credits: float | None = Field(default=None, ge=0, le=1_000_000_000_000)
    fuel_cost: float = Field(default=0, ge=0)
    route_fees: float = Field(default=0, ge=0)
    route_risk: float = Field(default=0, ge=0, le=1)
    travel_hours: float = Field(default=1, gt=0)
    minimum_confidence: float = Field(default=0, ge=0, le=1)
    server_shard: str = Field(min_length=1, max_length=100)
    patch_version: str = Field(min_length=1, max_length=80)

    @field_validator("current_location")
    @classmethod
    def trim_current_location(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class BugReportCreate(StrictSchema):
    category: Literal["terminal-outage", "cargo-bug", "patch-regression", "other"]
    location: str | None = Field(default=None, max_length=160)
    patch_version: str | None = Field(default=None, max_length=80)
    description: str = Field(min_length=10, max_length=4000)


class BugReview(StrictSchema):
    decision: Literal["confirmed", "rejected", "resolved"]
    route_penalty: float = Field(default=0.0, ge=0.0, le=1.0)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class RouteResponse(BaseModel):
    routes: list[dict]
    algorithm_version: str = "2.0"
