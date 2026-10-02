"""Exchange and manual quote-budget contracts; no external calls."""
import json
import math
import secrets

import pytest
from pydantic import ValidationError

from app import ask, ask_candidates
from app.db import AskMacroSession, User, get_session
from app.engine import BacktestResult


def answers(**kwargs):
    body = dict(risk_profile="balanced", market="spot", leverage=1,
                invest_horizon="weeks", watch_frequency="sometimes")
    return ask.CandidatesRequest(**(body | kwargs))


def test_exchange_and_budget_legacy_default_and_positive_validation():
    assert answers().exchange == "binance"
    assert answers().account_balance == ask.CAPITAL
    assert answers(exchange="upbit", account_balance=123_456).quote_currency == "KRW"
    for value in (0, -1, math.nan, math.inf, -math.inf, True):
        with pytest.raises(ValidationError):
            answers(account_balance=value)
    with pytest.raises(ValidationError):
        answers(exchange="unknown")
    with pytest.raises(ValidationError):
        answers(exchange="bithumb", market="futures", account_balance=50_000)
    with pytest.raises(ValidationError):
        answers(exchange="upbit", leverage=2, account_balance=50_000)
    with pytest.raises(ValidationError):
        answers(exchange="upbit")


def test_templates_use_manual_quote_budget_and_exchange_for_every_rule():
    req = answers(exchange="upbit", risk_profile="aggressive", account_balance=250_000)
    plan = ask._plan(req, "KRW-BTC")
    candidates = ask.build_templates(plan)
    assert {c.macro.rule_type.value for c in candidates} == {"A", "C", "E", "F", "G", "H", "I", "J"}
    for candidate in candidates:
        macro = candidate.macro
        assert macro.exchange == "upbit" and macro.quote_currency == "KRW"
        assert macro.symbol == "KRW-BTC" and macro.leverage == 1
        assert macro.market == "spot" and macro.position_side.value == "long"
        if macro.rule_type.value != "C":
            assert macro.initial_capital == 250_000
    h = next(c.macro for c in candidates if c.macro.rule_type.value == "H")
    assert h.params["base_order_size"] == 25_000
    assert h.params["safety_order_size"] == 25_000
    c = next(c.macro for c in candidates if c.macro.rule_type.value == "C")
    assert c.params["amount_per_buy"] <= 250_000 / 90


def test_ai_cannot_override_exchange_or_budget():
    plan = ask._plan(answers(exchange="bithumb", account_balance=90_000), "KRW-ETH")
    macro = ask._make_macro(plan, "A", {"params": {"take_profit_pct": 3, "initial_capital": 10**12}}, ["KRW-ETH"])
    assert macro.initial_capital == 90_000
    assert "90000" in ask._ai_system(plan) and "KRW" in ask._ai_system(plan)
    assert "1000000" not in ask._ai_system(plan)


def test_native_krw_pool_never_mixes_usdt_or_computes_usdt_pegs():
    def t(symbol, volume):
        return dict(symbol=symbol, quoteVolume=str(volume), lastPrice="100000000",
                    highPrice="101000000", lowPrice="99000000", priceChangePercent="1")
    tickers = [t("KRW-BTC", 90_000_000_000), t("KRW-ETH", 80_000_000_000),
               t("BTCUSDT", 1_000_000_000_000), t("KRW-TINY", 10_000)]
    pool = ask_candidates.build_pool(tickers, profile="balanced", exchange="upbit")
    assert {c["symbol"] for c in pool} == {"KRW-BTC", "KRW-ETH"}
    assert all(c["quote_currency"] == "KRW" for c in pool)
    assert pool[0]["base"] == "BTC"


def test_prompt_declares_exchange_manual_budget_and_quote():
    prompt = ask_candidates.build_prompt([], profile="balanced", horizon="weeks", watch="sometimes",
                                        exchange="bithumb", account_balance=75_000)
    assert "bithumb" in prompt and "75000" in prompt and "KRW" in prompt


def test_ask_pair_shape_defers_exchange_identity_to_saved_session():
    assert ask.AskRequest(session_id=1, symbol=" krw-btc ").symbol == "KRW-BTC"
    with pytest.raises(ValidationError):
        ask.AskRequest(session_id=1, symbol="BTC-KRW")


def test_domestic_flow_uses_actual_listings_and_immutable_saved_budget(monkeypatch):
    from app.data import krw
    monkeypatch.setattr(krw, "get_all_tickers", lambda exchange: [
        dict(symbol=symbol, quoteVolume="90000000000", lastPrice="100000000",
             highPrice="101000000", lowPrice="99000000", priceChangePercent="1")
        for symbol in ("KRW-BTC", "KRW-ETH", "KRW-NOTLISTED")
    ])
    monkeypatch.setattr(ask.symbols_mod, "list_symbols", lambda **kwargs: {
        "items": [{"symbol": "KRW-BTC", "spot": True}, {"symbol": "KRW-ETH", "spot": True}]
    })
    monkeypatch.setattr(ask, "_candidate_ai", lambda: None)
    monkeypatch.setattr(ask, "propose_with_ai", lambda plan: [])
    with get_session() as db:
        suffix = secrets.token_hex(4)
        user = User(username=f"ask_exchange_{suffix}", email=f"{suffix}@example.test",
                    password_hash="", created_at="2026-10-02T00:00:00Z",
                    ask_consent_version=ask.DISCLAIMER_VERSION)
        db.add(user)
        db.commit()
        db.refresh(user)
        response = ask.run_candidates(db, user, answers(exchange="upbit", account_balance=75_000))
        assert {c["symbol"] for c in response["candidates"]} == {"KRW-BTC", "KRW-ETH"}
        session = db.get(AskMacroSession, response["session_id"])
        assert json.loads(session.request_json)["account_balance"] == 75_000
        assert json.loads(session.request_json)["exchange"] == "upbit"
        seen = []

        def backtest(macro):
            seen.append(macro)
            return BacktestResult(initial_capital=75_000, final_equity=80_000, final_return_pct=6,
                                  mdd_pct=1, win_rate_pct=60, total_trades=10, trades=[], equity_curve=[])

        # Extra caller fields cannot override the immutable session answers.
        request = ask.AskRequest(session_id=session.id, symbol="KRW-BTC", exchange="binance", account_balance=999)
        result = ask.run_ask(db, user, request, backtest)
        assert result["results"] and seen
        assert all(m.exchange == "upbit" and m.initial_capital == 75_000 for m in seen)
        for symbol in ("BTCUSDT", "KRW-NOTLISTED"):
            with pytest.raises(ask.AskError) as exc:
                ask.run_ask(db, user, ask.AskRequest(session_id=session.id, symbol=symbol), backtest)
            assert exc.value.status == 422


def test_domestic_listing_failure_is_closed_not_candidate_fallback(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("listing unavailable")
    monkeypatch.setattr(ask, "_tradable_symbols", fail)
    row = AskMacroSession(user_id=1, day_kst="2026-10-02", request_json="{}", results_json="[]",
                          created_at="2026-10-02T00:00:00Z", created_ms=1,
                          candidates_json=json.dumps({"items": [{"symbol": "KRW-SCAM"}]}))
    assert ask._allowed_symbols(row, "spot", "bithumb") == set()
