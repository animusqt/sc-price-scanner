import os
import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple local limiter; uses Redis atomic counters when REDIS_URL is set."""

    def __init__(self, app, requests_per_minute: int = 120):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.redis_url = os.environ.get("REDIS_URL")
        self.trust_proxy_headers = os.environ.get("TRUST_PROXY_HEADERS") == "true"
        self._redis = None
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next):
        if request.url.path in {"/health", "/health/ready"}:
            return await call_next(request)
        is_ocr_request = request.url.path in {"/api/ocr/scan", "/api/ocr-submit"}
        address = request.client.host if request.client else "unknown"
        if self.trust_proxy_headers:
            forwarded = request.headers.get("x-real-ip", "").strip()
            if forwarded and len(forwarded) <= 64:
                address = forwarded
        rate_key = f"{address}:ocr" if is_ocr_request else address
        request_limit = (
            min(self.requests_per_minute, 10)
            if is_ocr_request
            else self.requests_per_minute
        )
        if self.redis_url:
            try:
                if self._redis is None:
                    from redis.asyncio import Redis
                    self._redis = Redis.from_url(self.redis_url, decode_responses=True)
                bucket = int(time.time() // 60)
                count = await self._redis.incr(f"scip:ratelimit:{rate_key}:{bucket}")
                if count == 1:
                    await self._redis.expire(f"scip:ratelimit:{rate_key}:{bucket}", 120)
                if count > request_limit:
                    return JSONResponse(
                        status_code=429,
                        content={"error": {"code": "rate_limited", "message": "Request limit exceeded."}},
                        headers={"Retry-After": "60"},
                    )
            except RedisError:
                return JSONResponse(
                    status_code=503,
                    content={"error": {"code": "rate_limiter_unavailable", "message": "Request limiter unavailable."}},
                )
        else:
            now = time.monotonic()
            hits = self._hits[rate_key]
            while hits and hits[0] <= now - 60:
                hits.popleft()
            if len(hits) >= request_limit:
                return JSONResponse(
                    status_code=429,
                    content={"error": {"code": "rate_limited", "message": "Request limit exceeded."}},
                    headers={"Retry-After": "60"},
                )
            hits.append(now)
        return await call_next(request)
