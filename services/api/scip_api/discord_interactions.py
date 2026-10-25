import json
import os
import re
import time
from datetime import timedelta

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import select
from sqlalchemy.orm import Session

import sc_analytics
from .models import (
    AuditEvent,
    BugReport,
    Commodity,
    Location,
    MarketObservation,
    PatchVersion,
    Report,
    RoutePenalty,
    User,
    utcnow,
)

DISCORD_PING = 1
DISCORD_APPLICATION_COMMAND = 2
DISCORD_PONG = 1
DISCORD_CHANNEL_MESSAGE = 4
EPHEMERAL_FLAG = 64
MAX_INTERACTION_BODY = 1_000_000
SIGNATURE_MAX_AGE_SECONDS = 300

APPLICATION_COMMANDS = [
    {
        "name": "ping",
        "description": "Check whether the SCIP bot is responding.",
        "type": 1,
    },
    {
        "name": "market",
        "description": "Look up recent confirmed commodity prices.",
        "type": 1,
        "options": [
            {"name": "patch", "description": "Game patch, for example 4.0", "type": 3, "required": True, "max_length": 80},
            {"name": "shard", "description": "Server shard, for example LIVE", "type": 3, "required": True, "max_length": 100},
            {"name": "commodity", "description": "Filter by commodity name", "type": 3, "required": False, "max_length": 160},
            {"name": "location", "description": "Filter by station name", "type": 3, "required": False, "max_length": 160},
        ],
    },
    {
        "name": "bug-report",
        "description": "Submit a game bug report for moderator review.",
        "type": 1,
        "options": [
            {
                "name": "category",
                "description": "Bug category",
                "type": 3,
                "required": True,
                "choices": [
                    {"name": "Terminal outage", "value": "terminal-outage"},
                    {"name": "Cargo bug", "value": "cargo-bug"},
                    {"name": "Patch regression", "value": "patch-regression"},
                    {"name": "Other", "value": "other"},
                ],
            },
            {"name": "description", "description": "What happened?", "type": 3, "required": True, "min_length": 10, "max_length": 1000},
            {"name": "location", "description": "Optional station or location", "type": 3, "required": False, "max_length": 160},
            {"name": "patch", "description": "Optional game patch", "type": 3, "required": False, "max_length": 80},
        ],
    },
    {
        "name": "review-bug",
        "description": "Review a bug report (moderators only).",
        "type": 1,
        "options": [
            {"name": "bug_id", "description": "Bug report ID", "type": 3, "required": True, "max_length": 36},
            {
                "name": "decision",
                "description": "Review decision",
                "type": 3,
                "required": True,
                "choices": [
                    {"name": "Confirmed", "value": "confirmed"},
                    {"name": "Rejected", "value": "rejected"},
                    {"name": "Resolved", "value": "resolved"},
                ],
            },
            {"name": "route_penalty", "description": "Risk penalty from 0 to 1 when confirming", "type": 10, "required": False, "min_value": 0, "max_value": 1},
        ],
    },
]


def verify_discord_signature(
    body: bytes,
    signature: str | None,
    timestamp: str | None,
    *,
    public_key: str | None = None,
    now: int | None = None,
) -> bool:
    key_hex = (public_key or os.environ.get("DISCORD_PUBLIC_KEY", "")).strip()
    if (
        len(key_hex) != 64
        or signature is None
        or not re.fullmatch(r"[0-9a-fA-F]{128}", signature)
        or timestamp is None
        or not timestamp.isdecimal()
    ):
        return False
    current_time = int(time.time()) if now is None else now
    issued_at = int(timestamp)
    if abs(current_time - issued_at) > SIGNATURE_MAX_AGE_SECONDS:
        return False
    try:
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(key_hex))
        key.verify(bytes.fromhex(signature), timestamp.encode("ascii") + body)
    except (ValueError, InvalidSignature):
        return False
    return True


def _response(message: str, *, ephemeral: bool = True) -> dict:
    result = {
        "type": DISCORD_CHANNEL_MESSAGE,
        "data": {
            "content": message[:1900],
            "allowed_mentions": {"parse": []},
        },
    }
    if ephemeral:
        result["data"]["flags"] = EPHEMERAL_FLAG
    return result


def _safe_text(value: object) -> str:
    text = str(value).replace("@", "@\u200b")
    for marker in ("\\", "`", "*", "_", "~", "|", ">"):
        text = text.replace(marker, f"\\{marker}")
    return text


def _command_options(data: dict) -> dict[str, object]:
    return {
        option["name"]: option.get("value")
        for option in data.get("options", [])
        if isinstance(option, dict) and "name" in option
    }


def _market_command(options: dict[str, object], session: Session) -> dict:
    patch = str(options.get("patch", "")).strip()
    shard = str(options.get("shard", "")).strip()
    commodity_filter = str(options.get("commodity", "")).strip()
    location_filter = str(options.get("location", "")).strip()
    if not patch or not shard:
        return _response("Both patch and shard are required.")

    statement = (
        select(MarketObservation, Report, Location, Commodity, User)
        .join(MarketObservation.report)
        .join(Report.location)
        .join(MarketObservation.commodity)
        .join(Report.user)
        .where(
            Report.patch_version == patch,
            Report.server_shard == shard,
            Report.review_status == "confirmed",
            MarketObservation.observed_at >= utcnow() - timedelta(days=7),
        )
        .order_by(MarketObservation.observed_at.desc())
        .limit(200)
    )
    if commodity_filter:
        statement = statement.where(Commodity.name.ilike(f"%{commodity_filter}%"))
    if location_filter:
        statement = statement.where(Location.name.ilike(f"%{location_filter}%"))

    lines = []
    seen = set()
    for observation, report, location, commodity, reporter in session.execute(statement):
        key = (location.name, commodity.name)
        if key in seen:
            continue
        seen.add(key)
        confidence = sc_analytics.report_confidence(
            report.ocr_confidence,
            reporter.reputation,
            observation.observed_at or report.observed_at,
            expected_patch=patch,
            report_patch=report.patch_version,
            expected_shard=shard,
            report_shard=report.server_shard,
        )["score"]
        prices = []
        for label, value in (
            ("buy", observation.buy_price),
            ("sell", observation.sell_price),
            ("stock", observation.stock),
        ):
            if value is not None:
                prices.append(f"{label} {value:,}")
        if prices:
            lines.append(
                f"**{_safe_text(commodity.name)}** @ {_safe_text(location.name)} — "
                f"{', '.join(prices)} · {round(confidence * 100)}% confidence"
            )
        if len(lines) >= 10:
            break

    if not lines:
        return _response("No recent confirmed market reports matched those filters.")
    return _response(
        f"Confirmed market reports · patch `{_safe_text(patch)}` · shard `{_safe_text(shard)}`\n"
        + "\n".join(lines)
    )


