"""Privacy-safe request tracing and browser Core Web Vitals ingestion."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import threading
import time
import uuid
from collections import OrderedDict, deque
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError, field_validator

_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_TIMING_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_STATIC_RUM_ROUTES = frozenset({
    "/",
    "/agents",
    "/board",
    "/builder",
    "/forgot",
    "/gallery",
    "/guide",
    "/leaderboard",
    "/login",
    "/mypage",
    "/news",
    "/reset",
    "/runner",
})
# Uvicorn configures this logger in both local and Render deployments, so these
# structured records are emitted without adding a duplicate process-wide handler.
_logger = logging.getLogger("uvicorn.error")


def _bounded_int_setting(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        configured = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        configured = default
    return max(minimum, min(maximum, configured))


RUM_MAX_BODY_BYTES = _bounded_int_setting(
    "RUM_MAX_BODY_BYTES", default=4_096, minimum=1_024, maximum=32_768
)
_RUM_CLIENT_RATE = _bounded_int_setting(
    "RUM_CLIENT_RATE_PER_MINUTE", default=30, minimum=1, maximum=600
)
_RUM_GLOBAL_RATE = _bounded_int_setting(
    "RUM_GLOBAL_RATE_PER_MINUTE", default=600, minimum=10, maximum=10_000
)


class SlidingWindowRateLimiter:
    """Small in-process limiter with bounded client-key cardinality."""

    def __init__(self, *, limit: int, window_seconds: float, max_keys: int) -> None:
        self.limit = max(1, int(limit))
        self.window_seconds = max(1.0, float(window_seconds))
        self.max_keys = max(1, int(max_keys))
        self._events: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def retry_after(self, key: str, *, now: float | None = None) -> int:
        current = time.monotonic() if now is None else float(now)
        with self._lock:
            events = self._events.get(key)
            if events is None:
                if len(self._events) >= self.max_keys:
                    self._events.popitem(last=False)
                events = deque()
                self._events[key] = events
            else:
                self._events.move_to_end(key)

            cutoff = current - self.window_seconds
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self.limit:
                return max(1, math.ceil(events[0] + self.window_seconds - current))
            events.append(current)
            return 0


_rum_client_limiter = SlidingWindowRateLimiter(
    limit=_RUM_CLIENT_RATE,
    window_seconds=60,
    max_keys=4_096,
)
_rum_global_limiter = SlidingWindowRateLimiter(
    limit=_RUM_GLOBAL_RATE,
    window_seconds=60,
    max_keys=1,
)


@dataclass
class TraceState:
    request_id: str
    spans: dict[str, list[float]] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    active: bool = True


_trace: ContextVar[TraceState | None] = ContextVar("gg_parrot_trace", default=None)


def normalize_request_id(value: str | None) -> str:
    candidate = str(value or "").strip()
    if _REQUEST_ID.fullmatch(candidate):
        return candidate
    return uuid.uuid4().hex


def begin_trace(request_id: str | None = None) -> Token:
    return _trace.set(TraceState(normalize_request_id(request_id)))


def end_trace(token: Token) -> None:
    state = _trace.get()
    if state is not None:
        with state.lock:
            state.active = False
    _trace.reset(token)


def record_timing(name: str, duration_ms: float) -> None:
    state = _trace.get()
    if state is None or not _TIMING_NAME.fullmatch(name):
        return
    value = float(duration_ms)
    if not math.isfinite(value) or value < 0:
        return
    with state.lock:
        if not state.active:
            return
        state.spans.setdefault(name, []).append(value)


@contextmanager
def timed_operation(name: str):
    started = time.perf_counter()
    try:
        yield
    finally:
        record_timing(name, (time.perf_counter() - started) * 1000.0)


def server_timing_header(total_ms: float) -> str:
    parts = [f"app;dur={max(0.0, total_ms):.2f}"]
    state = _trace.get()
    if state is None or not state.active:
        return parts[0]
    with state.lock:
        spans = {name: list(values) for name, values in state.spans.items()}
    for name in sorted(spans):
        values = spans[name]
        count = len(values)
        label = "call" if count == 1 else "calls"
        parts.append(f'{name};dur={sum(values):.2f};desc="{count} {label}"')
    return ", ".join(parts)


def _request_header(scope: dict, name: bytes) -> str | None:
    for key, value in scope.get("headers") or []:
        if key.lower() == name:
            return value.decode("latin-1", errors="ignore")
    return None


class ObservabilityMiddleware:
    """Pure ASGI middleware adding request IDs, Server-Timing, and JSON logs."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request_id = normalize_request_id(_request_header(scope, b"x-request-id"))
        token = begin_trace(request_id)
        started = time.perf_counter()
        status = 500

        async def traced_send(message):
            nonlocal status
            if message.get("type") == "http.response.start":
                status = int(message.get("status", 500))
                elapsed = (time.perf_counter() - started) * 1000.0
                headers = list(message.get("headers") or [])
                headers.append((b"x-request-id", request_id.encode("ascii")))
                headers.append((b"server-timing", server_timing_header(elapsed).encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        error_name = None
        try:
            await self.app(scope, receive, traced_send)
        except BaseException as error:
            error_name = type(error).__name__
            raise
        finally:
            elapsed = (time.perf_counter() - started) * 1000.0
            state = _trace.get()
            with state.lock:
                spans = {
                    name: {"duration_ms": round(sum(values), 2), "count": len(values)}
                    for name, values in state.spans.items()
                }
            if str(scope.get("path") or "").startswith("/api/"):
                _logger.info(json.dumps({
                    "event": "http_request",
                    "request_id": request_id,
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "status": status,
                    "duration_ms": round(elapsed, 2),
                    "spans": spans,
                    "error": error_name,
                }, ensure_ascii=False, separators=(",", ":")))
            end_trace(token)


def observe_application(
    app,
    *,
    allow_origins: list[str],
    allow_methods: list[str],
    allow_headers: list[str],
):
    """Wrap the complete ASGI app so even framework 500s retain CORS/tracing.

    Starlette's own error middleware is installed around user middleware. The
    wrappers therefore have to be outside the FastAPI instance, not registered
    with ``add_middleware``, to see its final uncaught-error response.
    """
    cors_app = CORSMiddleware(
        app,
        allow_origins=allow_origins,
        allow_methods=allow_methods,
        allow_headers=allow_headers,
    )
    return ObservabilityMiddleware(cors_app)


def normalize_rum_route(value: str) -> str:
    """Map browser paths to the project's finite, anonymous route templates."""
    if value in _STATIC_RUM_ROUTES:
        return value
    if re.fullmatch(r"/s/[^/]+", value):
        return "/s/:id"
    if re.fullmatch(r"/board/[^/]+", value):
        return "/board/:id"
    return "/other"


class RumMetric(BaseModel):
    name: Literal["CLS", "INP", "LCP", "FCP", "TTFB"]
    value: float = Field(ge=0, le=600_000)
    rating: Literal["good", "needs-improvement", "poor"]


class RumBatch(BaseModel):
    page_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    route: str = Field(min_length=1, max_length=200)
    metrics: list[RumMetric] = Field(min_length=1, max_length=10)

    @field_validator("route")
    @classmethod
    def route_is_anonymous_path(cls, value: str) -> str:
        if not value.startswith("/") or "?" in value or "#" in value:
            raise ValueError("route must be a path without query or fragment")
        return normalize_rum_route(value)


router = APIRouter(prefix="/api/observability", tags=["observability"])


def _enforce_rum_rate_limit(request: Request) -> None:
    client = request.client.host if request.client is not None else "unknown"
    client_retry_after = _rum_client_limiter.retry_after(client)
    if client_retry_after:
        raise HTTPException(
            status_code=429,
            detail="RUM rate limit exceeded",
            headers={"Retry-After": str(client_retry_after)},
        )

    # Only requests that pass their per-client budget may consume the shared
    # process budget. Otherwise one noisy client can starve every other visitor.
    global_retry_after = _rum_global_limiter.retry_after("global")
    if global_retry_after:
        raise HTTPException(
            status_code=429,
            detail="RUM rate limit exceeded",
            headers={"Retry-After": str(global_retry_after)},
        )


async def _read_bounded_body(request: Request) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_size = int(content_length)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="Invalid Content-Length") from error
        if declared_size < 0:
            raise HTTPException(status_code=400, detail="Invalid Content-Length")
        if declared_size > RUM_MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="RUM payload too large")

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > RUM_MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="RUM payload too large")
        body.extend(chunk)
    return bytes(body)


@router.post("/rum", status_code=204)
async def ingest_rum(request: Request) -> Response:
    _enforce_rum_rate_limit(request)
    raw_body = await _read_bounded_body(request)
    try:
        batch = RumBatch.model_validate_json(raw_body)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail="Invalid RUM payload") from error

    log_record = json.dumps({
        "event": "browser_web_vitals",
        "page_id": batch.page_id,
        "route": batch.route,
        "metrics": [metric.model_dump() for metric in batch.metrics],
    }, ensure_ascii=False, separators=(",", ":"))
    # stdout/log drains can block briefly under backpressure; keep that work off
    # the event loop after the request has passed both memory and rate bounds.
    await asyncio.to_thread(_logger.info, log_record)
    return Response(status_code=204)
