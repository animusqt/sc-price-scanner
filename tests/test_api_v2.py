import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

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
