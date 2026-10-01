"""Bounded, retry-aware OpenAI Responses runtime shared by every AI call path.

Every AI feature in the app speaks one tiny contract — a system instruction plus
user turns in, text blocks out — through ``get_ai_client().messages.create``.
That surface is deliberately provider-agnostic. Call sites and their test fakes
never import an SDK or see an SDK exception. Provider selection happens here.
"""
from __future__ import annotations

import atexit
import copy
import hashlib
import json
import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Callable, TypeVar

import httpx
import openai

from .api_usage import record_openai_usage
from .observability import timed_operation

T = TypeVar("T")

# All features share this target; no old-provider fallback.
DEFAULT_MODEL = "gpt-6-luna"
REASONING_EFFORT = "max"


def ai_api_key() -> str:
    """The server-side OpenAI key; empty means every AI feature is off."""
    return str(os.environ.get("OPENAI_API_KEY") or "").strip()


def ai_available() -> bool:
    return bool(ai_api_key())


def default_model() -> str:
    return str(os.environ.get("OPENAI_MODEL") or "").strip() or DEFAULT_MODEL


class AiBusyError(RuntimeError):
    """Raised when all configured AI call slots are occupied."""


# --- provider errors, translated once here so call sites stay SDK-free -------
class AiProviderError(RuntimeError):
    """Base for failures coming back from the model provider."""

    def __init__(self, message: str, *, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code


class AiAuthError(AiProviderError):
    """Key missing, invalid, or lacking permission (401/403)."""


class AiRateLimitError(AiProviderError):
    """Provider asked us to slow down (429) — transient."""


class AiConnectionError(AiProviderError):
    """Network failure or timeout before a response arrived — transient."""


class AiStatusError(AiProviderError):
    """Any other non-2xx answer; ``status_code`` tells the caller which."""


def _translate_error(error: BaseException) -> AiProviderError:
    if isinstance(error, openai.APIConnectionError):
        return AiConnectionError("OpenAI connection failed")
    if isinstance(error, openai.APIStatusError):
        code = int(error.status_code)
        # Do not propagate provider error bodies (may echo credentials/prompts).
        message = f"OpenAI HTTP {code}"
        if code in (401, 403):
            return AiAuthError(message, status_code=code)
        if code == 429:
            return AiRateLimitError(message, status_code=code)
        return AiStatusError(message, status_code=code)
    if isinstance(error, (httpx.TimeoutException, httpx.TransportError)):
        return AiConnectionError(str(error))
    return AiStatusError(str(error))


# --- the one contract every call site uses --------------------------------
@dataclass(frozen=True)
class TextBlock:
    text: str
    type: str = "text"


@dataclass(frozen=True)
class AiResponse:
    content: list[TextBlock] = field(default_factory=list)


def _to_contents(messages: list[dict]) -> list[dict]:
    """Shared text-only contract → Responses input turns, preserving roles."""
    contents = []
    for message in messages:
        role = "assistant" if message.get("role") == "assistant" else "user"
        content = message.get("content")
        if isinstance(content, str):
            text = content
        else:
            # Only text parts are used anywhere in the app; anything else is
            # stringified so a stray dict can't silently vanish from the prompt.
            text = "\n".join(
                p if isinstance(p, str) else str(p.get("text", json.dumps(p, ensure_ascii=False)))
                for p in (content or [])
            )
        contents.append({"role": role, "content": text})
    return contents


class _Messages:
    def __init__(self, client: openai.OpenAI) -> None:
        self._client = client

    def create(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str = "",
        messages: list[dict],
        timeout: float | None = None,
        purpose: str = "",
        reasoning_effort: str | None = None,
        json_schema: dict | None = None,
    ) -> AiResponse:
        """One request → text blocks. ``timeout`` is seconds, like the callers pass.

        ``purpose`` is the call site's code (position_news, ai_explain, …) for the
        usage ledger only — it never reaches the model.
        """
        # Responses counts reasoning + visible output together. Keep both bounded.
        reserve = max(0, min(16384, int(os.environ.get("OPENAI_REASONING_TOKEN_RESERVE", "4096"))))
        output_limit = max(16, min(32768, int(max_tokens) + reserve))
        kwargs = {"timeout": float(timeout)} if timeout is not None else {}
        if json_schema is not None:
            # Structured Outputs: 키 이름·JSON 형식을 OpenAI 쪽에서 강제한다. 낮은 추론에서
            # 모델이 title_ko 를 title 로 바꿔 써서 배치를 통째로 버리던 것을 막는다.
            kwargs["text"] = {"format": {"type": "json_schema", "name": json_schema["name"],
                                         "strict": True, "schema": json_schema["schema"]}}
        try:
            response = self._client.responses.create(
                model=model, input=_to_contents(messages), instructions=system,
                reasoning={"effort": reasoning_effort or REASONING_EFFORT},
                max_output_tokens=output_limit, store=False, **kwargs,
            )
        except (openai.APIError, httpx.HTTPError) as error:
            # 실패도 요청 한 건이다 — 여기서 세야 재시도·캐시 히트와 무관하게 과금 단위와 맞는다.
            record_openai_usage(model=model, purpose=purpose, usage=None, ok=False)
            raise _translate_error(error) from error
        except Exception:
            record_openai_usage(model=model, purpose=purpose, usage=None, ok=False)
            raise
        # usage 는 AiResponse 에 싣지 않는다 — 응답은 캐시에 deep-copy 되어 되돌아오므로
        # 거기 실으면 캐시 히트마다 다시 세게 된다.
        complete = response.status == "completed"
        record_openai_usage(
            model=getattr(response, "model", model), purpose=purpose,
            usage=getattr(response, "usage", None), ok=complete,
        )
        if not complete:
            # Count billed incomplete responses, but never cache/display truncated JSON.
            raise AiStatusError("OpenAI response incomplete; no automatic retry")
        return AiResponse(content=[TextBlock(text=response.output_text or "")])


class AiClient:
    """Thin wrapper so ``get_ai_client().messages.create(...)`` reads the same
    everywhere; holds one HTTP client for the process."""

    def __init__(self, *, api_key: str, timeout_seconds: float) -> None:
        self._client = openai.OpenAI(
            api_key=api_key,
            base_url="https://api.openai.com/v1",
            timeout=timeout_seconds,
            max_retries=0,
        )
        self.messages = _Messages(self._client)

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass


def ai_cache_key(namespace: str, prompt_version: str, model: str, payload) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    material = f"{namespace}\n{prompt_version}\n{model}\n{canonical}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _provider_transient(error: BaseException) -> bool:
    if isinstance(error, (AiConnectionError, AiRateLimitError)):
        return True
    return int(getattr(error, "status_code", 0) or 0) >= 500


class AiCallRuntime:
    """TTL/hash cache, same-key singleflight, retries, and bounded concurrency."""

    def __init__(
        self,
        *,
        max_concurrent: int | None = None,
        acquire_timeout_seconds: float | None = None,
        cache_ttl_seconds: float | None = None,
        cache_max_entries: int | None = None,
        retries: int | None = None,
        retry_backoff_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        is_transient: Callable[[BaseException], bool] = _provider_transient,
    ) -> None:
        self._clock = clock
        self._sleep = sleeper
        self._is_transient = is_transient
        self._cache_ttl = max(
            0.0,
            float(cache_ttl_seconds if cache_ttl_seconds is not None else os.environ.get("AI_CACHE_TTL_SECONDS", "900")),
        )
        self._cache_max = max(
            1,
            int(cache_max_entries if cache_max_entries is not None else os.environ.get("AI_CACHE_MAX_ENTRIES", "512")),
        )
        # One initial attempt + at most one retry, regardless of stale env values.
        self._retries = min(1, max(0, int(retries if retries is not None else os.environ.get("AI_RETRIES", "1"))))
        self._backoff = max(
            0.0,
            float(
                retry_backoff_seconds
                if retry_backoff_seconds is not None
                else os.environ.get("AI_RETRY_BACKOFF_SECONDS", "0.25")
            ),
        )
        self._acquire_timeout = max(
            0.0,
            float(
                acquire_timeout_seconds
                if acquire_timeout_seconds is not None
                else os.environ.get("AI_ACQUIRE_TIMEOUT_SECONDS", "1")
            ),
        )
        capacity = max(
            1,
            int(max_concurrent if max_concurrent is not None else os.environ.get("AI_MAX_CONCURRENT", "3")),
        )
        self._capacity = threading.BoundedSemaphore(capacity)
        self._lock = threading.Lock()
        self._cache: OrderedDict[str, tuple[float, T]] = OrderedDict()
        self._flights: dict[str, Future] = {}

    def _cache_get_locked(self, key: str):
        hit = self._cache.get(key)
        if hit is None:
            return None
        expires_at, value = hit
        if expires_at <= self._clock():
            self._cache.pop(key, None)
            return None
        self._cache.move_to_end(key)
        return copy.deepcopy(value)

    def _finish_failure(self, key: str, future: Future, error: BaseException) -> None:
        future.set_exception(error)
        # Observe it for the leader-only case; followers still receive it from result().
        future.exception()
        with self._lock:
            if self._flights.get(key) is future:
                self._flights.pop(key, None)

    def call(
        self,
        key: str,
        loader: Callable[[], T],
        *,
        retries: int | None = None,
    ) -> tuple[T, str]:
        retry_limit = self._retries if retries is None else min(1, max(0, int(retries)))
        with self._lock:
            cached = self._cache_get_locked(key)
            if cached is not None:
                return cached, "cached"
            future = self._flights.get(key)
            if future is None:
                future = Future()
                self._flights[key] = future
                leader = True
            else:
                leader = False

        if not leader:
            return copy.deepcopy(future.result()), "shared"

        acquired = self._capacity.acquire(timeout=self._acquire_timeout)
        if not acquired:
            error = AiBusyError("AI 요청이 몰려 있어요. 잠시 후 다시 시도해 주세요.")
            self._finish_failure(key, future, error)
            raise error

        try:
            with timed_operation("ai"):
                attempt = 0
                while True:
                    try:
                        value = loader()
                        break
                    except BaseException as error:
                        if attempt >= retry_limit or not self._is_transient(error):
                            self._finish_failure(key, future, error)
                            raise
                        self._sleep(self._backoff * (2 ** attempt))
                        attempt += 1

            stored = copy.deepcopy(value)
            with self._lock:
                if self._cache_ttl > 0:
                    self._cache[key] = (self._clock() + self._cache_ttl, stored)
                    self._cache.move_to_end(key)
                    while len(self._cache) > self._cache_max:
                        self._cache.popitem(last=False)
                if self._flights.get(key) is future:
                    self._flights.pop(key, None)
            future.set_result(copy.deepcopy(stored))
            return copy.deepcopy(stored), "loaded"
        finally:
            self._capacity.release()

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


_client_lock = threading.Lock()
_client = None
_client_factory = None
_runtime: AiCallRuntime | None = None


def _client_timeout_seconds() -> float:
    return max(
        1.0,
        float(
            os.environ.get(
                "AI_TIMEOUT_SECONDS",
                os.environ.get("OPENAI_POSITION_NEWS_TIMEOUT_SECONDS", "60"),
            )
        ),
    )


def get_ai_client() -> AiClient:
    global _client, _client_factory
    factory = AiClient
    with _client_lock:
        if _client is not None and _client_factory is factory:
            return _client
        if _client is not None:
            try:
                _client.close()
            except Exception:
                pass
        key = ai_api_key()
        if not key:
            raise AiAuthError("서버에 OPENAI_API_KEY 가 설정되지 않았어요.", status_code=401)
        _client = factory(api_key=key, timeout_seconds=_client_timeout_seconds())
        _client_factory = factory
        return _client


def get_ai_runtime() -> AiCallRuntime:
    global _runtime
    with _client_lock:
        if _runtime is None:
            _runtime = AiCallRuntime()
        return _runtime


def close_ai_runtime() -> None:
    global _client, _client_factory, _runtime
    with _client_lock:
        client = _client
        _client = None
        _client_factory = None
        runtime = _runtime
        _runtime = None
    if client is not None:
        try:
            client.close()
        except Exception:
            pass
    if runtime is not None:
        runtime.clear()


atexit.register(close_ai_runtime)
