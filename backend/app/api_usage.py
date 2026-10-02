"""유료 API 사용량 기록과 비용 보고 — 관리자 대시보드 'API 비용' 탭의 데이터.

OpenAI Responses usage를 호출마다 ``ApiUsageDaily``에 누적한다. 과거 Gemini 행은 분리 보존한다.
기록 지점은 ``ai_runtime._Messages.create`` 한 곳뿐이라 캐시 히트·singleflight 공유는 빠지고
재시도는 각각 세어진다 — 즉 실제로 과금된 요청 수와 같다. 비용은 청구서가 아니라 토큰 × 단가표
추정이다. API 오류/타임아웃으로 usage가 없으면 실제 청구액을 알 수 없으며 0원 확정이 아니다.

기록은 best-effort: 표가 없거나 DB 가 죽어도 AI 호출 자체를 막지 않는다(예외는 삼키고 1분에 한 번만
경고). CoinDesk 는 여기서 기록하지 않는다 — ``TickerNewsAiBudget`` 의 ``coindesk_news:<날짜>`` 행이
이미 호출 수를 세고 있어 보고 시점에 그 행을 읽는다.
"""
from __future__ import annotations

import calendar
import json
import logging
import os
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import select

from .db import ApiUsageDaily, TickerNewsAiBudget, get_session

logger = logging.getLogger(__name__)

_KST = timezone(timedelta(hours=9))
PROVIDER_GEMINI = "gemini"
PROVIDER_OPENAI = "openai"
WARN_INTERVAL_SECONDS = 60.0
DAILY_DAYS = 30
MONTHS_DEFAULT = 6

# 1M 토큰당 USD. Gemini 3.5 Flash-Lite 유료 등급 공시가이며 env 로 바꾼다
# (모델별 재정의는 GEMINI_PRICES_JSON). 출처: ai.google.dev/gemini-api/docs/pricing.
# 2026-09-28 정정 — 이전 값($0.10/$0.40/$0.025)은 출시 전 단가라 화면이 실제 청구액의
# 1/4~1/5 만 보여 줬다. 단가를 바꾸면 지난달 수치도 같이 다시 계산된다(토큰만 저장한다).
_DEFAULT_PRICES = {"input": 0.30, "output": 2.50, "cached": 0.03}
_PRICE_ENV = {
    "input": "GEMINI_PRICE_INPUT_USD_PER_M",
    "output": "GEMINI_PRICE_OUTPUT_USD_PER_M",
    "cached": "GEMINI_PRICE_CACHED_USD_PER_M",
}

# 용도 코드 → 화면 라벨 · 일일 한도 env(코드 기본값). 한도가 없는 용도는 None("없음").
# 순서가 화면 표 순서다.
PURPOSES: tuple[tuple[str, str, Optional[tuple[str, int]]], ...] = (
    ("position_news", "종목 뉴스 분류 · 요약", ("POSITION_NEWS_MAX_AI_ANALYSES_PER_DAY", 10)),
    ("title_translation", "뉴스 제목 한글 번역", None),
    ("community_summaries", "커뮤니티 글 요약", None),
    ("ai_explain", "백테스트 AI 해설", ("AI_EXPLAIN_MAX_CALLS_PER_DAY", 20)),
    ("market_news_summary", "시장 브리핑 요약", ("NEWS_MARKET_SUMMARY_MAX_CALLS_PER_DAY", 2)),
    ("ai_challenge", "일일 챌린지 생성", None),
    ("devnote", "개발자 노트 생성", None),
)
NO_LIMIT_LABEL = "없음"

# 구독형 고정액. 청구 API 가 없는 제공자는 요금제 금액을 설정값으로 둔다(ADMIN_FIXED_COSTS_JSON 이 통째로 대체).
# 항목마다 선택 필드 ``since: "YYYY-MM"`` — 구독 시작 월. 그 전 달은 0 이다(없던 비용을 만들지 않는다).
# 없으면 보고 시점의 '지난달'부터로 본다. 이번 달은 경과일로 안분한다(costs_report).
DEFAULT_FIXED_COSTS = {
    # 2026-09-28 정정 — 실제 청구는 Pro 다(청구 화면: 이달 예상 $39.71 = 작업공간
    # 구독 $22.52 + 서비스 $12.70 + 대역폭 $0.60). Starter ×2 $14 로 남아 있어
    # 화면이 Render 를 매달 약 $25 적게 잡았다. 서비스·대역폭은 달마다 변해 어림값이다.
    "render": {"label": "Render (web + worker)", "usd": 40, "plan": "Pro (구독 + 사용량)"},
    "supabase": {"label": "Supabase", "usd": 25, "plan": "Pro"},
    "vercel": {"label": "Vercel", "usd": 0, "plan": "Hobby"},
    "prefect": {"label": "Prefect Cloud", "usd": 0, "plan": "Free"},
}

