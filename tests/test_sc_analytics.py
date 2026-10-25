import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scanner-engine"))
import sc_analytics


NOW = 1_800_000_000


class ReportConfidenceTests(unittest.TestCase):
    def test_recency_decays_by_half_life(self):
        fresh = sc_analytics.report_confidence(
            0.8, 0.5, NOW, now=NOW, half_life_hours=12
        )
        aged = sc_analytics.report_confidence(
            0.8, 0.5, NOW, now=NOW + 12 * 3600, half_life_hours=12
        )
        self.assertAlmostEqual(fresh["score"], 0.4)
        self.assertAlmostEqual(aged["score"], fresh["score"] / 2)

    def test_patch_and_shard_mismatches_zero_the_score(self):
        score = sc_analytics.report_confidence(
            1.0,
            1.0,
            NOW,
            now=NOW,
            expected_patch="4.0",
            report_patch="3.24",
            expected_shard="A",
            report_shard="B",
        )
        self.assertEqual(score["score"], 0)
        self.assertEqual(score["context_match"], 0)

    def test_invalid_confidence_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            sc_analytics.report_confidence(1.1, 0.5, NOW, now=NOW)


class ObservationAggregationTests(unittest.TestCase):
    def observation(self, value, shard="A", patch="4.0", reputation=1.0):
        return {
            "value": value,
            "observed_at": NOW,
            "ocr_confidence": 1.0,
            "reporter_reputation": reputation,
            "server_shard": shard,
            "patch_version": patch,
        }

    def test_weighted_median_resists_outlier_and_filters_context(self):
        observations = [
            self.observation(100),
            self.observation(105),
            self.observation(110),
            self.observation(10000),
            self.observation(500, shard="B"),
            self.observation(600, patch="3.24"),
        ]
        result = sc_analytics.aggregate_observations(
            observations,
            expected_patch="4.0",
            expected_shard="A",
            now=NOW,
        )
        self.assertEqual(result["value"], 105)
        self.assertEqual(result["report_count"], 4)
        self.assertGreater(result["confidence"], 0)
        self.assertLessEqual(result["confidence"], 1)

    def test_no_matching_reports_returns_none(self):
        result = sc_analytics.aggregate_observations(
            [self.observation(100, shard="other")],
            expected_patch="4.0",
            expected_shard="A",
            now=NOW,
        )
        self.assertIsNone(result)

    def test_price_anomaly_detection_uses_only_matching_context(self):
        observations = [
            self.observation(100),
            self.observation(105),
            self.observation(110),
            self.observation(10000),
            self.observation(500, shard="B"),
        ]
        anomalies = sc_analytics.detect_price_anomalies(
            observations,
            expected_patch="4.0",
            expected_shard="A",
        )
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]["value"], 10000)
        self.assertEqual(anomalies[0]["reference_median"], 107.5)


class RouteRecommendationTests(unittest.TestCase):
    def setUp(self):
        self.markets = [
            {
                "station": "Origin",
                "commodity": "Silicon",
                "buy": 500,
                "stock": 100,
                "confidence": 0.9,
                "patch_version": "4.0",
                "server_shard": "A",
            },
            {
                "station": "Destination",
                "commodity": "Silicon",
                "sell": 1000,
                "confidence": 0.8,
                "patch_version": "4.0",
                "server_shard": "A",
            },
            {
                "station": "Wrong Patch",
                "commodity": "Silicon",
                "sell": 2000,
                "confidence": 1.0,
                "patch_version": "3.24",
                "server_shard": "A",
            },
            {
                "station": "Wrong Shard",
                "commodity": "Silicon",
                "sell": 3000,
                "confidence": 1.0,
                "patch_version": "4.0",
                "server_shard": "B",
            },
        ]

    def test_route_profit_accounts_for_quantity_costs_risk_and_confidence(self):
        routes = sc_analytics.recommend_routes(
            self.markets,
            cargo_capacity=10,
            fuel_cost=200,
            route_fees=100,
            route_risk=0.2,
            travel_hours=2,
            server_shard="A",
            patch_version="4.0",
        )
        self.assertEqual(len(routes), 1)
        route = routes[0]
        self.assertEqual(route["quantity"], 10)
        self.assertEqual(route["gross_profit"], 5000)
        self.assertEqual(route["net_profit"], 4700)
        self.assertEqual(route["risk_adjusted_profit"], 3760)
        self.assertEqual(route["risk_adjusted_profit_per_hour"], 1504)

    def test_routes_cannot_mix_patch_or_shard_even_without_explicit_filter(self):
        routes = sc_analytics.recommend_routes(
            self.markets,
            cargo_capacity=10,
        )
        self.assertEqual([route["destination"] for route in routes], ["Destination"])

    def test_low_confidence_route_can_be_excluded(self):
        routes = sc_analytics.recommend_routes(
            self.markets,
            cargo_capacity=10,
            minimum_confidence=0.85,
        )
        self.assertEqual(routes, [])

    def test_invalid_risk_is_rejected(self):
        with self.assertRaises(ValueError):
            sc_analytics.recommend_routes(
                self.markets,
                cargo_capacity=10,
                route_risk=1.1,
            )


