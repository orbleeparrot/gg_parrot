"""유료 API 사용량 기록과 비용 보고 — 관리자 대시보드 'API 비용' 탭의 데이터.

Gemini 는 응답의 ``usage_metadata`` 토큰을 호출마다 ``ApiUsageDaily`` 에 하루 단위로 누적한다.
기록 지점은 ``ai_runtime._Messages.create`` 한 곳뿐이라 캐시 히트·singleflight 공유는 빠지고
재시도는 각각 세어진다 — 즉 실제로 과금된 요청 수와 같다. 비용은 청구서가 아니라 토큰 × 단가표
추정이다(Gemini 에 청구 API 가 없다).

기록은 best-effort: 표가 없거나 DB 가 죽어도 AI 호출 자체를 막지 않는다(예외는 삼키고 1분에 한 번만
경고). CoinDesk 는 여기서 기록하지 않는다 — ``TickerNewsAiBudget`` 의 ``coindesk_news:<날짜>`` 행이
이미 호출 수를 세고 있어 보고 시점에 그 행을 읽는다.
"""
from __future__ import annotations

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
WARN_INTERVAL_SECONDS = 60.0
DAILY_DAYS = 30
MONTHS_DEFAULT = 6

# 1M 토큰당 USD. 3.5 Flash-Lite 기준값이며 env 로 바꾼다(모델별 재정의는 GEMINI_PRICES_JSON).
_DEFAULT_PRICES = {"input": 0.10, "output": 0.40, "cached": 0.025}
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
    ("market_news_summary", "시장 브리핑 요약", ("NEWS_MARKET_SUMMARY_MAX_CALLS_PER_DAY", 6)),
    ("ai_challenge", "일일 챌린지 생성", None),
)
NO_LIMIT_LABEL = "없음"

# 구독형 고정액. 청구 API 가 없는 제공자는 요금제 금액을 설정값으로 둔다(ADMIN_FIXED_COSTS_JSON 이 통째로 대체).
DEFAULT_FIXED_COSTS = {
    "render": {"label": "Render (web + worker)", "usd": 14, "plan": "Starter ×2"},
    "supabase": {"label": "Supabase", "usd": 25, "plan": "Pro"},
    "vercel": {"label": "Vercel", "usd": 0, "plan": "Hobby"},
    "prefect": {"label": "Prefect Cloud", "usd": 0, "plan": "Free"},
}

_COINDESK_PREFIX = "coindesk_news:"
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

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
        result[str(key)] = {
            "label": str(entry.get("label") or key),
            "usd": usd,
            "plan": str(entry.get("plan") or ""),
            "method": str(entry.get("method") or "fixed"),
        }
    return result


def daily_limit_for(purpose: str):
    """화면의 '일일 한도' — 실제 코드가 읽는 env 와 그 기본값을 그대로 보여준다."""
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
) -> bool:
    """Gemini 호출 한 건을 오늘(KST) 행에 더한다. 실패도 calls·failures 에 센다. 절대 raise 하지 않는다."""
    try:
        millis = _now_ms(now_ms)
        tokens = usage_tokens(usage)
        values = {
            "day_kst": day_kst(millis),
            "provider": PROVIDER_GEMINI,
            "model": str(model or "")[:80],
            "purpose": str(purpose or "")[:40],
            "calls": 1,
            "failures": 0 if ok else 1,
            **tokens,
            "cost_micro_usd": cost_micro_usd(model, **tokens),
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
        _warn_throttled("Gemini 사용량 기록 실패", error)
        return False


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
        ApiUsageDaily.provider == PROVIDER_GEMINI, ApiUsageDaily.day_kst >= since_day,
    )).all()

    def bucket() -> dict:
        return {"calls": 0, "failures": 0, "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "cost_micro_usd": 0}

    def add(target: dict, row: ApiUsageDaily) -> None:
        for name in _ACCUMULATED:
            target[name] += int(getattr(row, name) or 0)

    by_month: dict[str, dict] = {}
    by_day: dict[str, dict] = {}
    by_purpose: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    for row in rows:
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

    monthly = []
    for start in month_starts:
        key = _month_key(start)
        gemini = by_month.get(key, bucket())
        providers = {
            PROVIDER_GEMINI: _usd(gemini["cost_micro_usd"]),
            "coindesk": round(coindesk_by_month.get(key, 0) * coindesk_price, 2),
        }
        for name, entry in fixed.items():
            providers[name] = round(entry["usd"], 2)
        monthly.append({
            "month": key,
            "label": f"{start.month}월" + ("*" if key == current_month else ""),
            "providers": providers,
            "total_usd": round(sum(providers.values()), 2),
        })

    gemini_month = by_month.get(current_month, bucket())
    gemini_last = by_month.get(last_month, bucket())
    if by_model:
        top_models = sorted(by_model, key=lambda name: by_model[name]["calls"], reverse=True)
        model_label = top_models[0] or "?"
        if len(top_models) > 1:
            model_label += f" 외 {len(top_models) - 1}"
    else:
        from .ai_runtime import default_model
        model_label = default_model()
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
    for name, entry in fixed.items():
        providers.append({
            "provider": name,
            "label": entry["label"],
            "method": entry["method"],
            "calls": None,
            "failures": None,
            "input_tokens": None,
            "output_tokens": None,
            "month_usd": round(entry["usd"], 2),
            "last_month_usd": round(entry["usd"], 2),
            "plan": entry["plan"],
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
            "gemini_calls": usage["calls"],
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
            "gemini_today_usd": _usd(today_usage["cost_micro_usd"]),
        },
        "monthly": monthly,
        "providers": providers,
        "purposes": purposes,
        "daily": daily,
    }
