"""KRW identity survives chart, shared feeds, backtests and paper lifecycle."""
import asyncio

import pytest

from app import chart, marketdata, paper
from app.data import NoSpotDataError
from app.engine import Macro
from app.engine.candle_feed import CandleFeed
from app.engine.stepper import make_sim


def _macro(exchange="upbit", rule="F"):
    params = ({"rsi_period": 2, "entry_threshold": 30, "exit_threshold": 70, "initial_capital": 700_000}
              if rule == "F" else {"amount_per_buy": 10_000, "interval_days": 1, "initial_capital": 700_000})
    return Macro(exchange=exchange, symbol="KRW-BTC", rule_type=rule,
                 candle_interval="1d" if rule == "C" else "5m", params=params)


def test_domestic_chart_caches_same_code_per_exchange(monkeypatch):
    calls = []
    def fetch(symbol, interval, limit, market, exchange):
        calls.append(exchange)
        return [{"t": 0, "o": 1, "h": 2, "l": 1, "c": 1 if exchange == "upbit" else 2, "v": 1, "closed": True}]
    monkeypatch.setattr(chart, "get_recent_klines", fetch)
    a = chart.get_candles("KRW-BTC", exchange="upbit")
    b = chart.get_candles("KRW-BTC", exchange="bithumb")
    chart.get_candles("KRW-BTC", exchange="upbit")
    assert calls == ["upbit", "bithumb"] and a["candles"] != b["candles"]
    assert a["quote_currency"] == b["quote_currency"] == "KRW"
    assert a["exchange"] == "upbit" and b["exchange"] == "bithumb"


def test_domestic_chart_cannot_request_or_fallback_to_futures(monkeypatch):
    calls = []
    def fetch(*args, **kwargs):
        calls.append(kwargs)
        raise NoSpotDataError("missing KRW source")
    monkeypatch.setattr(chart, "get_recent_klines", fetch)
    with pytest.raises(ValueError):
        chart.get_candles("KRW-BTC", market="futures", exchange="upbit")
    assert not calls
    with pytest.raises(NoSpotDataError):
        chart.get_candles("KRW-BTC", exchange="upbit")
    assert len(calls) == 1 and calls[0]["exchange"] == "upbit"


def test_default_binance_paths_reject_native_krw_symbol_before_network(monkeypatch):
    from app import data
    from app.data import binance
    monkeypatch.setattr(binance, "get_recent_klines", lambda *args, **kwargs: pytest.fail("must not use Binance"))
    with pytest.raises(ValueError):
        data.get_recent_klines("KRW-BTC")
    with pytest.raises(ValueError):
        chart.get_candles("KRW-BTC")


def test_domestic_backtest_never_falls_back_to_binance_or_futures(monkeypatch):
    calls = []
    def fetch(*args, **kwargs):
        calls.append(kwargs)
        raise NoSpotDataError("missing KRW source")
    monkeypatch.setattr(marketdata, "get_klines", fetch)
    with pytest.raises(NoSpotDataError):
        marketdata.fetch_klines_for_macro(_macro(), 0, 60_000)
    assert len(calls) == 1 and calls[0]["exchange"] == "upbit" and calls[0]["market"] == "spot"


def test_candle_subscriptions_with_same_code_are_exchange_isolated():
    seen, delivered = [], []
    async def fetch(symbol, interval, limit, market, *, exchange):
        seen.append(exchange)
        return [{"t": 300_000, "o": 1, "h": 2, "l": 1, "c": 10 if exchange == "upbit" else 20, "closed": True}]
    async def callback(symbol, candle):
        delivered.append(candle.c)
    feed = CandleFeed(fetch=fetch, now_ms=lambda: 300_000)
    # Register outside a loop; tests invoke only the deterministic polling API.
    a = feed.subscribe("KRW-BTC", "5m", "spot", callback, since_t=0, exchange="upbit")
    b = feed.subscribe("KRW-BTC", "5m", "spot", callback, since_t=0, exchange="bithumb")
    try:
        asyncio.run(feed.poll_once(a.key))
        asyncio.run(feed.poll_once(b.key))
        assert a.key != b.key and seen == ["upbit", "bithumb"] and delivered == [10, 20]
    finally:
        feed.unsubscribe(a)
        feed.unsubscribe(b)


def test_dca_respects_explicit_native_currency_capital():
    macro = _macro(rule="C")
    assert paper._session_initial(macro) == 700_000
    assert make_sim(macro).initial_capital == 700_000


def test_domestic_replay_does_not_invent_prices_on_failure(monkeypatch):
    def fetch(*args, **kwargs):
        assert kwargs["exchange"] == "bithumb"
        raise NoSpotDataError("no candles")
    monkeypatch.setattr(paper, "get_klines", fetch)
    monkeypatch.setattr(paper, "_synthetic_intraday", lambda symbol: pytest.fail("must not fabricate KRW prices"))
    with pytest.raises(NoSpotDataError):
        paper._load_replay_prices("KRW-BTC", exchange="bithumb")


def test_paper_history_and_subscriptions_keep_exchange_on_resume(monkeypatch):
    seen = []
    class Feed:
        async def history(self, symbol, interval, market, n, *, exchange):
            seen.append(("history", exchange))
            return [(i * 300_000, 100, 100, 100, 100) for i in range(5)]
        def subscribe(self, symbol, interval, market, callback, **kwargs):
            seen.append(("subscribe", kwargs["exchange"]))
            return object()
    monkeypatch.setattr(paper, "feed", Feed())
    macro = _macro("bithumb")
    sim = make_sim(macro, initial_capital=700_000)
    runner = paper._Runner(0, sim, "KRW-BTC", "live", 700_000)
    runner.driver.macro = macro
    asyncio.run(paper._attach_feed(runner))
    assert seen == [("history", "bithumb"), ("subscribe", "bithumb")]


