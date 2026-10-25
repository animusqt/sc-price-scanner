"""Version 2 market intelligence primitives.

These pure functions operate on player-submitted observations. They do not
connect to a central service or treat local OCR output as verified market data.
"""

from datetime import datetime, timezone
import math
from typing import Any, Iterable


def _bounded_number(value: Any, name: str, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number.") from exc
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}.")
    return number


def _timestamp(value: Any) -> float:
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("observed_at must be an epoch or ISO timestamp.") from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    try:
        timestamp = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("observed_at must be an epoch or ISO timestamp.") from exc
    if not math.isfinite(timestamp):
        raise ValueError("observed_at must be finite.")
    return timestamp


def report_confidence(
    ocr_confidence: float,
    reporter_reputation: float,
    observed_at: Any,
    *,
    now: Any = None,
    half_life_hours: float = 24.0,
    expected_patch: str | None = None,
    report_patch: str | None = None,
    expected_shard: str | None = None,
    report_shard: str | None = None,
) -> dict[str, float]:
    """Score a report from OCR quality, reporter history, and recency.

    Patch and shard mismatches are rejected rather than blended. Confidence is
    a ranking signal, not proof that a player report is correct.
    """
    ocr = _bounded_number(ocr_confidence, "ocr_confidence", 0.0, 1.0)
    reputation = _bounded_number(reporter_reputation, "reporter_reputation", 0.0, 1.0)
    half_life = _bounded_number(half_life_hours, "half_life_hours", 0.01, 24.0 * 365.0)
    timestamp = _timestamp(observed_at)
    current_time = _timestamp(now) if now is not None else datetime.now(timezone.utc).timestamp()

    matches_patch = expected_patch is None or report_patch == expected_patch
    matches_shard = expected_shard is None or report_shard == expected_shard
    age_hours = max(0.0, (current_time - timestamp) / 3600.0)
    recency = math.exp(-math.log(2) * age_hours / half_life)
    score = ocr * reputation * recency if matches_patch and matches_shard else 0.0
    return {
        "score": score,
        "ocr": ocr,
        "reputation": reputation,
        "recency": recency,
        "age_hours": age_hours,
        "context_match": float(matches_patch and matches_shard),
    }


def aggregate_observations(
    observations: Iterable[dict[str, Any]],
    *,
    expected_patch: str,
    expected_shard: str,
    now: Any = None,
    half_life_hours: float = 24.0,
) -> dict[str, Any] | None:
    """Return a weighted-median value and a support-aware confidence score."""
    weighted_values: list[tuple[float, float]] = []
    total_weight = 0.0
    weighted_quality = 0.0
    report_count = 0

    for observation in observations:
        if observation.get("patch_version") != expected_patch:
            continue
        if observation.get("server_shard") != expected_shard:
            continue
        value = _bounded_number(observation.get("value"), "value", 0.0, 1e12)
        parts = report_confidence(
            observation.get("ocr_confidence", 0.0),
            observation.get("reporter_reputation", 0.0),
            observation.get("observed_at"),
            now=now,
            half_life_hours=half_life_hours,
            expected_patch=expected_patch,
            report_patch=observation.get("patch_version"),
            expected_shard=expected_shard,
            report_shard=observation.get("server_shard"),
        )
        weight = parts["score"]
        if weight <= 0:
            continue
        weighted_values.append((value, weight))
        total_weight += weight
        weighted_quality += parts["ocr"] * parts["reputation"] * weight
        report_count += 1

    if not weighted_values:
        return None

    weighted_values.sort(key=lambda pair: pair[0])
    halfway = total_weight / 2.0
    accumulated = 0.0
    median = weighted_values[-1][0]
    for value, weight in weighted_values:
        accumulated += weight
        if accumulated >= halfway:
            median = value
            break

    mean_quality = weighted_quality / total_weight
    support = 1.0 - math.exp(-total_weight)
    return {
        "value": median,
        "confidence": max(0.0, min(1.0, mean_quality * support)),
        "report_count": report_count,
        "weight": total_weight,
        "patch_version": expected_patch,
        "server_shard": expected_shard,
    }