def _bug_report_command(
    options: dict[str, object],
    discord_user_id: str,
    session: Session,
) -> dict:
    category = str(options.get("category", ""))
    allowed_categories = {"terminal-outage", "cargo-bug", "patch-regression", "other"}
    description = str(options.get("description", "")).strip()
    location_name = str(options.get("location", "")).strip()
    patch_name = str(options.get("patch", "")).strip()
    if category not in allowed_categories or not 10 <= len(description) <= 1000:
        return _response("Provide a valid category and a 10–1000 character description.")

    location_id = None
    if location_name:
        location = session.scalar(select(Location).where(Location.name == location_name))
        if location is None:
            return _response("That location is not in the market database yet.")
        location_id = location.id
    if patch_name and session.get(PatchVersion, patch_name) is None:
        session.add(PatchVersion(id=patch_name, label=patch_name))

    reporter = session.scalar(select(User).where(User.discord_id == discord_user_id))
    bug = BugReport(
        reporter_id=reporter.id if reporter else None,
        category=category,
        location_id=location_id,
        patch_version=patch_name or None,
        description=description,
    )
    session.add(bug)
    session.flush()
    session.add(AuditEvent(
        actor_id=reporter.id if reporter else None,
        action="bug_report.submit.discord",
        target_type="bug_report",
        target_id=bug.id,
        metadata_json={"category": category, "discord_user_id": discord_user_id},
    ))
    session.commit()
    return _response(f"Bug report submitted for moderator review. ID: `{bug.id}`")


def _review_bug_command(
    options: dict[str, object],
    discord_user_id: str,
    session: Session,
) -> dict:
    allowed_moderators = {
        value.strip()
        for value in os.environ.get("SCIP_MODERATOR_DISCORD_IDS", "").split(",")
        if value.strip()
    }
    moderator = session.scalar(select(User).where(User.discord_id == discord_user_id))
    if discord_user_id not in allowed_moderators and not (
        moderator is not None and moderator.is_active and moderator.is_moderator
    ):
        return _response("Moderator permissions are required for this command.")

    bug_id = str(options.get("bug_id", "")).strip()
    decision = str(options.get("decision", ""))
    try:
        penalty = float(options.get("route_penalty", 0) or 0)
    except (TypeError, ValueError):
        return _response("Route penalty must be a number from 0 to 1.")
    if decision not in {"confirmed", "rejected", "resolved"} or not 0 <= penalty <= 1:
        return _response("Use a valid decision and route penalty from 0 to 1.")
    bug = session.get(BugReport, bug_id)
    if bug is None:
        return _response("Bug report not found.")

    bug.status = decision
    prior_penalties = session.scalars(
        select(RoutePenalty).where(RoutePenalty.bug_report_id == bug.id)
    ).all()
    for prior in prior_penalties:
        prior.is_active = False
    if decision == "confirmed" and penalty and bug.location_id and bug.patch_version:
        session.add(RoutePenalty(
            bug_report_id=bug.id,
            location_id=bug.location_id,
            patch_version=bug.patch_version,
            category=bug.category,
            penalty=penalty,
        ))
    session.add(AuditEvent(
        actor_id=moderator.id if moderator else None,
        action=f"bug_report.review.discord.{decision}",
        target_type="bug_report",
        target_id=bug.id,
        metadata_json={
            "discord_user_id": discord_user_id,
            "route_penalty": penalty if decision == "confirmed" else 0,
        },
    ))
    session.commit()
    return _response(f"Bug `{bug.id}` marked **{decision}**.")


def handle_interaction(payload: dict, session: Session) -> dict:
    if payload.get("type") == DISCORD_PING:
        return {"type": DISCORD_PONG}
    if payload.get("type") != DISCORD_APPLICATION_COMMAND:
        return _response("Unsupported Discord interaction.")

    data = payload.get("data")
    member = payload.get("member") or {}
    user = member.get("user") or payload.get("user") or {}
    discord_user_id = str(user.get("id", ""))
    if not isinstance(data, dict) or not discord_user_id:
        return _response("The interaction payload is missing command or user information.")
    options = _command_options(data)
    command = data.get("name")
    if command == "ping":
        return _response("SCIP bot is online.")
    if command == "market":
        return _market_command(options, session)
    if command == "bug-report":
        return _bug_report_command(options, discord_user_id, session)
    if command == "review-bug":
        return _review_bug_command(options, discord_user_id, session)
    return _response("Unknown SCIP command.")


def parse_interaction_body(body: bytes) -> dict:
    if len(body) > MAX_INTERACTION_BODY:
        raise ValueError("Discord interaction payload is too large.")
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise ValueError("Discord interaction payload must be a JSON object.")
    return payload