def test_bithumb_closed_candle_schedule_uses_kst_grid():
    from datetime import datetime, timezone
    now = int(datetime(2026, 10, 2, 4, tzinfo=timezone.utc).timestamp() * 1000)
    next_bithumb_4h = int(datetime(2026, 10, 2, 7, tzinfo=timezone.utc).timestamp() * 1000)
    next_upbit_4h = int(datetime(2026, 10, 2, 8, tzinfo=timezone.utc).timestamp() * 1000)
    next_bithumb_day = int(datetime(2026, 10, 2, 15, tzinfo=timezone.utc).timestamp() * 1000)
    assert CandleFeed.next_close_ms("4h", now, exchange="bithumb") == next_bithumb_4h
    assert CandleFeed.next_close_ms("4h", now, exchange="upbit") == next_upbit_4h
    assert CandleFeed.next_close_ms("1d", now, exchange="bithumb") == next_bithumb_day


def test_domestic_dca_three_second_ticks_do_not_repeat_daily_buy():
    from datetime import datetime, timedelta, timezone
    sim = make_sim(_macro(rule="C"))
    start = datetime(2026, 10, 2, tzinfo=timezone.utc)
    assert sim.step(100, start).side == "buy"
    assert sim.step(100, start + timedelta(seconds=3)) is None
    assert sim.step(100, start + timedelta(hours=23, minutes=59)) is None
    assert sim.step(100, start + timedelta(days=1)).side == "buy"
    assert sim.buys_done == 2


def test_domestic_dca_missing_timestamp_cannot_trigger_a_buy():
    sim = make_sim(_macro(rule="C"))
    assert sim.step(100) is None and sim.buys_done == 0


def test_domestic_dca_restore_preserves_each_legs_next_daily_buy():
    from datetime import datetime, timedelta, timezone
    from app.engine.driver import Leg, StrategyDriver
    macro = _macro(rule="C")
    start = datetime(2026, 10, 2, tzinfo=timezone.utc)
    first = StrategyDriver([Leg("KRW-BTC", make_sim(macro), 700_000)], 700_000, macro=macro)
    assert first.tick(100, start).side == "buy"
    snapshot = first.state()
    restored = StrategyDriver([Leg("KRW-BTC", make_sim(macro), 700_000)], 700_000, macro=macro)
    restored.restore(snapshot, leg_equity={}, total_equity=first.equity)
    assert restored.tick(100, start + timedelta(seconds=3)) is None
    assert restored.tick(100, start + timedelta(days=1)).side == "buy"


def test_domestic_dca_sparse_daily_backtest_uses_elapsed_days_not_row_count():
    from datetime import datetime, timedelta, timezone
    import pandas as pd
    from app.engine import run_backtest
    macro = _macro(rule="C")
    macro.params["interval_days"] = 2
    start = datetime(2026, 10, 2, tzinfo=timezone.utc)
    frame = pd.DataFrame({"timestamp": [start, start + timedelta(days=3), start + timedelta(days=6)],
                          "open": [100] * 3, "high": [100] * 3, "low": [100] * 3,
                          "close": [100] * 3, "volume": [1] * 3})
    assert run_backtest(macro, frame).total_trades == 3


def test_domestic_portfolio_dca_restores_independent_buy_schedules():
    from datetime import datetime, timedelta, timezone
    from app.engine.driver import Leg, StrategyDriver
    macro = _macro(rule="C")
    start = datetime(2026, 10, 2, tzinfo=timezone.utc)
    first = StrategyDriver([Leg("KRW-BTC", make_sim(macro), 700_000),
                            Leg("KRW-ETH", make_sim(macro.for_symbol("KRW-ETH")), 700_000)],
                           1_400_000, macro=macro)
    assert first.tick(100, start, "KRW-BTC").side == "buy"
    assert first.tick(100, start + timedelta(hours=12), "KRW-ETH").side == "buy"
    snapshot = first.state()
    restored = StrategyDriver([Leg("KRW-BTC", make_sim(macro), 700_000),
                               Leg("KRW-ETH", make_sim(macro.for_symbol("KRW-ETH")), 700_000)],
                              1_400_000, macro=macro)
    restored.restore(snapshot, leg_equity={leg.symbol: leg.equity for leg in first.legs}, total_equity=first.equity)
    assert restored.tick(100, start + timedelta(days=1), "KRW-BTC").side == "buy"
    assert restored.tick(100, start + timedelta(days=1), "KRW-ETH") is None


def test_domestic_dca_restore_does_not_restart_after_stop_loss():
    from datetime import datetime, timedelta, timezone
    from app.engine.driver import Leg, StrategyDriver
    macro = _macro(rule="C")
    macro.risk.stop_loss_pct = 5
    start = datetime(2026, 10, 2, tzinfo=timezone.utc)
    first = StrategyDriver([Leg("KRW-BTC", make_sim(macro), 700_000)], 700_000, macro=macro)
    assert first.tick(100, start).side == "buy"
    assert first.tick(90, start + timedelta(seconds=3)).side == "sell"
    restored = StrategyDriver([Leg("KRW-BTC", make_sim(macro), 700_000)], 700_000, macro=macro)
    restored.restore(first.state(), leg_equity={}, total_equity=first.equity)
    assert restored.tick(100, start + timedelta(days=2)) is None
    assert restored.sim.stopped is True
