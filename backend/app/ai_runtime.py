"""Bounded, retry-aware runtime shared by every Gemini call path.

Every AI feature in the app speaks one tiny contract — a system instruction plus
user turns in, text blocks out — through ``get_ai_client().messages.create``.
That surface is deliberately provider-agnostic (it predates Gemini: the app ran
on Anthropic first), which is why swapping the model provider touches this
module only. Call sites and their test fakes never import an SDK, never see an
SDK exception, and never read an API key.
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
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from .observability import timed_operation

T = TypeVar("T")

# 기본 모델. 3.5 Flash-Lite 는 thinking_level 기본이 'minimal' 이라 분류·번역·JSON 추출
# 같은 이 앱의 짧은 작업에 맞고, 출력 토큰을 생각에 쓰지 않아 max_tokens 예산이 그대로
# 본문에 쓰인다. 바꾸려면 GEMINI_MODEL 하나만 바꾸면 된다 — 모든 호출 지점이 이걸 읽는다.
DEFAULT_MODEL = "gemini-3.5-flash-lite"


def ai_api_key() -> str:
    """The server-side Gemini key; empty means every AI feature is off."""
    return str(os.environ.get("GEMINI_API_KEY") or "").strip()


def ai_available() -> bool:
    return bool(ai_api_key())


def default_model() -> str:
    return str(os.environ.get("GEMINI_MODEL") or "").strip() or DEFAULT_MODEL


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
    if isinstance(error, genai_errors.APIError):
        code = int(getattr(error, "code", 0) or 0)
        message = str(getattr(error, "message", "") or error)
        # Google answers an invalid key with 400 INVALID_ARGUMENT, not 401 — read
        # the message so the user sees "키가 유효하지 않아요" rather than a generic failure.
        if code in (401, 403) or "api key" in message.lower():
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


def _to_contents(messages: list[dict]) -> list[genai_types.Content]:
    """Anthropic-style ``[{role, content}]`` → Gemini ``Content`` turns."""
    contents = []
    for message in messages:
        role = "model" if message.get("role") == "assistant" else "user"
        content = message.get("content")
        if isinstance(content, str):
            parts = [genai_types.Part(text=content)]
        else:
            # Only text parts are used anywhere in the app; anything else is
            # stringified so a stray dict can't silently vanish from the prompt.
            parts = [
                genai_types.Part(text=p if isinstance(p, str) else json.dumps(p, ensure_ascii=False))
                for p in (content or [])
            ]
        contents.append(genai_types.Content(role=role, parts=parts))
    return contents


class _Messages:
    def __init__(self, client: genai.Client) -> None:
        self._client = client

    def create(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str = "",
        messages: list[dict],
        timeout: float | None = None,
    ) -> AiResponse:
        """One request → text blocks. ``timeout`` is seconds, like the callers pass."""
        config = genai_types.GenerateContentConfig(
            system_instruction=system or None,
            max_output_tokens=int(max_tokens),
            http_options=(
                genai_types.HttpOptions(timeout=int(float(timeout) * 1000)) if timeout else None
            ),
            # No tools anywhere in the app; opting out skips the SDK's function-
            # calling loop and the warning it logs about it on every process.
            automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True),
        )
        try:
            response = self._client.models.generate_content(
                model=model, contents=_to_contents(messages), config=config,
            )
        except (genai_errors.APIError, httpx.HTTPError) as error:
            raise _translate_error(error) from error
        # ``.text`` is None when the answer was blocked or empty; callers already
        # treat an empty block as "no answer", so hand them exactly that.
        return AiResponse(content=[TextBlock(text=response.text or "")])


class AiClient:
    """Thin wrapper so ``get_ai_client().messages.create(...)`` reads the same
    everywhere; holds one HTTP client for the process."""

    def __init__(self, *, api_key: str, timeout_seconds: float) -> None:
        self._client = genai.Client(
            api_key=api_key,
            http_options=genai_types.HttpOptions(
                timeout=int(timeout_seconds * 1000),
                # The runtime below owns retries (bounded, cached, single-flight);
                # SDK-level retries on top would multiply paid calls.
                retry_options=genai_types.HttpRetryOptions(attempts=1),
            ),
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
        self._retries = max(0, int(retries if retries is not None else os.environ.get("AI_RETRIES", "1")))
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
        retry_limit = self._retries if retries is None else max(0, int(retries))
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
                os.environ.get("GEMINI_POSITION_NEWS_TIMEOUT_SECONDS", "15"),
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
            raise AiAuthError("서버에 GEMINI_API_KEY 가 설정되지 않았어요.", status_code=401)
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