class InventoryTrendTests(unittest.TestCase):
    def snapshot(self, stock, observed_at, patch="4.0"):
        return {
            "station": "Origin",
            "commodity": "Silicon",
            "stock": stock,
            "observed_at": observed_at,
            "server_shard": "A",
            "patch_version": patch,
        }

    def test_depletion_estimate_uses_matching_snapshots(self):
        snapshots = [
            self.snapshot(100, NOW - 2 * 3600),
            self.snapshot(60, NOW - 1 * 3600),
            self.snapshot(20, NOW),
        ]
        estimate = sc_analytics.estimate_stock_depletion(snapshots, now=NOW + 60)
        self.assertEqual(estimate["depletion_per_hour"], 40)
        self.assertEqual(estimate["hours_remaining"], 0.5)
        self.assertEqual(estimate["snapshot_count"], 3)

    def test_stale_or_single_snapshot_does_not_predict(self):
        self.assertIsNone(
            sc_analytics.estimate_stock_depletion(
                [self.snapshot(100, NOW - 4 * 3600), self.snapshot(50, NOW - 3 * 3600)],
                now=NOW,
                max_age_hours=2,
            )
        )
        self.assertIsNone(
            sc_analytics.estimate_stock_depletion(
                [self.snapshot(100, NOW)],
                now=NOW,
            )
        )

    def test_mixed_context_snapshots_are_rejected(self):
        with self.assertRaises(ValueError):
            sc_analytics.estimate_stock_depletion(
                [self.snapshot(100, NOW - 3600), self.snapshot(50, NOW, patch="3.24")],
                now=NOW,
            )

    def test_linear_forecast_projects_trend_and_reports_uncertainty(self):
        observations = [
            {"value": 100, "observed_at": NOW - 2 * 3600, "confidence": 0.9},
            {"value": 120, "observed_at": NOW - 3600, "confidence": 0.8},
            {"value": 140, "observed_at": NOW, "confidence": 0.9},
        ]
        forecast = sc_analytics.linear_forecast(
            observations,
            horizon_hours=2,
            now=NOW,
        )
        self.assertEqual(forecast["predicted_value"], 180)
        self.assertEqual(forecast["slope_per_hour"], 20)
        self.assertGreater(forecast["confidence"], 0)
        self.assertLessEqual(forecast["confidence"], 1)

    def test_linear_forecast_rejects_insufficient_or_stale_history(self):
        self.assertIsNone(sc_analytics.linear_forecast([
            {"value": 100, "observed_at": NOW - 3600},
            {"value": 90, "observed_at": NOW},
        ], now=NOW))
        self.assertIsNone(sc_analytics.linear_forecast([
            {"value": 100, "observed_at": NOW - 5 * 3600},
            {"value": 90, "observed_at": NOW - 4 * 3600},
            {"value": 80, "observed_at": NOW - 3 * 3600},
        ], now=NOW, max_age_hours=2))


if __name__ == "__main__":
    unittest.main()
