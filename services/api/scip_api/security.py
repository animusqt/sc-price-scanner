import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any


class SecurityConfigurationError(RuntimeError):
    pass


def signing_secret() -> bytes:
    raw = os.environ.get("JWT_SECRET", "")
    if len(raw.encode("utf-8")) < 32:
        if os.environ.get("APP_ENV") == "production":
            raise SecurityConfigurationError("JWT_SECRET must be at least 32 bytes in production.")
        raw = "local-development-only-secret-change-before-deploy"
    return raw.encode("utf-8")


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def create_access_token(user_id: str, *, expires_seconds: int = 900) -> str:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"sub": user_id, "iat": now, "exp": now + expires_seconds}
    body = f"{_b64encode(json.dumps(header, separators=(',', ':')).encode())}.{_b64encode(json.dumps(payload, separators=(',', ':')).encode())}"
    signature = hmac.new(signing_secret(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64encode(signature)}"


def verify_access_token(token: str) -> dict[str, Any] | None:
    try:
        header, payload, signature = token.split(".")
        body = f"{header}.{payload}"
        expected = hmac.new(signing_secret(), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64decode(signature)):
            return None
        claims = json.loads(_b64decode(payload))
        if claims.get("exp", 0) <= int(time.time()) or not claims.get("sub"):
            return None
        if json.loads(_b64decode(header)).get("alg") != "HS256":
            return None
        return claims
    except (ValueError, TypeError, json.JSONDecodeError, SecurityConfigurationError):
        return None


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def utc_after(seconds: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)
