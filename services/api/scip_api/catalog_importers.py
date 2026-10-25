"""Source-neutral catalog importers and validation."""

import csv
import io
import ipaddress
import json
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import (
    Commodity,
    CommodityPrice,
    Component,
    Location,
    Ship,
    ShipComponent,
    SourceIngestion,
    utcnow,
)

MAX_IMPORT_BYTES = 10 * 1024 * 1024
MAX_IMPORT_RECORDS = 25_000


class ImportSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CommodityImport(ImportSchema):
    name: str = Field(min_length=1, max_length=160)
    category: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=4000)
    legal_status: str | None = Field(default=None, max_length=40)
    base_price: int | None = Field(default=None, ge=0, le=1_000_000_000)
    aliases: list[str] = Field(default_factory=list, max_length=50)
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name cannot be blank")
        return value


class LocationImport(ImportSchema):
    name: str = Field(min_length=1, max_length=160)
    planet: str | None = Field(default=None, max_length=100)
    moon: str | None = Field(default=None, max_length=100)
    system: str = Field(default="Stanton", min_length=1, max_length=100)
    location_type: str = Field(default="terminal", min_length=1, max_length=40)

    @field_validator("name", "system", "location_type")
    @classmethod
    def trim_required_names(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name fields cannot be blank")
        return value


class ShipImport(ImportSchema):
    name: str = Field(min_length=1, max_length=160)
    manufacturer: str = Field(default="", max_length=120)
    role: str | None = Field(default=None, max_length=100)
    cargo_capacity_scu: float = Field(default=0, ge=0, le=10_000_000)
    quantum_fuel: float | None = Field(default=None, ge=0, le=10_000_000)
    hydrogen_fuel: float | None = Field(default=None, ge=0, le=10_000_000)
    crew_min: int = Field(default=1, ge=0, le=100_000)
    crew_max: int = Field(default=1, ge=0, le=100_000)
    price_auec: int | None = Field(default=None, ge=0, le=1_000_000_000_000)
    price_usd: float | None = Field(default=None, ge=0, le=10_000_000)
    is_flyable: bool = True
    image_url: str | None = Field(default=None, max_length=2048)
    description: str | None = Field(default=None, max_length=4000)
    specifications: dict[str, str | int | float | bool | None] = Field(default_factory=dict, max_length=100)
    patch_version: str = Field(default="live", min_length=1, max_length=80)

    @field_validator("name", "patch_version")
    @classmethod
    def trim_required_ship_fields(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("ship name and patch version cannot be blank")
        return value

    @field_validator("manufacturer")
    @classmethod
    def trim_manufacturer(cls, value: str) -> str:
        return value.strip()

    @field_validator("image_url")
    @classmethod
    def validate_image_url(cls, value: str | None) -> str | None:
        if value is not None:
            parsed = urlsplit(value)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("image_url must be an HTTPS URL")
        return value

    @model_validator(mode="after")
    def validate_crew(self):
        if self.crew_max < self.crew_min:
            raise ValueError("crew_max must be greater than or equal to crew_min")
        return self


class ComponentImport(ImportSchema):
    name: str = Field(min_length=1, max_length=160)
    type: str = Field(min_length=1, max_length=80)
    size: int = Field(default=0, ge=0, le=1000)
    grade: str | None = Field(default=None, max_length=20)
    manufacturer: str = Field(default="", max_length=120)

    @field_validator("name", "type")
    @classmethod
    def trim_component_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("component name and type cannot be blank")
        return value


class PriceImport(ImportSchema):
    commodity: str = Field(min_length=1, max_length=160)
    location: str = Field(min_length=1, max_length=160)
    buy_price: int | None = Field(default=None, ge=0, le=1_000_000_000)
    sell_price: int | None = Field(default=None, ge=0, le=1_000_000_000)
    supply: int | None = Field(default=None, ge=0, le=1_000_000_000)
    demand: int | None = Field(default=None, ge=0, le=1_000_000_000)
    confidence_score: float = Field(default=0.5, ge=0, le=1)
    timestamp: datetime = Field(default_factory=utcnow)
    patch_version: str = Field(default="live", min_length=1, max_length=80)
    server_shard: str = Field(default="global", min_length=1, max_length=100)
    external_id: str | None = Field(default=None, max_length=180)

    @field_validator("commodity", "location", "patch_version", "server_shard")
    @classmethod
    def trim_price_context(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("commodity, location, patch and shard cannot be blank")
        return value

    @model_validator(mode="after")
    def require_price_value(self):
        if all(getattr(self, field) is None for field in ("buy_price", "sell_price", "supply", "demand")):
            raise ValueError("price row must include at least one market value")
        if self.timestamp.tzinfo is None:
            self.timestamp = self.timestamp.replace(tzinfo=timezone.utc)
        if self.timestamp > utcnow() + timedelta(minutes=5):
            raise ValueError("price timestamp cannot be more than five minutes in the future")
        return self


class ComponentLinkImport(ImportSchema):
    ship: str = Field(min_length=1, max_length=160)
    manufacturer: str = Field(default="", max_length=120)
    component: str = Field(min_length=1, max_length=160)
    component_type: str = Field(min_length=1, max_length=80)
    component_size: int = Field(default=0, ge=0, le=1000)
    component_manufacturer: str = Field(default="", max_length=120)


class CatalogImportBatch(ImportSchema):
    commodities: list[CommodityImport] = Field(default_factory=list, max_length=MAX_IMPORT_RECORDS)
    locations: list[LocationImport] = Field(default_factory=list, max_length=MAX_IMPORT_RECORDS)
    ships: list[ShipImport] = Field(default_factory=list, max_length=MAX_IMPORT_RECORDS)
    components: list[ComponentImport] = Field(default_factory=list, max_length=MAX_IMPORT_RECORDS)
    prices: list[PriceImport] = Field(default_factory=list, max_length=MAX_IMPORT_RECORDS)
    ship_components: list[ComponentLinkImport] = Field(default_factory=list, max_length=MAX_IMPORT_RECORDS)

    @model_validator(mode="after")
    def require_records(self):
        if not any(getattr(self, key) for key in CatalogImportBatch.model_fields):
            raise ValueError("import must include at least one catalog record")
        return self


class CatalogImporter:
    source_key: str
    display_name: str
    hostname_suffixes: tuple[str, ...]

    def parse_json(self, payload: object) -> CatalogImportBatch:
        try:
            if isinstance(payload, list):
                payload = {"commodities": payload}
            return CatalogImportBatch.model_validate(payload)
        except ValidationError as exc:
            raise ValueError(f"Catalog JSON is invalid: {exc.errors(include_url=False)}") from exc

    def parse_csv(self, content: str, record_type: str) -> CatalogImportBatch:
        if record_type not in CatalogImportBatch.model_fields:
            raise ValueError(f"Unsupported CSV record type: {record_type}")
        try:
            reader = csv.DictReader(io.StringIO(content))
            if not reader.fieldnames:
                raise ValueError("CSV requires a header row.")
            rows = list(reader)
            if len(rows) > MAX_IMPORT_RECORDS:
                raise ValueError(f"CSV exceeds the {MAX_IMPORT_RECORDS} record limit.")
            return CatalogImportBatch.model_validate({record_type: rows})
        except (csv.Error, ValidationError) as exc:
            raise ValueError(f"Catalog CSV is invalid: {exc}") from exc


class JsonImporter(CatalogImporter):
    def __init__(self, source_key: str = "json-import"):
        self.source_key = source_key
        self.display_name = "JSON file"
        self.hostname_suffixes = ()


class CsvImporter(CatalogImporter):
    def __init__(self, source_key: str = "csv-import"):
        self.source_key = source_key
        self.display_name = "CSV file"
        self.hostname_suffixes = ()


class ApiImporter(CatalogImporter):
    def __init__(self, source_key: str, display_name: str, hostname_suffixes: tuple[str, ...]):
        self.source_key = source_key
        self.display_name = display_name
        self.hostname_suffixes = hostname_suffixes

    def configured_url(self) -> str:
        prefix = f"SCIP_IMPORT_{self.source_key.upper().replace('-', '_')}"
        setting = f"{prefix}_URL"
        url = os.environ.get(setting, "").strip()
        if not url:
            raise ValueError(f"{self.display_name} API URL is not configured ({setting}).")
        parsed = urlsplit(url)
        host = (parsed.hostname or "").rstrip(".").lower()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            is_ip = False
        else:
            is_ip = True
        allowed_suffixes = self.hostname_suffixes
        if not allowed_suffixes:
            configured_host = os.environ.get(f"{prefix}_ALLOWED_HOST", "").strip().lower()
            allowed_suffixes = (configured_host,) if configured_host else ()
        if (
            parsed.scheme != "https"
            or not host
            or is_ip
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not any(host == suffix or host.endswith(f".{suffix}") for suffix in allowed_suffixes)
        ):
            raise ValueError(f"{self.display_name} API URL is not an allowed HTTPS endpoint.")
        return url

    async def fetch_json(self) -> object:
        url = self.configured_url()
        token_setting = f"SCIP_IMPORT_{self.source_key.upper().replace('-', '_')}_TOKEN"
        token = os.environ.get(token_setting, "").strip()
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
            if self.source_key == "lovable":
                headers["apikey"] = token
        try:
            if self.source_key == "lovable":
                return await self._fetch_lovable_catalog(url, headers)
            async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                async with client.stream("GET", url, headers=headers) as response:
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "")
                    if "json" not in content_type.lower():
                        raise ValueError(f"{self.display_name} API did not return JSON.")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_IMPORT_BYTES:
                            raise ValueError("API import exceeds the 10 MiB size limit.")
            try:
                return json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"{self.display_name} API returned invalid JSON.") from exc
        except httpx.HTTPStatusError as exc:
            raise ValueError(f"{self.display_name} API returned HTTP {exc.response.status_code}.") from exc
        except httpx.RequestError as exc:
            raise ValueError(f"{self.display_name} API could not be reached.") from exc

    async def _fetch_lovable_catalog(self, configured_url: str, headers: dict[str, str]) -> object:
        base_url = configured_url.rstrip("/")
        if not base_url.endswith("/rest/v1"):
            base_url = f"{base_url}/rest/v1"
        results: dict[str, list[object]] = {}
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                for table in ("commodities", "ships"):
                    table_rows: list[object] = []
                    total_bytes = 0
                    while len(table_rows) < MAX_IMPORT_RECORDS:
                        page_size = min(1000, MAX_IMPORT_RECORDS - len(table_rows))
                        request_headers = {
                            **headers,
                            "Range": f"{len(table_rows)}-{len(table_rows) + page_size - 1}",
                            "Prefer": "count=exact",
                        }
                        async with client.stream(
                            "GET",
                            f"{base_url}/{table}",
                            params={"select": "*"},
                            headers=request_headers,
                        ) as response:
                            response.raise_for_status()
                            content_type = response.headers.get("content-type", "")
                            if "json" not in content_type.lower():
                                raise ValueError(f"Lovable {table} endpoint did not return JSON.")
                            content_range = response.headers.get("content-range", "")
                            total = content_range.rsplit("/", 1)[-1]
                            if total.isdigit() and int(total) > MAX_IMPORT_RECORDS:
                                raise ValueError(
                                    f"Lovable {table} catalog exceeds the {MAX_IMPORT_RECORDS} row limit."
                                )
                            body = bytearray()
                            async for chunk in response.aiter_bytes():
                                total_bytes += len(chunk)
                                if total_bytes > MAX_IMPORT_BYTES:
                                    raise ValueError(
                                        f"Lovable {table} catalog exceeds the 10 MiB size limit."
                                    )
                                body.extend(chunk)
                        try:
                            page_rows = json.loads(body)
                        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                            raise ValueError(
                                f"Lovable {table} endpoint returned invalid JSON."
                            ) from exc
                        if not isinstance(page_rows, list):
                            raise ValueError(f"Lovable {table} endpoint must return a JSON array.")
                        if any(not isinstance(row, dict) for row in page_rows):
                            raise ValueError(f"Lovable {table} endpoint must return an array of objects.")
                        allowed_fields = (
                            CommodityImport.model_fields
                            if table == "commodities"
                            else ShipImport.model_fields
                        )
                        page_rows = [
                            {key: value for key, value in row.items() if key in allowed_fields}
                            for row in page_rows
                        ]
                        table_rows.extend(page_rows)
                        if not page_rows or len(page_rows) < page_size:
                            break
                        if total.isdigit() and len(table_rows) >= int(total):
                            break
                    results[table] = table_rows
        except httpx.HTTPStatusError as exc:
            table = exc.request.url.path.rsplit("/", 1)[-1]
            raise ValueError(
                f"Lovable {table} endpoint returned HTTP {exc.response.status_code}."
            ) from exc
        except httpx.RequestError as exc:
            raise ValueError("Lovable catalog API could not be reached.") from exc
        if not any(results.values()):
            raise ValueError("Lovable returned no commodity or ship records.")
        return results


API_IMPORTERS = {
    "sc-trade-tools": ApiImporter("sc-trade-tools", "SC Trade Tools", ("sc-trade.tools",)),
    "uex": ApiImporter("uex", "UEX", ("uexcorp.space",)),
    "hauler": ApiImporter("hauler", "Hauler", ("hauler.app",)),
    "erkul": ApiImporter("erkul", "Erkul", ("erkul.games",)),
    "star-citizen-wiki": ApiImporter(
        "star-citizen-wiki", "Star Citizen Wiki", ("starcitizen.tools",)
    ),
    "lovable": ApiImporter("lovable", "Lovable Market API", ()),
}
IMPORTER_SOURCES = {
    "json-import": JsonImporter(),
    "csv-import": CsvImporter(),
    **API_IMPORTERS,
}


def importer_registry() -> list[dict[str, str | bool]]:
    results = [
        {"key": "json-import", "name": "JSON file", "configured": True, "kind": "json"},
        {"key": "csv-import", "name": "CSV file", "configured": True, "kind": "csv"},
    ]
    for importer in API_IMPORTERS.values():
        setting = f"SCIP_IMPORT_{importer.source_key.upper().replace('-', '_')}_URL"
        results.append({
            "key": importer.source_key,
            "name": importer.display_name,
            "configured": bool(os.environ.get(setting)),
            "kind": "api",
        })
    return results


def _unique_existing(session: Session, model, **conditions):
    clauses = [func.lower(getattr(model, key)) == value.strip().casefold() for key, value in conditions.items()]
    return session.scalar(select(model).where(*clauses))


def import_catalog_batch(
    session: Session,
    source: str,
    batch: CatalogImportBatch,
) -> dict[str, int]:
    counts = {"created": 0, "updated": 0, "skipped": 0}
    try:
        for item in batch.commodities:
            row = _unique_existing(session, Commodity, name=item.name)
            if row is None:
                row = Commodity(name=item.name)
                session.add(row)
                counts["created"] += 1
            else:
                counts["updated"] += 1
            for field in ("category", "description", "legal_status", "base_price", "is_active"):
                setattr(row, field, getattr(item, field))
            row.aliases = item.aliases

        for item in batch.locations:
            row = _unique_existing(session, Location, name=item.name)
            if row is None:
                row = Location(name=item.name)
                session.add(row)
                counts["created"] += 1
            else:
                counts["updated"] += 1
            row.planet = item.planet
            row.moon = item.moon
            row.system = item.system
            row.location_type = item.location_type
            row.is_active = True

        for item in batch.ships:
            row = session.scalar(select(Ship).where(
                func.lower(Ship.name) == item.name.casefold(),
                func.lower(Ship.manufacturer) == item.manufacturer.casefold(),
            ))
            if row is None:
                row = Ship(name=item.name, manufacturer=item.manufacturer)
                session.add(row)
                counts["created"] += 1
            else:
                counts["updated"] += 1
            for field in ShipImport.model_fields:
                setattr(row, field, getattr(item, field))
            row.source = source

        for item in batch.components:
            row = session.scalar(select(Component).where(
                func.lower(Component.name) == item.name.casefold(),
                func.lower(Component.type) == item.type.casefold(),
                Component.size == item.size,
                func.lower(Component.manufacturer) == item.manufacturer.casefold(),
            ))
            if row is None:
                row = Component(**item.model_dump(), source=source)
                session.add(row)
                counts["created"] += 1
            else:
                row.grade = item.grade
                row.source = source
                counts["updated"] += 1

        for item in batch.prices:
            commodity = _unique_existing(session, Commodity, name=item.commodity)
            if commodity is None:
                commodity = Commodity(name=item.commodity)
                session.add(commodity)
                session.flush()
                counts["created"] += 1
            location = _unique_existing(session, Location, name=item.location)
            if location is None:
                location = Location(name=item.location)
                session.add(location)
                session.flush()
                counts["created"] += 1
            price = None
            if item.external_id:
                price = session.scalar(select(CommodityPrice).where(
                    CommodityPrice.source == source,
                    CommodityPrice.external_id == item.external_id,
                ))
            if price is None:
                price = CommodityPrice(
                    commodity_id=commodity.id,
                    location_id=location.id,
                    source=source,
                    external_id=item.external_id,
                )
                session.add(price)
                counts["created"] += 1
            else:
                counts["updated"] += 1
            price.commodity_id = commodity.id
            price.location_id = location.id
            price.buy_price = item.buy_price
            price.sell_price = item.sell_price
            price.supply = item.supply
            price.demand = item.demand
            price.confidence_score = item.confidence_score
            price.observed_at = item.timestamp
            price.patch_version = item.patch_version
            price.server_shard = item.server_shard

        session.flush()
        for item in batch.ship_components:
            ship = session.scalar(select(Ship).where(
                func.lower(Ship.name) == item.ship.casefold(),
                func.lower(Ship.manufacturer) == item.manufacturer.casefold(),
            ))
            component = session.scalar(select(Component).where(
                func.lower(Component.name) == item.component.casefold(),
                func.lower(Component.type) == item.component_type.casefold(),
                Component.size == item.component_size,
                func.lower(Component.manufacturer) == item.component_manufacturer.casefold(),
            ))
            if ship is None or component is None:
                counts["skipped"] += 1
                continue
            link = session.scalar(select(ShipComponent).where(
                ShipComponent.ship_id == ship.id,
                ShipComponent.component_id == component.id,
            ))
            if link is None:
                session.add(ShipComponent(ship_id=ship.id, component_id=component.id))
                counts["created"] += 1
            else:
                counts["skipped"] += 1
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ValueError("Catalog import conflicts with existing data; no changes were saved.") from exc
    return counts


def record_ingestion(
    session: Session,
    source: str,
    batch: CatalogImportBatch | None,
    outcome: dict[str, int] | None,
    error: str | None = None,
) -> None:
    records_seen = 0
    if batch is not None:
        records_seen = sum(len(getattr(batch, key)) for key in CatalogImportBatch.model_fields)
    ingestion = SourceIngestion(
        source=source,
        status="failed" if error else "completed",
        records_seen=records_seen,
        result_json=outcome or {},
        error_summary=error[:500] if error else None,
        completed_at=utcnow(),
    )
    if outcome is not None:
        ingestion.records_seen = records_seen
    session.add(ingestion)
    session.commit()
