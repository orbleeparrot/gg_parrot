"""Gemini 사용량 원장 — 토큰 × 단가 추정, 하루 행 누적, 실패 집계, 비용 보고 모양, 어댑터 기록."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors as genai_errors
from sqlalchemy import delete
from sqlmodel import select

from app import ai_runtime, api_usage
from app.db import ApiUsageDaily, TickerNewsAiBudget, get_session

_KST = timezone(timedelta(hours=9))
# 2026-09-17 10:00 KST — 달력 계산(월 라벨·경과일)이 실제 오늘과 무관하게 고정된다.
NOW_MS = int(datetime(2026, 9, 17, 10, 0, tzinfo=_KST).timestamp() * 1000)
TODAY = "2026-09-17"


@pytest.fixture(autouse=True)
def clean_ledger(monkeypatch):
    # 다른 테스트가 가짜 SDK 로 부른 create() 도 같은 원장에 쓴다 — 매번 빈 원장에서 시작.
    with get_session() as db:
        db.exec(delete(ApiUsageDaily))
        db.exec(delete(TickerNewsAiBudget).where(TickerNewsAiBudget.budget_date_kst.like("coindesk_news:%")))
        db.commit()
    for name in ("GEMINI_PRICE_INPUT_USD_PER_M", "GEMINI_PRICE_OUTPUT_USD_PER_M", "GEMINI_PRICE_CACHED_USD_PER_M",
                 "GEMINI_PRICES_JSON", "ADMIN_FIXED_COSTS_JSON", "COINDESK_COST_PER_CALL_USD",
                 "POSITION_NEWS_MAX_AI_ANALYSES_PER_DAY", "AI_EXPLAIN_MAX_CALLS_PER_DAY",
                 "NEWS_MARKET_SUMMARY_MAX_CALLS_PER_DAY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(api_usage, "_last_warn_monotonic", None)
    yield


def _usage(prompt=1000, out=200, cached=0, thoughts=0):
    return SimpleNamespace(prompt_token_count=prompt, candidates_token_count=out,
                           cached_content_token_count=cached, thoughts_token_count=thoughts,
                           total_token_count=(prompt or 0) + (out or 0) + (thoughts or 0))


def _rows() -> list[dict]:
    with get_session() as db:
        rows = db.exec(select(ApiUsageDaily).order_by(ApiUsageDaily.day_kst, ApiUsageDaily.purpose)).all()
        return [row.model_dump() for row in rows]


# --- 비용 계산 ----------------------------------------------------------------
def test_usage_tokens_treats_none_as_zero_and_thoughts_as_output():
    assert api_usage.usage_tokens(None) == {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0}
    assert api_usage.usage_tokens(_usage(None, None, None, None)) == {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0}
    assert api_usage.usage_tokens(_usage(1000, 200, cached=200, thoughts=50)) == {
        "input_tokens": 1000, "output_tokens": 250, "cached_tokens": 200,
    }
    # dict 모양도 받는다(테스트·재생 로그).
    assert api_usage.usage_tokens({"prompt_token_count": 7, "candidates_token_count": 3})["input_tokens"] == 7


def test_cost_uses_default_price_table_and_bills_cached_input_cheaper():
    # 1M 토큰당 $0.10 / $0.40 / $0.025 → 토큰 하나가 그 값의 micro USD.
    assert api_usage.cost_micro_usd("m", input_tokens=1000, output_tokens=250, cached_tokens=200) == 80 + 5 + 100
    assert api_usage.cost_micro_usd("m", input_tokens=0, output_tokens=0) == 0


def test_price_env_and_per_model_override(monkeypatch):
    monkeypatch.setenv("GEMINI_PRICE_INPUT_USD_PER_M", "0.5")
    monkeypatch.setenv("GEMINI_PRICES_JSON", json.dumps({"gemini-pro": {"input": 1.0, "output": 2.0}}))
    assert api_usage.gemini_prices("gemini-pro") == {"input": 1.0, "output": 2.0, "cached": 0.025}
    assert api_usage.gemini_prices("gemini-3.5-flash-lite") == {"input": 0.5, "output": 0.40, "cached": 0.025}
    assert api_usage.cost_micro_usd("gemini-pro", input_tokens=1000, output_tokens=100) == 1000 + 200

    monkeypatch.setenv("GEMINI_PRICES_JSON", "{not json")
    assert api_usage.gemini_prices("gemini-pro")["input"] == 0.5  # 깨진 JSON 은 무시, raise 없음


# --- 기록 ------------------------------------------------------------------
def test_record_accumulates_calls_tokens_cost_and_failures_per_day_row():
    assert api_usage.record_gemini_usage(model="m", purpose="ai_explain", usage=_usage(1000, 200), now_ms=NOW_MS)
    assert api_usage.record_gemini_usage(model="m", purpose="ai_explain", usage=_usage(500, 100), now_ms=NOW_MS + 1000)
    assert api_usage.record_gemini_usage(model="m", purpose="ai_explain", usage=None, ok=False, now_ms=NOW_MS + 2000)
    assert api_usage.record_gemini_usage(model="m", purpose="position_news", usage=_usage(10, 1), now_ms=NOW_MS)

    rows = _rows()
    assert [(r["day_kst"], r["provider"], r["model"], r["purpose"]) for r in rows] == [
        (TODAY, "gemini", "m", "ai_explain"), (TODAY, "gemini", "m", "position_news"),
    ]
    explain = rows[0]
    assert (explain["calls"], explain["failures"]) == (3, 1)
    assert (explain["input_tokens"], explain["output_tokens"]) == (1500, 300)
    assert explain["cost_micro_usd"] == 150 + 120
    assert explain["updated_ms"] == NOW_MS + 2000


def test_record_with_borrowed_session_commits_on_that_session():
    with get_session() as db:
        assert api_usage.record_gemini_usage(model="m", purpose="ai_challenge", usage=_usage(1, 1), now_ms=NOW_MS, db=db)
    assert _rows()[0]["calls"] == 1


def test_record_never_raises_and_warns_at_most_once_per_minute(caplog):
    class BrokenDb:
        rolled_back = 0

        def get_bind(self):
            raise RuntimeError("no database")

        def rollback(self):
            self.rolled_back += 1

    broken = BrokenDb()
    with caplog.at_level(logging.WARNING, logger="app.api_usage"):
        assert api_usage.record_gemini_usage(model="m", purpose="x", usage=_usage(), db=broken) is False
        assert api_usage.record_gemini_usage(model="m", purpose="x", usage=_usage(), db=broken) is False
    assert broken.rolled_back == 2  # 빌린 세션은 실패한 트랜잭션 채로 돌려주지 않는다
    warnings = [r for r in caplog.records if "Gemini 사용량 기록 실패" in r.getMessage()]
    assert len(warnings) == 1 and "no database" in warnings[0].getMessage()
    assert _rows() == []


# --- 보고 ------------------------------------------------------------------
def _seed_report_rows():
    big = _usage(10_000_000, 1_000_000)  # $1.00 + $0.40
    august = int(datetime(2026, 8, 20, 12, 0, tzinfo=_KST).timestamp() * 1000)
    assert api_usage.record_gemini_usage(model="gemini-3.5-flash-lite", purpose="position_news", usage=big, now_ms=NOW_MS)
    assert api_usage.record_gemini_usage(model="gemini-3.5-flash-lite", purpose="title_translation", usage=None, ok=False, now_ms=NOW_MS)
    assert api_usage.record_gemini_usage(model="gemini-3.5-flash-lite", purpose="ai_explain", usage=big, now_ms=august)
    with get_session() as db:
        db.add(TickerNewsAiBudget(budget_date_kst=f"coindesk_news:{TODAY}", used=12, updated_at=""))
        db.add(TickerNewsAiBudget(budget_date_kst="coindesk_news:2026-08-20", used=4, updated_at=""))
        db.add(TickerNewsAiBudget(budget_date_kst="coindesk_news:lifetime", used=999, updated_at=""))
        db.commit()


def test_costs_report_shape_and_totals(monkeypatch):
    monkeypatch.setenv("COINDESK_COST_PER_CALL_USD", "0.5")
    monkeypatch.setenv("POSITION_NEWS_MAX_AI_ANALYSES_PER_DAY", "1000")
    _seed_report_rows()

    with get_session() as db:
        report = api_usage.costs_report(db, months=6, now_ms=NOW_MS)

    assert report["month"] == "2026-09" and report["month_days_elapsed"] == 17
    assert report["generated_at"].endswith("Z")

    monthly = report["monthly"]
    assert [m["month"] for m in monthly] == ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"]
    assert [m["label"] for m in monthly] == ["4월", "5월", "6월", "7월", "8월", "9월*"]
    current = monthly[-1]
    assert set(current["providers"]) >= {"gemini", "coindesk", "render", "supabase", "vercel", "prefect"}
    assert current["providers"]["gemini"] == 1.4 and current["providers"]["coindesk"] == 6.0
    # 구독 고정비: 이번 달(9월, 17/30 경과)은 안분 — $14 → 7.93, $25 → 14.17. 시작 월(기본 지난달) 이전은 0.
    assert (current["providers"]["render"], current["providers"]["supabase"]) == (7.93, 14.17)
    assert current["total_usd"] == round(1.4 + 6.0 + 7.93 + 14.17, 2)
    assert monthly[-2]["providers"] == {"gemini": 1.4, "coindesk": 2.0, "render": 14.0, "supabase": 25.0, "vercel": 0.0, "prefect": 0.0}
    assert monthly[0]["providers"]["gemini"] == 0.0 and monthly[0]["total_usd"] == 0.0, "4월 구독비는 없던 비용"
    assert [m["providers"]["render"] for m in monthly] == [0.0, 0.0, 0.0, 0.0, 14.0, 7.93]

    providers = report["providers"]
    assert [p["provider"] for p in providers] == ["gemini", "coindesk", "render", "supabase", "vercel", "prefect"]
    gemini = providers[0]
    assert gemini["label"] == "Gemini · gemini-3.5-flash-lite" and gemini["method"] == "estimate"
    assert (gemini["calls"], gemini["failures"], gemini["input_tokens"], gemini["output_tokens"]) == (2, 1, 10_000_000, 1_000_000)
    assert (gemini["month_usd"], gemini["last_month_usd"]) == (1.4, 1.4)
    coindesk = providers[1]
    assert (coindesk["method"], coindesk["calls"], coindesk["month_usd"], coindesk["last_month_usd"]) == ("calls", 12, 6.0, 2.0)
    render = providers[2]
    assert (render["label"], render["method"], render["month_usd"], render["last_month_usd"], render["plan"]) == (
        "Render (web + worker)", "fixed", 7.93, 14.0, "Starter ×2")
    assert render["prorated"] is True and render["since"] == "2026-08"

    kpis = report["kpis"]
    assert kpis == {
        "month_total_usd": round(1.4 + 6.0 + 7.93 + 14.17, 2), "last_month_total_usd": round(1.4 + 2.0 + 39.0, 2),
        "gemini_month_usd": 1.4, "gemini_calls_month": 2, "gemini_failures_month": 1, "gemini_today_usd": 1.4,
    }

    purposes = report["purposes"]
    assert [p["code"] for p in purposes] == ["position_news", "title_translation", "community_summaries",
                                             "ai_explain", "market_news_summary", "ai_challenge"]
    assert purposes[0]["label"] == "종목 뉴스 분류 · 요약" and purposes[0]["calls"] == 1 and purposes[0]["cost_usd"] == 1.4
    assert purposes[0]["daily_limit"] == 1000  # env 가 있으면 그 값
    assert purposes[1]["failures"] == 1 and purposes[1]["daily_limit"] == "없음"
    assert purposes[3]["calls"] == 0  # 8월 행은 이번 달 표에 없다
    assert (purposes[3]["daily_limit"], purposes[4]["daily_limit"]) == (20, 6)  # env 없으면 코드 기본값

    daily = report["daily"]
    assert len(daily) == 30 and daily[-1]["day"] == TODAY and daily[0]["day"] == "2026-08-19"
    assert (daily[-1]["gemini_calls"], daily[-1]["cost_usd"], daily[-1]["coindesk_calls"]) == (2, 1.4, 12)
    assert (daily[1]["gemini_calls"], daily[1]["cost_usd"], daily[1]["coindesk_calls"]) == (1, 1.4, 4)


def test_costs_report_fixed_costs_env_replaces_defaults_and_months_clamp(monkeypatch):
    monkeypatch.setenv("ADMIN_FIXED_COSTS_JSON", json.dumps({"render": {"label": "R", "usd": 30, "plan": "x"}}))
    with get_session() as db:
        report = api_usage.costs_report(db, months=2, now_ms=NOW_MS)
    assert [m["label"] for m in report["monthly"]] == ["8월", "9월*"]
    assert report["monthly"][-1]["providers"] == {"gemini": 0.0, "coindesk": 0.0, "render": 17.0}  # 30 × 17/30
    assert [p["provider"] for p in report["providers"]] == ["gemini", "coindesk", "render"]
    assert report["providers"][0]["label"] == "Gemini · gemini-3.5-flash-lite"  # 사용 기록이 없으면 기본 모델
    assert report["kpis"]["month_total_usd"] == 17.0


def test_fixed_costs_since_month_zeroes_earlier_months_and_prorates_the_current_one(monkeypatch):
    """A4: since 가 있으면 그 전 달은 0, 이번 달은 경과일 안분, 지난 달은 전액. 잘못된 since 는 무시(기본 = 지난달)."""
    monkeypatch.setenv("ADMIN_FIXED_COSTS_JSON", json.dumps({
        "supabase": {"label": "S", "usd": 25, "since": "2026-06"},
        "render": {"label": "R", "usd": 14, "since": "2026-09"},
        "vercel": {"label": "V", "usd": 20, "since": "not-a-month"},
    }))
    assert api_usage.fixed_costs()["vercel"]["since"] == ""
    with get_session() as db:
        report = api_usage.costs_report(db, months=6, now_ms=NOW_MS)
    by_month = {m["month"]: m["providers"] for m in report["monthly"]}
    assert [by_month[m]["supabase"] for m in ("2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09")] == [0.0, 0.0, 25.0, 25.0, 25.0, 14.17]
    assert [by_month[m]["render"] for m in ("2026-07", "2026-08", "2026-09")] == [0.0, 0.0, 7.93]
    assert [by_month[m]["vercel"] for m in ("2026-07", "2026-08", "2026-09")] == [0.0, 20.0, 11.33]
    providers = {p["provider"]: p for p in report["providers"]}
    assert (providers["render"]["month_usd"], providers["render"]["last_month_usd"], providers["render"]["since"]) == (7.93, 0.0, "2026-09")
    assert (providers["supabase"]["month_usd"], providers["supabase"]["last_month_usd"]) == (14.17, 25.0)
    assert report["kpis"]["last_month_total_usd"] == 45.0 and report["kpis"]["month_total_usd"] == round(14.17 + 7.93 + 11.33, 2)
    # 함수 단위: 말일이면 전액, 1일이면 1/일수.
    from datetime import date
    assert api_usage.fixed_month_usd(31, "2026-10", today=date(2026, 10, 31), since="") == 31.0
    assert api_usage.fixed_month_usd(31, "2026-10", today=date(2026, 10, 1), since="") == 1.0
    assert api_usage.fixed_month_usd(31, "2026-09", today=date(2026, 10, 1), since="2026-10") == 0.0


# --- 어댑터: create() 가 용도와 함께 기록한다 -----------------------------------
class _FakeModels:
    def __init__(self, outcome, usage=None):
        self.outcome = outcome
        self.usage = usage

    def generate_content(self, **kwargs):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return SimpleNamespace(text=self.outcome, usage_metadata=self.usage)


def _messages(outcome, usage=None):
    return ai_runtime._Messages(SimpleNamespace(models=_FakeModels(outcome, usage)))


def test_adapter_records_success_with_purpose_tokens_and_model():
    response = _messages("답", _usage(1000, 200, thoughts=50)).create(
        model="gemini-3.5-flash-lite", max_tokens=10, messages=[{"role": "user", "content": "q"}], purpose="ai_explain",
    )
    assert response.content[0].text == "답"
    assert not hasattr(response, "usage")  # 캐시로 복사되는 응답에는 싣지 않는다
    (row,) = _rows()
    assert (row["model"], row["purpose"], row["calls"], row["failures"]) == ("gemini-3.5-flash-lite", "ai_explain", 1, 0)
    assert (row["input_tokens"], row["output_tokens"]) == (1000, 250)


def test_adapter_records_failures_for_provider_and_transport_errors():
    with pytest.raises(ai_runtime.AiRateLimitError):
        _messages(genai_errors.APIError(429, {"error": {"message": "slow down"}})).create(
            model="m", max_tokens=1, messages=[{"role": "user", "content": "q"}], purpose="position_news",
        )
    with pytest.raises(ai_runtime.AiConnectionError):
        _messages(httpx.ConnectError("down")).create(
            model="m", max_tokens=1, messages=[{"role": "user", "content": "q"}], purpose="position_news",
        )
    (row,) = _rows()
    assert (row["purpose"], row["calls"], row["failures"], row["input_tokens"], row["cost_micro_usd"]) == ("position_news", 2, 2, 0, 0)


def test_adapter_purpose_defaults_to_empty_and_ledger_failure_does_not_break_the_call(monkeypatch):
    seen = []
    monkeypatch.setattr(ai_runtime, "record_gemini_usage", lambda **kw: seen.append(kw))
    response = _messages("ok", _usage(3, 1)).create(model="m", max_tokens=1, messages=[{"role": "user", "content": "q"}])
    assert response.content[0].text == "ok"
    assert seen[0]["purpose"] == "" and seen[0]["ok"] is True and seen[0]["usage"].prompt_token_count == 3