def detect_price_anomalies(
    observations: Iterable[dict[str, Any]],
    *,
    expected_patch: str,
    expected_shard: str,
    relative_deviation_threshold: float = 0.5,
) -> list[dict[str, Any]]:
    """Flag large deviations from the compatible-context median for review."""
    threshold = _bounded_number(
        relative_deviation_threshold,
        "relative_deviation_threshold",
        0.01,
        100.0,
    )
    compatible = [
        observation for observation in observations
        if observation.get("patch_version") == expected_patch
        and observation.get("server_shard") == expected_shard
    ]
    if len(compatible) < 3:
        return []

    values = [
        _bounded_number(observation.get("value"), "value", 0.0, 1e12)
        for observation in compatible
    ]
    median = sorted(values)[len(values) // 2] if len(values) % 2 else (
        sorted(values)[len(values) // 2 - 1] + sorted(values)[len(values) // 2]
    ) / 2.0
    scale = max(abs(median), 1.0)
    anomalies = []
    for observation, value in zip(compatible, values):
        deviation = abs(value - median) / scale
        if deviation > threshold:
            anomalies.append({
                "observation": observation,
                "value": value,
                "reference_median": median,
                "relative_deviation": deviation,
                "reason": "large_deviation_from_context_median",
            })
    return anomalies


def recommend_routes(
    markets: Iterable[dict[str, Any]],
    *,
    cargo_capacity: float,
    fuel_cost: float = 0.0,
    route_fees: float = 0.0,
    route_risk: float = 0.0,
    travel_hours: float = 1.0,
    minimum_confidence: float = 0.0,
    server_shard: str | None = None,
    patch_version: str | None = None,
) -> list[dict[str, Any]]:
    """Rank buy-at-origin/sell-at-destination routes by risk-adjusted hourly profit."""
    capacity = _bounded_number(cargo_capacity, "cargo_capacity", 0.000001, 1e9)
    fuel = _bounded_number(fuel_cost, "fuel_cost", 0.0, 1e12)
    fees = _bounded_number(route_fees, "route_fees", 0.0, 1e12)
    risk = _bounded_number(route_risk, "route_risk", 0.0, 1.0)
    hours = _bounded_number(travel_hours, "travel_hours", 0.000001, 1e9)
    confidence_floor = _bounded_number(
        minimum_confidence, "minimum_confidence", 0.0, 1.0
    )

    purchases: dict[str, list[dict[str, Any]]] = {}
    sales: dict[str, list[dict[str, Any]]] = {}
    for market in markets:
        station = str(market.get("station") or "").strip()
        commodity = str(market.get("commodity") or "").strip()
        if not station or not commodity:
            continue
        if server_shard is not None and market.get("server_shard") != server_shard:
            continue
        if patch_version is not None and market.get("patch_version") != patch_version:
            continue

        confidence = _bounded_number(
            market.get("confidence", 0.0), "confidence", 0.0, 1.0
        )
        if confidence < confidence_floor:
            continue
        normalized = dict(market, station=station, commodity=commodity, confidence=confidence)
        if market.get("buy") is not None:
            buy = _bounded_number(market["buy"], "buy", 0.0, 1e12)
            stock = _bounded_number(market.get("stock", 0.0), "stock", 0.0, 1e12)
            if stock > 0:
                purchases.setdefault(commodity.casefold(), []).append(
                    dict(normalized, buy=buy, stock=stock)
                )
        if market.get("sell") is not None:
            sell = _bounded_number(market["sell"], "sell", 0.0, 1e12)
            sales.setdefault(commodity.casefold(), []).append(
                dict(normalized, sell=sell)
            )

    routes = []
    for commodity, origins in purchases.items():
        for origin in origins:
            for destination in sales.get(commodity, []):
                if origin["station"] == destination["station"]:
                    continue
                origin_patch = origin.get("patch_version")
                destination_patch = destination.get("patch_version")
                origin_shard = origin.get("server_shard")
                destination_shard = destination.get("server_shard")
                if origin_patch != destination_patch or origin_shard != destination_shard:
                    continue

                quantity = min(capacity, origin["stock"])
                gross_profit = (destination["sell"] - origin["buy"]) * quantity
                net_profit = gross_profit - fuel - fees
                confidence = min(origin["confidence"], destination["confidence"])
                risk_adjusted_profit = net_profit * (1.0 - risk)
                routes.append({
                    "commodity": origin["commodity"],
                    "origin": origin["station"],
                    "destination": destination["station"],
                    "quantity": quantity,
                    "buy_price": origin["buy"],
                    "sell_price": destination["sell"],
                    "gross_profit": gross_profit,
                    "net_profit": net_profit,
                    "route_risk": risk,
                    "confidence": confidence,
                    "risk_adjusted_profit": risk_adjusted_profit,
                    "risk_adjusted_profit_per_hour": risk_adjusted_profit * confidence / hours,
                    "patch_version": origin_patch,
                    "server_shard": origin_shard,
                })

    routes.sort(
        key=lambda route: (
            -route["risk_adjusted_profit_per_hour"],
            -route["confidence"],
            route["commodity"],
            route["origin"],
            route["destination"],
        )
    )
    return routes


def estimate_stock_depletion(
    snapshots: Iterable[dict[str, Any]],
    *,
    now: Any = None,
    max_age_hours: float = 72.0,
) -> dict[str, Any] | None:
    """Estimate a linear stock depletion rate from matching historical snapshots."""
    max_age = _bounded_number(max_age_hours, "max_age_hours", 0.01, 24.0 * 365.0)
    valid = []
    context = None
    for snapshot in snapshots:
        key = (
            snapshot.get("station"),
            snapshot.get("commodity"),
            snapshot.get("server_shard"),
            snapshot.get("patch_version"),
        )
        if context is None:
            context = key
        elif key != context:
            raise ValueError("Snapshots must match station, commodity, shard, and patch.")
        stock = _bounded_number(snapshot.get("stock"), "stock", 0.0, 1e12)
        timestamp = _timestamp(snapshot.get("observed_at"))
        valid.append((timestamp, stock))

    if len(valid) < 2:
        return None
    valid.sort(key=lambda item: item[0])
    oldest_time, oldest_stock = valid[0]
    latest_time, latest_stock = valid[-1]
    current_time = _timestamp(now) if now is not None else datetime.now(timezone.utc).timestamp()
    age_hours = max(0.0, (current_time - latest_time) / 3600.0)
    elapsed_hours = (latest_time - oldest_time) / 3600.0
    if elapsed_hours <= 0 or age_hours > max_age:
        return None

    depletion_per_hour = max(0.0, (oldest_stock - latest_stock) / elapsed_hours)
    hours_remaining = latest_stock / depletion_per_hour if depletion_per_hour > 0 else None
    return {
        "station": context[0],
        "commodity": context[1],
        "server_shard": context[2],
        "patch_version": context[3],
        "latest_stock": latest_stock,
        "depletion_per_hour": depletion_per_hour,
        "hours_remaining": hours_remaining,
        "observed_at": latest_time,
        "snapshot_count": len(valid),
    }


def linear_forecast(
    observations: Iterable[dict[str, Any]],
    *,
    horizon_hours: float = 24.0,
    now: Any = None,
    max_age_hours: float = 72.0,
) -> dict[str, Any] | None:
    """Project a metric with a transparent linear baseline and bounded confidence."""
    horizon = _bounded_number(horizon_hours, "horizon_hours", 0.01, 24.0 * 30.0)
    max_age = _bounded_number(max_age_hours, "max_age_hours", 0.01, 24.0 * 365.0)
    points = [
        (
            _timestamp(item.get("observed_at")),
            _bounded_number(item.get("value"), "value", 0.0, 1e12),
            _bounded_number(item.get("confidence", 1.0), "confidence", 0.0, 1.0),
        )
        for item in observations
    ]
    if len(points) < 3:
        return None
    points.sort(key=lambda point: point[0])
    current_time = _timestamp(now) if now is not None else datetime.now(timezone.utc).timestamp()
    oldest, latest = points[0][0], points[-1][0]
    age_hours = max(0.0, (current_time - latest) / 3600.0)
    span_hours = (latest - oldest) / 3600.0
    if span_hours <= 0 or age_hours > max_age:
        return None

    origin = points[0][0]
    xs = [(timestamp - origin) / 3600.0 for timestamp, _, _ in points]
    ys = [value for _, value, _ in points]
    x_mean = sum(xs) / len(xs)
    y_mean = sum(ys) / len(ys)
    variance = sum((x - x_mean) ** 2 for x in xs)
    if variance <= 0:
        return None
    slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / variance
    intercept = y_mean - slope * x_mean
    prediction = max(0.0, intercept + slope * (xs[-1] + horizon))
    residual_sum = sum(
        (y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys)
    )
    rmse = math.sqrt(residual_sum / len(points))
    normalized_error = rmse / max(abs(y_mean), 1.0)
    sample_support = min(1.0, len(points) / 8.0)
    freshness = math.exp(-math.log(2) * age_hours / 24.0)
    source_quality = sum(point[2] for point in points) / len(points)
    confidence = source_quality * sample_support * freshness / (1.0 + normalized_error)
    return {
        "predicted_value": prediction,
        "slope_per_hour": slope,
        "confidence": max(0.0, min(1.0, confidence)),
        "rmse": rmse,
        "sample_count": len(points),
        "span_hours": span_hours,
        "horizon_hours": horizon,
        "latest_observed_at": latest,
    }
