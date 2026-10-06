import hashlib
import hmac
import json

import pytest
from pydantic import ValidationError

from app.engine import Macro
from app import macro_signing


def make_macro(**changes):
    return Macro(rule_type="A", params={"take_profit_pct": 3, "initial_capital": 1000}, **changes)


def test_legacy_macro_defaults_to_binance_usdt():
    macro = make_macro()
    assert (macro.exchange, macro.quote_currency) == ("binance", "USDT")


def test_domestic_macro_uses_native_krw_and_preserves_portfolio():
    macro = make_macro(exchange="upbit", symbol="KRW-BTC", symbols=["KRW-BTC", "KRW-T"])
    assert macro.quote_currency == "KRW"
    assert macro.resolved_market() == "spot"
    assert macro.for_symbol("KRW-T").exchange == "upbit"


@pytest.mark.parametrize("changes", [
    {"position_side": "short"}, {"leverage": 2}, {"market": "futures"},
    {"quote_currency": "USDT"}, {"symbol": "BTCUSDT"},
    {"fees": {"funding_pct": 0.01}},
])
def test_domestic_unsupported_settings_rejected(changes):
    with pytest.raises(ValidationError):
        make_macro(exchange="bithumb", **({"symbol": "KRW-BTC"} | changes))


def test_binance_cannot_accept_domestic_symbol_or_quote():
    for changes in ({"symbol": "KRW-BTC"}, {"quote_currency": "KRW"}, {"exchange": "unknown"}):
        with pytest.raises(ValidationError):
            make_macro(**changes)


def test_domestic_short_flip_strategy_is_rejected():
    with pytest.raises(ValidationError):
        Macro(exchange="upbit", symbol="KRW-T", rule_type="K", params={"initial_capital": 1000})


def test_dca_backtest_respects_explicit_quote_budget():
    import pandas as pd
    from app.engine import run_backtest
    macro = Macro(exchange="bithumb", symbol="KRW-BTC", rule_type="C",
                  params={"initial_capital": 100, "amount_per_buy": 60, "interval_days": 1},
                  fees={"commission_pct": 0, "slippage_pct": 0})
    df = pd.DataFrame({"timestamp": pd.date_range("2024-01-01", periods=3, freq="D"), "close": [100, 100, 100]})
    result = run_backtest(macro, df)
    assert result.initial_capital == 100
    assert result.total_trades <= 2
    assert result.final_return_pct == 0


def test_native_krw_market_maps_to_exact_news_asset():
    from app.news import asset_from_market_symbol
    assert asset_from_market_symbol("KRW-T") == "T"
    assert asset_from_market_symbol("KRW-BTC") == "BTC"
    assert asset_from_market_symbol("TUSDT") == "T"


def test_legacy_library_performance_identity_survives_exchange_defaults():
    from app.user_macros import _same_macro
    macro = make_macro()
    legacy = macro.model_dump(mode="json")
    legacy.pop("exchange")
    legacy.pop("quote_currency")
    assert _same_macro(json.dumps(legacy), macro.model_dump_json())


def test_locked_leaderboard_exposes_exchange_but_not_paid_parameters():
    from app.db import LeaderboardEntry
    from app.leaderboard import _entry_view
    macro = make_macro(exchange="bithumb", symbol="KRW-BTC")
    row = LeaderboardEntry(id=1, owner_user_id=42, user_id="owner", symbol=macro.symbol,
                           macro_json=macro.model_dump_json(), username="owner", nickname="owner",
                           created_ms=1800000000000, created_at="2026-10-02T00:00:00Z")
    result = _entry_view(row, {}, viewer_id="visitor")
    assert result["locked"] is True
    assert result["macro"] is None
    assert result["human_summary"] == ""
    assert result["exchange"] == "bithumb"
    assert result["quote_currency"] == "KRW"


@pytest.mark.parametrize("symbol", ["BTCUSDT", "btcusdt"])
def test_new_signature_binds_exchange_and_legacy_signature_still_verifies(symbol):
    macro = make_macro(symbol=symbol)
    assert macro.symbol == symbol
    old = macro.model_dump(mode="json")
    old.pop("exchange", None)
    old.pop("quote_currency", None)
    old.pop("entry_filter", None)  # v1 서명에는 이 필드도 없다
    old.pop("legs", None)  # v1 에는 묶음 칸도 없었다
    old.pop("bundle_risk", None)
    payload = json.dumps(old, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    sig = {"v": 1, "hmac": hmac.new(macro_signing._key(), payload, hashlib.sha256).hexdigest()}
    assert macro_signing.verify(macro, sig)
    signed = macro_signing.sign(macro)
    assert signed["v"] == macro_signing.SIG_VERSION
    assert macro_signing.verify(macro, signed)
    domestic = make_macro(exchange="upbit", symbol="KRW-BTC")
    assert not macro_signing.verify(domestic, sig)
    assert not macro_signing.verify(domestic, signed)


def test_every_exchange_reports_runner_support():
    # 실행기 v10 은 업비트 · 빗썸 원화 현물에 주문을 낸다. 국내를 거짓으로 두면 API 가 거짓말을 하고,
    # 다음에 이 값을 읽는 화면이 국내 실행을 막게 된다.
    from app.exchanges import capabilities
    for exchange in ("binance", "upbit", "bithumb"):
        assert capabilities(exchange)["runner_supported"] is True, exchange