_COINDESK_PREFIX = "coindesk_news:"
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

_warn_lock = threading.Lock()
_last_warn_monotonic: Optional[float] = None


# --- 시각 --------------------------------------------------------------------
def _now_ms(now_ms: Optional[int]) -> int:
    return int(now_ms if now_ms is not None else time.time() * 1000)


def day_kst(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).astimezone(_KST).strftime("%Y-%m-%d")


def _kst_date(ms: int) -> date:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).astimezone(_KST).date()


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- 설정값 ------------------------------------------------------------------
def _env_float(name: str, default: float) -> float:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


def _env_json(name: str):
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError as error:
        _warn_throttled(f"{name} 파싱 실패, 기본값 사용", error)
        return None


def gemini_prices(model: str) -> dict[str, float]:
    """1M 토큰당 USD (input/output/cached). 모델별 재정의는 있는 키만 덮는다."""
    prices = {key: _env_float(_PRICE_ENV[key], default) for key, default in _DEFAULT_PRICES.items()}
    overrides = _env_json("GEMINI_PRICES_JSON")
    entry = overrides.get(str(model or "")) if isinstance(overrides, dict) else None
    if isinstance(entry, dict):
        for key in prices:
            try:
                if entry.get(key) is not None:
                    prices[key] = max(0.0, float(entry[key]))
            except (TypeError, ValueError):
                continue
    return prices


def fixed_costs() -> dict[str, dict]:
    raw = _env_json("ADMIN_FIXED_COSTS_JSON")
    source = raw if isinstance(raw, dict) and raw else DEFAULT_FIXED_COSTS
    result: dict[str, dict] = {}
    for key, entry in source.items():
        if not isinstance(entry, dict):
            continue
        try:
            usd = max(0.0, float(entry.get("usd") or 0))
        except (TypeError, ValueError):
            usd = 0.0
        since = str(entry.get("since") or "").strip()
        result[str(key)] = {
            "label": str(entry.get("label") or key),
            "usd": usd,
            "plan": str(entry.get("plan") or ""),
            "method": str(entry.get("method") or "fixed"),
            "since": since if _MONTH_RE.match(since) else "",
        }
    return result


def fixed_month_usd(usd: float, month: str, *, today: date, since: str) -> float:
    """구독 고정액의 한 달 몫(USD, 2자리).

    시작 월(``since``, "YYYY-MM") 이전은 0 — 4~8월에 $39 씩 넣어 없던 비용 $234 를 만든 적이 있다(2026-09-18 점검).
    이번 달은 ``usd × 경과일 / 그 달 일수`` 로 안분한다(월 중순에 한 달치를 전부 보이지 않게). 지난 달들은 전액.
    """
    if since and month < since:
        return 0.0
    current = _month_key(today)
    if month == current:
        days_in_month = calendar.monthrange(today.year, today.month)[1]
        return round(float(usd) * today.day / days_in_month, 2)
    return round(float(usd), 2)


def daily_limit_for(purpose: str):
    """화면의 '일일 한도' — 실제 코드가 읽는 env 와 그 기본값을 그대로 보여준다."""
    if purpose == 'market_news_summary':
        from .news_ai_budget import MARKET_SUMMARY_MAX_CALLS_PER_DAY
        return MARKET_SUMMARY_MAX_CALLS_PER_DAY
    for code, _label, limit in PURPOSES:
        if code != purpose:
            continue
        if limit is None:
            return NO_LIMIT_LABEL
        env_name, default = limit
        raw = str(os.environ.get(env_name) or "").strip()
        try:
            return max(0, int(raw)) if raw else default
        except ValueError:
            return default
    return NO_LIMIT_LABEL


