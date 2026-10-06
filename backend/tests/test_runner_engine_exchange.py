import asyncio
from unittest.mock import Mock

from app import runner_engine


def _live(symbol, exchange):
    driver = Mock()
    driver.symbol = symbol
    driver.tick.return_value = None
    driver.state.return_value = {}
    live = runner_engine._Live(session_id=1, driver=driver, exchange=exchange)
    live.unsaved = []
    return live


def _run(live, monkeypatch):
    seen = {}

    def fake_price(symbol, ttl=2.0, *, exchange="binance"):
        seen["symbol"], seen["exchange"] = symbol, exchange
        return 100_000_000.0

    monkeypatch.setattr(runner_engine, "get_ticker_price_cached", fake_price)
    asyncio.run(runner_engine._tick_once(live))
    return seen


def test_signal_price_is_fetched_from_the_macro_exchange(monkeypatch):
    seen = _run(_live("KRW-BTC", "upbit"), monkeypatch)
    assert seen == {"symbol": "KRW-BTC", "exchange": "upbit"}


def test_binance_sessions_still_ask_binance(monkeypatch):
    seen = _run(_live("BTCUSDT", "binance"), monkeypatch)
    assert seen == {"symbol": "BTCUSDT", "exchange": "binance"}
