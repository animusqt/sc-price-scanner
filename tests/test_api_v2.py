import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.websockets import WebSocketDisconnect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))
sys.path.insert(0, str(ROOT / "scanner-engine"))

os.environ["JWT_SECRET"] = "test-only-secret-with-more-than-32-bytes"

from scip_api.database import Base, get_session
from scip_api.catalog_importers import API_IMPORTERS
from scip_api.main import create_app
from scip_api.models import (
    BugReport,
    InventorySnapshot,
    Location,
    PatchVersion,
    Report,
    RoutePenalty,
    User,
)
from scip_api.security import create_access_token


class StubAsyncClient:
    def __init__(self, response):
        self.response = response
        self.url = None
        self.kwargs = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, url, **kwargs):
        self.url = url
        self.kwargs = kwargs
        return self.response


class ApiV2Tests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.Session() as session:
            self.user = User(username="Test Pilot", reputation=0.9)
            session.add(self.user)
            session.commit()
            self.user_id = self.user.id

        app = create_app()

        def override_session():
            with self.Session() as session:
                yield session

        app.dependency_overrides[get_session] = override_session
        self.client = TestClient(app)
        self.headers = {"Authorization": f"Bearer {create_access_token(self.user_id)}"}

    def tearDown(self):
        self.client.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def report(self, **overrides):
        data = {
            "client_report_id": "client-report-0001",
            "location": "Terra Gateway",
            "patch_version": "4.0",
            "server_shard": "A",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "ocr_confidence": 0.9,
            "source": "player-scan",
            "commodities": [
                {"commodity": "Silicon", "buy": 500, "stock": 100},
            ],
        }
        data.update(overrides)
        return data

    def test_json_import_updates_commodity_catalog_and_market_prices(self):
        batch = {
            "commodities": [
                {"name": "Silicon", "category": "Mineral", "base_price": 500},
                {"name": "Scrap", "category": "Recyclable"},
            ],
            "locations": [
                {"name": "Area18", "planet": "ArcCorp", "system": "Stanton"},
                {"name": "Baijini Point", "planet": "ArcCorp", "system": "Stanton"},
            ],
            "prices": [
                {
                    "commodity": "Silicon",
                    "location": "Area18",
                    "buy_price": 500,
                    "supply": 100,
                    "confidence_score": 0.9,
                    "external_id": "silicon-area18-v1",
                },
                {
                    "commodity": "Silicon",
                    "location": "Baijini Point",
                    "sell_price": 900,
                    "demand": 50,
                    "confidence_score": 0.8,
                    "external_id": "silicon-baijini-v1",
                },
            ],
        }
        first = self.client.post("/api/v2/imports/json", json=batch)
        second = self.client.post("/api/v2/imports/json", json=batch)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json()["created"], 6)
        self.assertEqual(second.json()["created"], 0)
        self.assertEqual(second.json()["updated"], 6)

        result = self.client.get("/api/v2/catalog/commodities", params={
            "patch_version": "live",
            "server_shard": "global",
        })
        silicon = next(row for row in result.json() if row["name"] == "Silicon")
        self.assertEqual(silicon["best_buy_location"], "Area18")
        self.assertEqual(silicon["best_sell_location"], "Baijini Point")
        self.assertEqual(silicon["profit"], 400)
        self.assertEqual(silicon["supply"], 100)
        self.assertEqual(silicon["demand"], 50)

    def test_csv_import_validates_and_upserts_ships(self):
        csv_content = (
            "name,manufacturer,role,cargo_capacity_scu,crew_min,crew_max,price_auec\n"
            "C2 Hercules,Crusader,Transport,696,1,2,49200000\n"
        )
        response = self.client.post(
            "/api/v2/imports/csv?record_type=ships",
            content=csv_content,
            headers={"Content-Type": "text/csv"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        ships = self.client.get("/api/v2/ships", params={
            "search": "herc",
            "min_cargo": 500,
            "manufacturer": "crus",
        })
        self.assertEqual(ships.status_code, 200)
        self.assertEqual(ships.json()[0]["name"], "C2 Hercules")

    def test_import_registry_prepares_independent_source_adapters(self):
        response = self.client.get("/api/v2/importers")
        self.assertEqual(response.status_code, 200)
        sources = {item["key"]: item for item in response.json()}
        self.assertTrue(sources["json-import"]["configured"])
        self.assertIn("uex", sources)
        self.assertIn("hauler", sources)
        self.assertIn("erkul", sources)
        self.assertIn("star-citizen-wiki", sources)
        self.assertIn("lovable", sources)

    def test_fleet_crud_calculates_capacity_and_value(self):
        imported = self.client.post("/api/v2/imports/json", json={
            "ships": [{
                "name": "Freelancer MAX",
                "manufacturer": "MISC",
                "cargo_capacity_scu": 120,
                "crew_min": 2,
                "crew_max": 4,
                "price_auec": 2000000,
            }],
        })
        self.assertEqual(imported.status_code, 200, imported.text)
        ship = self.client.get("/api/v2/ships").json()[0]
        created = self.client.post("/api/v2/fleets", json={
            "fleet_name": "Trade Group",
            "description": "Cargo fleet",
        })
        self.assertEqual(created.status_code, 201, created.text)
        fleet_id = created.json()["id"]
        added = self.client.post(f"/api/v2/fleets/{fleet_id}/ships", json={
            "ship_id": ship["id"],
            "quantity": 3,
        })
        self.assertEqual(added.status_code, 200, added.text)
        self.assertEqual(added.json()["total_scu"], 360)
        self.assertEqual(added.json()["total_value_auec"], 6000000)
        updated = self.client.put(f"/api/v2/fleets/{fleet_id}", json={
            "fleet_name": "Haulers",
            "description": "Renamed fleet",
        })
        self.assertEqual(updated.json()["fleet_name"], "Haulers")
        removed = self.client.delete(f"/api/v2/fleets/{fleet_id}/ships/{ship['id']}")
        self.assertEqual(removed.status_code, 204)
        deleted = self.client.delete(f"/api/v2/fleets/{fleet_id}")
        self.assertEqual(deleted.status_code, 204)

    def test_catalog_import_rejects_invalid_price_and_reports_history(self):
        invalid = self.client.post("/api/v2/imports/json", json={
            "prices": [{
                "commodity": "Silicon",
                "location": "Area18",
                "buy_price": -1,
            }],
        })
        self.assertEqual(invalid.status_code, 422)
        history = self.client.get("/api/v2/imports/history")
        self.assertTrue(any(item["status"] == "failed" for item in history.json()))

    def test_imported_latest_market_values_drive_route_planning(self):
        imported = self.client.post("/api/v2/imports/json", json={
            "prices": [
                {
                    "commodity": "Medical Supplies",
                    "location": "Port Olisar",
                    "buy_price": 100,
                    "supply": 75,
                    "confidence_score": 0.95,
                    "patch_version": "4.0",
                    "server_shard": "A",
                },
                {
                    "commodity": "Medical Supplies",
                    "location": "Area18",
                    "sell_price": 250,
                    "demand": 500,
                    "confidence_score": 0.9,
                    "patch_version": "4.0",
                    "server_shard": "A",
                },
            ],
        })
        self.assertEqual(imported.status_code, 200, imported.text)
        response = self.client.post("/api/v2/routes", json={
            "cargo_capacity": 20,
            "minimum_confidence": 0.8,
            "patch_version": "4.0",
            "server_shard": "A",
        })
        self.assertEqual(response.status_code, 200, response.text)
        routes = response.json()["routes"]
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["commodity"], "Medical Supplies")
        self.assertEqual(routes[0]["origin"], "Port Olisar")
        self.assertEqual(routes[0]["destination"], "Area18")
        self.assertEqual(routes[0]["quantity"], 20)
        self.assertEqual(routes[0]["gross_profit"], 3000)
        history = self.client.get("/api/v2/history", params={
            "commodity": "Medical Supplies",
            "location": "Port Olisar",
            "patch_version": "4.0",
            "server_shard": "A",
        })
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()[0]["buy"], 100)
        self.assertEqual(history.json()[0]["review_status"], "live")

    def test_component_imports_are_independent_of_ship_imports(self):
        response = self.client.post("/api/v2/imports/json", json={
            "components": [{
                "name": "CF-337 Panther",
                "type": "Weapon",
                "size": 3,
                "grade": "C",
                "manufacturer": "Behring",
            }],
        })
        self.assertEqual(response.status_code, 200, response.text)
        components = self.client.get("/api/v2/components", params={"search": "Panther"})
        self.assertEqual(components.status_code, 200)
        self.assertEqual(components.json()[0]["name"], "CF-337 Panther")

    def test_external_importers_reject_non_https_and_host_spoofing(self):
        importer = API_IMPORTERS["uex"]
        with patch.dict(os.environ, {"SCIP_IMPORT_UEX_URL": "http://uexcorp.space/data"}):
            with self.assertRaises(ValueError):
                importer.configured_url()
        with patch.dict(os.environ, {"SCIP_IMPORT_UEX_URL": "https://uexcorp.space.evil.test/data"}):
            with self.assertRaises(ValueError):
                importer.configured_url()
        with patch.dict(os.environ, {"SCIP_IMPORT_UEX_URL": "https://api.uexcorp.space/data"}):
            self.assertEqual(importer.configured_url(), "https://api.uexcorp.space/data")

    def test_lovable_import_reads_supabase_commodity_and_ship_tables(self):
        original_async_client = httpx.AsyncClient
        seen = []

        def handler(request):
            seen.append(request)
            self.assertEqual(request.url.query, b"select=%2A")
            self.assertEqual(request.headers["apikey"], "local-test-anon-key")
            self.assertEqual(
                request.headers["authorization"],
                "Bearer local-test-anon-key",
            )
            if request.url.path.endswith("/commodities"):
                rows = [{
                    "id": "website-commodity-id",
                    "name": "Lovable Silicon",
                    "category": "Mineral",
                    "base_price": 500,
                    "created_at": "2026-10-25T12:00:00Z",
                }]
            else:
                rows = [{
                    "id": "website-ship-id",
                    "name": "Lovable Hauler",
                    "manufacturer": "Test Foundry",
                    "role": "Transport",
                    "cargo_capacity_scu": 320,
                    "crew_min": 2,
                    "crew_max": 4,
                    "price_auec": 5_000_000,
                    "created_at": "2026-10-25T12:00:00Z",
                }]
            return httpx.Response(
                200,
                json=rows,
                headers={"content-range": "0-0/1"},
            )

        def client_factory(**kwargs):
            return original_async_client(
                transport=httpx.MockTransport(handler),
                **kwargs,
            )

        with patch.dict(os.environ, {
            "SCIP_IMPORT_LOVABLE_URL": "https://project.supabase.co",
            "SCIP_IMPORT_LOVABLE_ALLOWED_HOST": "project.supabase.co",
            "SCIP_IMPORT_LOVABLE_TOKEN": "local-test-anon-key",
        }), patch("scip_api.catalog_importers.httpx.AsyncClient", side_effect=client_factory):
            response = self.client.post("/api/v2/imports/api/lovable")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["created"], 2)
        self.assertEqual(
            {request.url.path.rsplit("/", 1)[-1] for request in seen},
            {"commodities", "ships"},
        )
        commodities = self.client.get("/api/v2/catalog/commodities").json()
        self.assertEqual(commodities[0]["name"], "Lovable Silicon")
        ships = self.client.get("/api/v2/ships").json()
        self.assertEqual(ships[0]["name"], "Lovable Hauler")
        self.assertEqual(ships[0]["cargo_capacity_scu"], 320)

    def test_fleet_mutation_methods_are_allowed_by_desktop_cors(self):
        for method in ("PUT", "DELETE"):
            response = self.client.options(
                "/api/v2/fleets/example",
                headers={
                    "Origin": "http://127.0.0.1:1420",
                    "Access-Control-Request-Method": method,
                    "Access-Control-Request-Headers": "content-type",
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn(method, response.headers["access-control-allow-methods"])

    def test_report_submission_is_authenticated_and_pending_review(self):
        unauthorized = self.client.post("/api/v2/reports", json=self.report())
        self.assertEqual(unauthorized.status_code, 401)

        response = self.client.post(
            "/api/v2/reports", json=self.report(), headers=self.headers
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["status"], "pending")
        self.assertFalse(response.json()["duplicate"])

    def test_api_root_identifies_desktop_client(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"service": "scip-api", "desktop": "apps/desktop"})

    def test_ocr_submission_requires_configured_website_endpoint(self):
        with patch.dict(os.environ, {"SC_TRUCKERS_API_BASE_URL": ""}):
            response = self.client.post("/api/ocr-submit", json=self.report())
        self.assertEqual(response.status_code, 503)
        self.assertIn("not configured", response.json()["detail"])

    def test_ocr_submission_forwards_validated_report_and_keeps_confirmation(self):
        upstream = Mock(status_code=201)
        upstream.json.return_value = {"id": "website-report-1", "status": "pending"}
        client = StubAsyncClient(upstream)
        with (
            patch.dict(os.environ, {
                "SC_TRUCKERS_API_BASE_URL": "https://market.example.com",
                "SC_TRUCKERS_API_TOKEN": "test-token",
            }),
            patch("scip_api.main.httpx.AsyncClient", return_value=client),
        ):
            response = self.client.post("/api/ocr-submit", json=self.report())

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {
            "status": "accepted",
            "confirmation": {"id": "website-report-1", "status": "pending"},
        })
        self.assertEqual(client.url, "https://market.example.com/api/ocr-submit")
        self.assertEqual(client.kwargs["headers"]["Authorization"], "Bearer test-token")
        self.assertEqual(client.kwargs["json"]["client_report_id"], "client-report-0001")

    def test_ocr_submission_rejects_insecure_remote_endpoint(self):
        with patch.dict(os.environ, {"SC_TRUCKERS_API_BASE_URL": "http://market.example.com"}):
            response = self.client.post("/api/ocr-submit", json=self.report())
        self.assertEqual(response.status_code, 503)
        self.assertIn("HTTPS", response.json()["detail"])

    def test_ocr_submission_rejects_rows_without_market_values(self):
        invalid_report = self.report(commodities=[{"commodity": "Silicon"}])
        response = self.client.post("/api/ocr-submit", json=invalid_report)
        self.assertEqual(response.status_code, 422)
        self.assertIn("no numeric observation", response.text)

    def test_ocr_routes_have_a_separate_rate_limit(self):
        with patch.dict(os.environ, {"SC_TRUCKERS_API_BASE_URL": ""}):
            responses = [
                self.client.post("/api/ocr-submit", json=self.report())
                for _ in range(11)
            ]
        self.assertTrue(all(response.status_code == 503 for response in responses[:10]))
        self.assertEqual(responses[10].status_code, 429)

    def test_ocr_scan_forwards_screenshot_to_local_ocr_service(self):
        upstream = Mock(status_code=200)
        upstream.json.return_value = {
            "commodities": [{"commodity": "Silicon", "buy": 500}],
            "quality": {"ocr_confidence": 0.9},
        }
        client = StubAsyncClient(upstream)
        with (
            patch.dict(os.environ, {"SCIP_OCR_SERVICE_URL": "http://127.0.0.1:8001"}),
            patch("scip_api.main.httpx.AsyncClient", return_value=client),
        ):
            response = self.client.post("/api/ocr/scan", json={"image_base64": "c2Nhbg=="})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(client.url, "http://127.0.0.1:8001/ocr/scan")
        self.assertEqual(response.json()["commodities"][0]["commodity"], "Silicon")

    def test_ocr_scan_reports_unavailable_local_ocr_service(self):
        with patch("scip_api.main.httpx.AsyncClient", side_effect=httpx.ConnectError("offline")):
            response = self.client.post("/api/ocr/scan", json={"image_base64": "c2Nhbg=="})
        self.assertEqual(response.status_code, 503)
        self.assertIn("OCR service is unavailable", response.json()["detail"])

    def test_discord_routes_are_not_exposed(self):
        self.assertEqual(self.client.get("/api/v2/auth/discord/start").status_code, 404)
        self.assertEqual(self.client.get("/api/v2/auth/discord/callback").status_code, 404)
        self.assertEqual(self.client.post("/api/v2/discord/interactions").status_code, 404)

    def test_tauri_desktop_origin_can_read_local_api(self):
        response = self.client.options(
            "/api/v2/markets",
            headers={
                "Origin": "http://tauri.localhost",
                "Access-Control-Request-Method": "GET",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["access-control-allow-origin"],
            "http://tauri.localhost",
        )

        blocked = self.client.options(
            "/api/v2/markets",
            headers={
                "Origin": "https://untrusted.example",
                "Access-Control-Request-Method": "GET",
            },
        )
        self.assertNotIn("access-control-allow-origin", blocked.headers)

    def test_source_registry_is_limited_to_lovable_feed(self):
        response = self.client.get("/api/v2/sources")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([source["key"] for source in response.json()], ["sc-truckers"])
        self.assertFalse(response.json()[0]["enabled"])

    def test_client_report_id_is_idempotent(self):
        first = self.client.post(
            "/api/v2/reports", json=self.report(), headers=self.headers
        )
        second = self.client.post(
            "/api/v2/reports", json=self.report(), headers=self.headers
        )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(second.json()["id"], first.json()["id"])
        self.assertTrue(second.json()["duplicate"])

    def test_report_rejects_invalid_values_and_unknown_fields(self):
        negative = self.report(commodities=[
            {"commodity": "Silicon", "buy": -1},
        ])
        response = self.client.post(
            "/api/v2/reports", json=negative, headers=self.headers
        )
        self.assertEqual(response.status_code, 422)

        extra = self.report(unreviewed=True)
        response = self.client.post(
            "/api/v2/reports", json=extra, headers=self.headers
        )
        self.assertEqual(response.status_code, 422)

    def test_report_rejects_far_future_observation_timestamps(self):
        from datetime import timedelta

        response = self.client.post(
            "/api/v2/reports",
            json=self.report(observed_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()),
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 422)

    def test_market_reads_require_explicit_patch_and_shard(self):
        self.client.post("/api/v2/reports", json=self.report(), headers=self.headers)
        response = self.client.get(
            "/api/v2/markets",
            params={"patch_version": "4.0", "server_shard": "A"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        rows = response.json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["location"], "Terra Gateway")
        self.assertEqual(rows[0]["commodity"], "Silicon")
        self.assertEqual(rows[0]["buy"][0]["value"], 500)
        self.assertEqual(rows[0]["buy"][0]["review_status"], "pending")

        wrong_shard = self.client.get(
            "/api/v2/markets",
            params={"patch_version": "4.0", "server_shard": "B"},
        )
        self.assertEqual(wrong_shard.json(), [])

    def test_route_api_excludes_context_mismatches(self):
        origin = self.client.post("/api/v2/reports", json=self.report(), headers=self.headers)
        destination = self.client.post(
            "/api/v2/reports",
            json=self.report(
                client_report_id="client-report-0002",
                location="Destination",
                commodities=[{"commodity": "Silicon", "sell": 1000}],
            ),
            headers=self.headers,
        )
        self.client.post(
            "/api/v2/reports",
            json=self.report(
                client_report_id="client-report-0003",
                location="Other Patch",
                patch_version="3.24",
                commodities=[{"commodity": "Silicon", "sell": 5000}],
            ),
            headers=self.headers,
        )
        pending_routes = self.client.post("/api/v2/routes", json={
            "cargo_capacity": 10,
            "patch_version": "4.0",
            "server_shard": "A",
        })
        self.assertEqual(pending_routes.json()["routes"], [])

        with self.Session() as session:
            user = session.get(User, self.user_id)
            user.is_moderator = True
            for report_id in (origin.json()["id"], destination.json()["id"]):
                session.get(Report, report_id).review_status = "confirmed"
            session.commit()
        self.headers = {"Authorization": f"Bearer {create_access_token(self.user_id)}"}
        response = self.client.post("/api/v2/routes", json={
            "cargo_capacity": 10,
            "patch_version": "4.0",
            "server_shard": "A",
        })
        self.assertEqual(response.status_code, 200, response.text)
        routes = response.json()["routes"]
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["destination"], "Destination")
        self.assertEqual(routes[0]["quantity"], 10)

        constrained = self.client.post("/api/v2/routes", json={
            "cargo_capacity": 10,
            "current_location": "terra gateway",
            "available_credits": 4_999,
            "patch_version": "4.0",
            "server_shard": "A",
        })
        self.assertEqual(constrained.status_code, 200, constrained.text)
        self.assertEqual(constrained.json()["routes"], [])

        affordable = self.client.post("/api/v2/routes", json={
            "cargo_capacity": 10,
            "current_location": "terra gateway",
            "available_credits": 5_000,
            "patch_version": "4.0",
            "server_shard": "A",
        })
        self.assertEqual(affordable.status_code, 200, affordable.text)
        self.assertEqual(len(affordable.json()["routes"]), 1)

    def test_confirmed_bug_can_apply_reviewed_route_penalty(self):
        origin = self.client.post("/api/v2/reports", json=self.report(), headers=self.headers)
        destination = self.client.post(
            "/api/v2/reports",
            json=self.report(
                client_report_id="client-report-0002",
                location="Destination",
                commodities=[{"commodity": "Silicon", "sell": 1000}],
            ),
            headers=self.headers,
        )
        with self.Session() as session:
            session.get(Report, origin.json()["id"]).review_status = "confirmed"
            session.get(Report, destination.json()["id"]).review_status = "confirmed"
            session.get(User, self.user_id).is_moderator = True
            session.commit()
        self.headers = {"Authorization": f"Bearer {create_access_token(self.user_id)}"}

        bug = self.client.post("/api/v2/bugs", json={
            "category": "terminal-outage",
            "location": "Terra Gateway",
            "patch_version": "4.0",
            "description": "The terminal is consistently unavailable at this location.",
        })
        self.assertEqual(bug.status_code, 201, bug.text)
        review = self.client.post(
            f"/api/v2/bugs/{bug.json()['id']}/review",
            json={"decision": "confirmed", "route_penalty": 0.5},
            headers=self.headers,
        )
        self.assertEqual(review.status_code, 200, review.text)
        routes = self.client.post("/api/v2/routes", json={
            "cargo_capacity": 10,
            "patch_version": "4.0",
            "server_shard": "A",
        }).json()["routes"]
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["bug_risk_penalty"], 0.5)
        self.assertEqual(routes[0]["risk_adjusted_profit"], 2500)
        resolved = self.client.post(
            f"/api/v2/bugs/{bug.json()['id']}/review",
            json={"decision": "resolved"},
            headers=self.headers,
        )
        self.assertEqual(resolved.status_code, 200, resolved.text)
        routes = self.client.post("/api/v2/routes", json={
            "cargo_capacity": 10,
            "patch_version": "4.0",
            "server_shard": "A",
        }).json()["routes"]
        self.assertEqual(routes[0]["bug_risk_penalty"], 0)

    def test_scan_inventory_and_per_commodity_timestamps_are_stored(self):
        observed_at = datetime.now(timezone.utc).isoformat()
        data = self.report(
            inventory=[{"commodity": "Waste", "quantity": 12, "profit_direction": "up"}],
            commodities=[{
                "commodity": "Silicon",
                "buy": 500,
                "stock": 100,
                "observed_at": observed_at,
            }],
        )
        response = self.client.post("/api/v2/reports", json=data, headers=self.headers)
        self.assertEqual(response.status_code, 201, response.text)
        history = self.client.get("/api/v2/history", params={
            "commodity": "Silicon",
            "location": "Terra Gateway",
            "patch_version": "4.0",
            "server_shard": "A",
        })
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()[0]["observed_at"], observed_at)
        with self.Session() as session:
            inventory = session.query(InventorySnapshot).one()
            self.assertEqual(inventory.quantity, 12)
            self.assertEqual(inventory.profit_direction, "up")

    def test_confirmed_history_drives_stock_trend_and_forecast(self):
        from datetime import timedelta

        report_ids = []
        now = datetime.now(timezone.utc)
        for index, stock in enumerate((120, 90, 60)):
            observed_at = (now - timedelta(hours=2 - index)).isoformat()
            response = self.client.post(
                "/api/v2/reports",
                json=self.report(
                    client_report_id=f"trend-report-{index:04d}",
                    observed_at=observed_at,
                    commodities=[{
                        "commodity": "Silicon",
                        "buy": 500 + index * 10,
                        "sell": 900 + index * 10,
                        "stock": stock,
                        "observed_at": observed_at,
                    }],
                ),
                headers=self.headers,
            )
            self.assertEqual(response.status_code, 201, response.text)
            report_ids.append(response.json()["id"])
        with self.Session() as session:
            for report_id in report_ids:
                session.get(Report, report_id).review_status = "confirmed"
            session.commit()

        stock = self.client.get("/api/v2/stock-trends", params={
            "location": "Terra Gateway",
            "commodity": "Silicon",
            "patch_version": "4.0",
            "server_shard": "A",
        })
        self.assertEqual(stock.status_code, 200, stock.text)
        self.assertEqual(stock.json()["latest_stock"], 60)
        self.assertEqual(stock.json()["snapshot_count"], 3)

        forecasts = self.client.get("/api/v2/forecasts", params={
            "location": "Terra Gateway",
            "commodity": "Silicon",
            "patch_version": "4.0",
            "server_shard": "A",
            "horizon_hours": 1,
        })
        self.assertEqual(forecasts.status_code, 200, forecasts.text)
        metrics = {row["metric"] for row in forecasts.json()}
        self.assertIn("stock", metrics)
        self.assertIn("buy", metrics)

    def test_cumulative_report_does_not_refresh_an_old_commodity_row(self):
        from datetime import timedelta

        response = self.client.post(
            "/api/v2/reports",
            json=self.report(commodities=[{
                "commodity": "Silicon",
                "buy": 500,
                "stock": 100,
                "observed_at": (datetime.now(timezone.utc) - timedelta(days=8)).isoformat(),
            }]),
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 201, response.text)
        with self.Session() as session:
            session.get(Report, response.json()["id"]).review_status = "confirmed"
            session.commit()

        markets = self.client.get("/api/v2/markets", params={
            "patch_version": "4.0",
            "server_shard": "A",
        })
        self.assertEqual(markets.status_code, 200, markets.text)
        self.assertEqual(markets.json(), [])

    def test_websocket_ticket_is_single_use(self):
        ticket_response = self.client.post(
            "/api/v2/auth/websocket-ticket",
            headers=self.headers,
        )
        self.assertEqual(ticket_response.status_code, 200, ticket_response.text)
        url = f"/api/v2/ws/markets?ticket={ticket_response.json()['ticket']}"

        with patch("scip_api.database.SessionLocal", self.Session):
            with self.client.websocket_connect(url):
                pass
            with self.assertRaises(WebSocketDisconnect):
                with self.client.websocket_connect(url):
                    pass

    def test_only_moderators_can_confirm_reports(self):
        response = self.client.post(
            "/api/v2/reports", json=self.report(), headers=self.headers
        )
        review = self.client.post(
            f"/api/v2/reports/{response.json()['id']}/review",
            params={"decision": "confirmed"},
            headers=self.headers,
        )
        self.assertEqual(review.status_code, 403)


if __name__ == "__main__":
    unittest.main()