# --- 토큰 · 비용 ---------------------------------------------------------------
def _usage_field(usage, name: str) -> int:
    if usage is None:
        return 0
    value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def usage_tokens(usage) -> dict[str, int]:
    """google-genai ``usage_metadata`` (또는 같은 키의 dict) → 입력/출력/캐시 토큰.

    ``prompt_token_count`` 는 캐시된 부분을 포함한 전체 입력이고 ``cached_content_token_count`` 는
    그중 캐시 히트 몫이다. 생각(thoughts) 토큰은 출력으로 과금되므로 출력에 더한다.
    """
    prompt = _usage_field(usage, "prompt_token_count")
    cached = min(prompt, _usage_field(usage, "cached_content_token_count"))
    output = _usage_field(usage, "candidates_token_count") + _usage_field(usage, "thoughts_token_count")
    return {"input_tokens": prompt, "output_tokens": output, "cached_tokens": cached}


def cost_micro_usd(model: str, *, input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> int:
    """토큰 × 단가 → micro USD. 1M 토큰에 $P 면 토큰 하나가 P micro USD 라 곱셈이 곧 답이다."""
    prices = gemini_prices(model)
    cached = min(max(0, int(cached_tokens)), max(0, int(input_tokens)))
    billable_input = max(0, int(input_tokens)) - cached
    total = (
        billable_input * prices["input"]
        + cached * prices["cached"]
        + max(0, int(output_tokens)) * prices["output"]
    )
    return int(round(total))


# --- 기록 ------------------------------------------------------------------
def _warn_throttled(message: str, error: BaseException) -> None:
    global _last_warn_monotonic
    now = time.monotonic()
    with _warn_lock:
        if _last_warn_monotonic is not None and now - _last_warn_monotonic < WARN_INTERVAL_SECONDS:
            return
        _last_warn_monotonic = now
    logger.warning("[api_usage] %s: %s: %s", message, type(error).__name__, error)


_ACCUMULATED = ("calls", "failures", "input_tokens", "output_tokens", "cached_tokens", "cost_micro_usd")


def _upsert(db, values: dict) -> None:
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    statement = insert(ApiUsageDaily).values(**values)
    excluded = statement.excluded
    set_ = {name: getattr(ApiUsageDaily, name) + getattr(excluded, name) for name in _ACCUMULATED}
    set_["updated_ms"] = excluded.updated_ms
    # rowcount 는 보지 않는다 — 운영 Postgres 에서 INSERT … ON CONFLICT 의 rowcount 는 -1 이다.
    db.exec(statement.on_conflict_do_update(
        index_elements=["day_kst", "provider", "model", "purpose"], set_=set_,
    ))


def record_gemini_usage(
    *,
    model: str,
    purpose: str,
    usage=None,
    ok: bool = True,
    now_ms: Optional[int] = None,
    db=None,
    _provider: str = PROVIDER_GEMINI,
) -> bool:
    """Gemini 호출 한 건을 오늘(KST) 행에 더한다. 실패도 calls·failures 에 센다. 절대 raise 하지 않는다."""
    try:
        millis = _now_ms(now_ms)
        tokens = openai_usage_tokens(usage) if _provider == PROVIDER_OPENAI else usage_tokens(usage)
        values = {
            "day_kst": day_kst(millis),
            "provider": _provider,
            "model": str(model or "")[:80],
            "purpose": str(purpose or "")[:40],
            "calls": 1,
            "failures": 0 if ok else 1,
            **tokens,
            "cost_micro_usd": (openai_cost_micro_usd(model, **tokens, cache_write_tokens=_openai_cache_writes(usage)) if _provider == PROVIDER_OPENAI
                               else cost_micro_usd(model, **tokens)),
            "updated_ms": millis,
        }
        if db is None:
            with get_session() as owned:
                _upsert(owned, values)
                owned.commit()
        else:
            try:
                _upsert(db, values)
                db.commit()
            except Exception:
                # 빌린 세션을 실패한 트랜잭션 채로 돌려주면 호출자의 다음 쿼리까지 깨진다.
                db.rollback()
                raise
        return True
    except Exception as error:
        _warn_throttled("AI 사용량 기록 실패", error)
        return False


def openai_usage_tokens(usage) -> dict[str, int]:
    prompt = _usage_field(usage, "input_tokens")
    details = usage.get("input_tokens_details") if isinstance(usage, dict) else getattr(usage, "input_tokens_details", None)
    # Responses output_tokens ALREADY includes reasoning tokens; never add twice.
    return {"input_tokens": prompt, "output_tokens": _usage_field(usage, "output_tokens"),
            "cached_tokens": min(prompt, _usage_field(details, "cached_tokens"))}


def _openai_cache_writes(usage) -> int:
    details = usage.get("input_tokens_details") if isinstance(usage, dict) else getattr(usage, "input_tokens_details", None)
    return _usage_field(details, "cache_write_tokens")


def openai_cost_micro_usd(model: str, *, input_tokens: int, output_tokens: int, cached_tokens: int = 0,
                          cache_write_tokens: int = 0) -> int:
    # GPT-6 Luna standard short-context USD / 1M (official model page, 2026-09-30).
    # Long-context premium applies to the ENTIRE request, not just the excess.
    long = input_tokens > 272_000
    cached = min(max(0, cached_tokens), max(0, input_tokens))
    writes = min(max(0, cache_write_tokens), max(0, input_tokens - cached))
    prices = {"input": 0.10, "output": 0.50, "cached": 0.01}
    overrides = _env_json("OPENAI_PRICES_JSON")
    entry = overrides.get(model) if isinstance(overrides, dict) else None
    if isinstance(entry, dict):
        prices.update({k: max(0.0, float(entry[k])) for k in prices if k in entry})
    return int(round((input_tokens - cached + writes * 0.25) * prices["input"] * (2 if long else 1)
                     + cached * prices["cached"] * (2 if long else 1)
                     + output_tokens * prices["output"] * (1.5 if long else 1)))


def record_openai_usage(**kwargs) -> bool:
    return record_gemini_usage(**kwargs, _provider=PROVIDER_OPENAI)


# --- 보고 ------------------------------------------------------------------
def _usd(micro: float) -> float:
    return round(float(micro) / 1_000_000, 2)


def _month_key(day: date) -> str:
    return day.strftime("%Y-%m")


def _months_back(today: date, months: int) -> list[date]:
    """오래된 달 → 이번 달, 각 달의 1일."""
    year, month = today.year, today.month
    result = []
    for _ in range(months):
        result.append(date(year, month, 1))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(result))


