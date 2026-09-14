"""Explicit browser/CDN policy. Private API responses never enter shared caches."""
from __future__ import annotations

import hashlib
import json
import re

from starlette.requests import Request
from starlette.responses import Response

PRIVATE = "private, no-store"
_HASHED_ASSET = re.compile(r"^/assets/[^/]+-[A-Za-z0-9_-]{8,}\.(?:js|css|woff2?|ttf|svg|png|webp|jpg|jpeg|gif)$")
_PUBLIC_SECONDS = {
    "/api/news/market": 1,
    "/api/hot-coins": 2,
    "/api/candles": 1,
    "/api/candles/live": 1,
    "/api/fear-greed": 30,
    "/api/hangang-temp": 30,
    "/api/kimchi-premium": 1,
    "/api/usdkrw": 30,
    "/api/funding-rate": 5,
    "/api/backtest/limits": 300,
    "/api/auth/google/config": 300,
    "/api/runner/download/info": 30,
}


class CachePolicyMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        method = scope.get("method", "GET")

        async def policy_send(message):
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = list(message.get("headers", []))
                previous = next((value.decode("latin-1") for key, value in headers
                                 if key.lower() == b"cache-control"), None)
                cache_policy = previous
                private_policy = previous if previous in {"no-store", PRIVATE} else PRIVATE
                if path == "/api" or path.startswith("/api/"):
                    if method not in {"GET", "HEAD"} or status >= 400:
                        cache_policy = private_policy
                    elif path.startswith("/api/me/"):
                        cache_policy = private_policy
                    elif path in _PUBLIC_SECONDS:
                        seconds = _PUBLIC_SECONDS[path]
                        cache_policy = previous or f"public, max-age={seconds}, s-maxage={seconds}"
                    elif path.startswith("/api/news/coin/"):
                        cache_policy = previous or "public, max-age=1, s-maxage=1"
                    else:
                        # Other explicitly public image endpoints keep their own
                        # policy; mixed leaderboard/chat/board responses default private.
                        cache_policy = previous or PRIVATE
                elif status >= 400:
                    cache_policy = "no-store"
                elif _HASHED_ASSET.fullmatch(path):
                    cache_policy = "public, max-age=31536000, immutable"
                elif path == "/sw.js":
                    cache_policy = "no-cache"
                elif previous is None:
                    cache_policy = "public, max-age=0, must-revalidate"
                headers = [(key, value) for key, value in headers if key.lower() != b"cache-control"]
                headers.append((b"cache-control", cache_policy.encode("ascii")))
                if "no-store" in cache_policy:
                    # No CDN override may re-enable storage of account/error data.
                    headers = [(key, value) for key, value in headers
                               if key.lower() not in {b"cdn-cache-control", b"vercel-cdn-cache-control", b"etag"}]
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, policy_send)


def public_news_response(request: Request, payload: dict) -> Response:
    """Hash the prepared projection; browser revalidation uses its cached body."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
                      allow_nan=False).encode("utf-8")
    etag = '"' + hashlib.sha256(body).hexdigest() + '"'
    headers = {"Cache-Control": "public, max-age=1, s-maxage=1", "ETag": etag}
    # GET validators permit weak comparison and comma-separated alternatives.
    validators = [value.strip().removeprefix("W/") for value in
                  request.headers.get("if-none-match", "").split(",")]
    if etag in validators or "*" in validators:
        return Response(status_code=304, headers=headers)
    return Response(body, media_type="application/json", headers=headers)