def _coindesk_calls_by_day(db, since_day: str) -> dict[str, int]:
    rows = db.exec(
        select(TickerNewsAiBudget.budget_date_kst, TickerNewsAiBudget.used)
        .where(TickerNewsAiBudget.budget_date_kst.like(f"{_COINDESK_PREFIX}%"))
    ).all()
    result: dict[str, int] = {}
    for key, used in rows:
        day = str(key)[len(_COINDESK_PREFIX):]
        if not _DAY_RE.match(day) or day < since_day:
            continue
        result[day] = result.get(day, 0) + max(0, int(used or 0))
    return result


def costs_report(db, *, months: int = MONTHS_DEFAULT, now_ms: Optional[int] = None) -> dict:
    """``GET /api/admin/costs`` 응답 전체. Gemini 는 ApiUsageDaily, CoinDesk 는 예산 행, 구독형은 설정값."""
    months = max(1, min(24, int(months)))
    millis = _now_ms(now_ms)
    today = _kst_date(millis)
    today_key = today.strftime("%Y-%m-%d")
    month_starts = _months_back(today, months)
    current_month = _month_key(today)
    last_month = _month_key(month_starts[-2]) if len(month_starts) >= 2 else _month_key(_months_back(today, 2)[0])
    daily_days = [(today - timedelta(days=offset)).strftime("%Y-%m-%d") for offset in range(DAILY_DAYS - 1, -1, -1)]
    since_day = min(month_starts[0].strftime("%Y-%m-%d"), daily_days[0])

    rows = db.exec(select(ApiUsageDaily).where(
        ApiUsageDaily.provider.in_([PROVIDER_GEMINI, PROVIDER_OPENAI]), ApiUsageDaily.day_kst >= since_day,
    )).all()

    def bucket() -> dict:
        return {"calls": 0, "failures": 0, "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "cost_micro_usd": 0}

    def add(target: dict, row: ApiUsageDaily) -> None:
        for name in _ACCUMULATED:
            if name == "cost_micro_usd":
                continue
            target[name] += int(getattr(row, name) or 0)
        # 비용은 (모델, 토큰) 에서 나오는 파생값이다. 저장된 금액은 기록 시점 단가로
        # 굳어 있어, 단가 설정이 틀렸으면 지난 기록까지 틀린 채 남는다. 여기서 다시
        # 계산해 단가를 고치면 과거 수치도 함께 맞게 한다. 저장 컬럼은 그대로 둔다.
        # OpenAI is priced per request (context tiers/cache writes); daily totals
        # cannot reconstruct those tiers. Preserve its recorded request costs.
        if row.provider == PROVIDER_OPENAI:
            target["cost_micro_usd"] += int(row.cost_micro_usd or 0)
            return
        target["cost_micro_usd"] += cost_micro_usd(
            row.model or "",
            input_tokens=int(row.input_tokens or 0),
            output_tokens=int(row.output_tokens or 0),
            cached_tokens=int(row.cached_tokens or 0),
        )

    by_month: dict[str, dict] = {}
    by_day: dict[str, dict] = {}
    by_purpose: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    provider_months: dict[tuple[str, str], dict] = {}
    provider_days: dict[tuple[str, str], dict] = {}
    for row in rows:
        add(provider_months.setdefault((row.provider, row.day_kst[:7]), bucket()), row)
        add(provider_days.setdefault((row.provider, row.day_kst), bucket()), row)
        add(by_month.setdefault(row.day_kst[:7], bucket()), row)
        add(by_day.setdefault(row.day_kst, bucket()), row)
        if row.day_kst[:7] == current_month:
            add(by_purpose.setdefault(row.purpose or "", bucket()), row)
            add(by_model.setdefault(row.model or "", bucket()), row)

    coindesk_by_day = _coindesk_calls_by_day(db, since_day)
    coindesk_price = _env_float("COINDESK_COST_PER_CALL_USD", 0.0)
    coindesk_by_month: dict[str, int] = {}
    for day, used in coindesk_by_day.items():
        coindesk_by_month[day[:7]] = coindesk_by_month.get(day[:7], 0) + used

    fixed = fixed_costs()
    # 시작 월이 없는 구독은 지난달부터로 본다 — 기록이 없는 과거 달까지 소급해 채우지 않는다.
    fixed_since = {name: entry["since"] or last_month for name, entry in fixed.items()}

    monthly = []
    for start in month_starts:
        key = _month_key(start)
        providers = {
            PROVIDER_GEMINI: _usd(provider_months.get((PROVIDER_GEMINI, key), bucket())["cost_micro_usd"]),
            PROVIDER_OPENAI: _usd(provider_months.get((PROVIDER_OPENAI, key), bucket())["cost_micro_usd"]),
            "coindesk": round(coindesk_by_month.get(key, 0) * coindesk_price, 2),
        }
        for name, entry in fixed.items():
            providers[name] = fixed_month_usd(entry["usd"], key, today=today, since=fixed_since[name])
        monthly.append({
            "month": key,
            "label": f"{start.month}월" + ("*" if key == current_month else ""),
            "providers": providers,
            "total_usd": round(sum(providers.values()), 2),
        })

    ai_month = by_month.get(current_month, bucket())
    gemini_month = provider_months.get((PROVIDER_GEMINI, current_month), bucket())
    gemini_last = provider_months.get((PROVIDER_GEMINI, last_month), bucket())
    by_model = {row.model: {} for row in rows if row.provider == PROVIDER_GEMINI and row.day_kst[:7] == current_month}
    if by_model:
        top_models = sorted(by_model)
        model_label = top_models[0] or "?"
        if len(top_models) > 1:
            model_label += f" 외 {len(top_models) - 1}"
    else:
        model_label = "이전 사용 기록"
    providers = [{
        "provider": PROVIDER_GEMINI,
        "label": f"Gemini · {model_label}",
        "method": "estimate",
        "calls": gemini_month["calls"],
        "failures": gemini_month["failures"],
        "input_tokens": gemini_month["input_tokens"],
        "output_tokens": gemini_month["output_tokens"],
        "month_usd": _usd(gemini_month["cost_micro_usd"]),
        "last_month_usd": _usd(gemini_last["cost_micro_usd"]),
        "plan": "종량제",
    }, {
        "provider": "coindesk",
        "label": "CoinDesk Data API",
        "method": "calls",
        "calls": coindesk_by_month.get(current_month, 0),
        "failures": None,
        "input_tokens": None,
        "output_tokens": None,
        "month_usd": round(coindesk_by_month.get(current_month, 0) * coindesk_price, 2),
        "last_month_usd": round(coindesk_by_month.get(last_month, 0) * coindesk_price, 2),
        "plan": "무료 구간" if coindesk_price <= 0 else f"호출당 ${coindesk_price:g}",
    }]
    openai_month = provider_months.get((PROVIDER_OPENAI, current_month), bucket())
    openai_last = provider_months.get((PROVIDER_OPENAI, last_month), bucket())
    from .ai_runtime import REASONING_EFFORT, default_model
    providers.insert(0, {
        # 추론 수준은 설정값(OPENAI_REASONING_EFFORT)을 그대로 보여준다 — 글자로 박아 두면 바뀐 걸 못 따라간다.
        "provider": PROVIDER_OPENAI, "label": f"OpenAI · {default_model()} ({REASONING_EFFORT})",
        "method": "estimate", "calls": openai_month["calls"], "failures": openai_month["failures"],
        "input_tokens": openai_month["input_tokens"], "output_tokens": openai_month["output_tokens"],
        "month_usd": _usd(openai_month["cost_micro_usd"]), "last_month_usd": _usd(openai_last["cost_micro_usd"]),
        "plan": "종량제 · 추론 토큰 포함",
    })
    for name, entry in fixed.items():
        providers.append({
            "provider": name,
            "label": entry["label"],
            "method": entry["method"],
            "calls": None,
            "failures": None,
            "input_tokens": None,
            "output_tokens": None,
            "month_usd": fixed_month_usd(entry["usd"], current_month, today=today, since=fixed_since[name]),
            "last_month_usd": fixed_month_usd(entry["usd"], last_month, today=today, since=fixed_since[name]),
            "plan": entry["plan"],
            "prorated": True,  # 화면 캡션용: 이번 달은 경과일 안분, 시작 월 이전은 0
            "since": fixed_since[name],
        })

    purposes = []
    known = {code for code, _label, _limit in PURPOSES}
    labels = {code: label for code, label, _limit in PURPOSES}
    for code in [c for c, _l, _x in PURPOSES] + sorted(p for p in by_purpose if p not in known):
        usage = by_purpose.get(code, bucket())
        purposes.append({
            "purpose": code,
            "label": labels.get(code) or (code if code else "(미지정)"),
            "code": code,
            "calls": usage["calls"],
            "failures": usage["failures"],
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "cost_usd": _usd(usage["cost_micro_usd"]),
            "daily_limit": daily_limit_for(code),
        })

    daily = []
    for day in daily_days:
        usage = by_day.get(day, bucket())
        daily.append({
            "day": day,
            "gemini_calls": provider_days.get((PROVIDER_GEMINI, day), bucket())["calls"],
            "openai_calls": provider_days.get((PROVIDER_OPENAI, day), bucket())["calls"],
            "ai_calls": usage["calls"],
            "failures": usage["failures"],
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "cost_usd": _usd(usage["cost_micro_usd"]),
            "coindesk_calls": coindesk_by_day.get(day, 0),
        })

    today_usage = by_day.get(today_key, bucket())
    return {
        "generated_at": _iso(millis),
        "month": current_month,
        "month_days_elapsed": today.day,
        "kpis": {
            "month_total_usd": round(sum(p["month_usd"] for p in providers), 2),
            "last_month_total_usd": round(sum(p["last_month_usd"] for p in providers), 2),
            "gemini_month_usd": _usd(gemini_month["cost_micro_usd"]),
            "gemini_calls_month": gemini_month["calls"],
            "gemini_failures_month": gemini_month["failures"],
            "gemini_today_usd": _usd(provider_days.get((PROVIDER_GEMINI, today_key), bucket())["cost_micro_usd"]),
            # 실패는 토큰 0 으로 기록된다 — 호출은 많은데 비용이 0 이면 절약이 아니라 장애다.
            "gemini_failures_today": provider_days.get((PROVIDER_GEMINI, today_key), bucket())["failures"],
            "gemini_calls_today": provider_days.get((PROVIDER_GEMINI, today_key), bucket())["calls"],
            "ai_month_usd": _usd(ai_month["cost_micro_usd"]),
            "ai_calls_month": ai_month["calls"],
            "ai_today_usd": _usd(today_usage["cost_micro_usd"]),
            "ai_failures_today": today_usage["failures"],
            "ai_calls_today": today_usage["calls"],
        },
        "monthly": monthly,
        "providers": providers,
        "purposes": purposes,
        "daily": daily,
    }
