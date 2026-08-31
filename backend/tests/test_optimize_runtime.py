"""CPU-isolated optimize runtime: preparation fingerprint, cache, and capacity."""
from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass
import importlib
import importlib.util
import threading

import pandas as pd
import pytest

from app import optimize as optimize_mod
from app.engine.schema import Fees, Macro, Period, Risk


NO_COST = Fees(commission_pct=0.0, slippage_pct=0.0, funding_pct=0.0)


def _macro() -> Macro:
    return Macro(
        symbol="BTCUSDT",
        rule_type="A",
        position_side="long",
        candle_interval="1h",
        params={"take_profit_pct": 5.0, "initial_capital": 1000.0},
        risk=Risk(invest_ratio=1.0, stop_loss_pct=3.0),
        period=Period(preset="3m"),
        fees=NO_COST,
    )


def _df(last_close: float = 100.0, bars: int = 100) -> pd.DataFrame:
    closes = [100.0] * bars
    closes[-1] = last_close
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=bars, freq="h", tz="UTC"),
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [1.0] * bars,
        }
    )


def _runtime():
    assert importlib.util.find_spec("app.optimize_runtime") is not None
    return importlib.import_module("app.optimize_runtime")


def test_prepare_cache_key_changes_when_candle_data_changes(monkeypatch):
    assert hasattr(optimize_mod, "prepare_optimization")
    frames = iter((_df(100.0), _df(101.0)))
    monkeypatch.setattr(
        optimize_mod,
        "fetch_klines_for_macro",
        lambda *args, **kwargs: (next(frames), "cache"),
    )

    first = optimize_mod.prepare_optimization(_macro(), [5, 3, 3], [4, 2])
    second = optimize_mod.prepare_optimization(_macro(), [5, 3, 3], [4, 2])

    assert first.cache_key != second.cache_key


def test_prepare_cache_key_uses_cleaned_axes(monkeypatch):
    assert hasattr(optimize_mod, "prepare_optimization")
    monkeypatch.setattr(
        optimize_mod,
        "fetch_klines_for_macro",
        lambda *args, **kwargs: (_df(), "cache"),
    )

    first = optimize_mod.prepare_optimization(_macro(), [5, 3, 3], [4, 2, 2])
    second = optimize_mod.prepare_optimization(_macro(), [3, 5], [2, 4])

    assert first.cache_key == second.cache_key


def test_prepare_rejects_data_above_optimize_bar_budget(monkeypatch):
    assert hasattr(optimize_mod, "prepare_optimization")
    monkeypatch.setattr(optimize_mod, "OPTIMIZE_MAX_BARS", 50, raising=False)
    monkeypatch.setattr(
        optimize_mod,
        "fetch_klines_for_macro",
        lambda *args, **kwargs: (_df(bars=51), "cache"),
    )

    with pytest.raises(ValueError, match="50개"):
        optimize_mod.prepare_optimization(_macro(), [3], [2])


@dataclass(frozen=True)
class _Prepared:
    cache_key: str


class _ImmediateExecutor:
    def __init__(self):
        self.submits = 0
        self.shutdowns = 0

    def submit(self, fn, value):
        self.submits += 1
        future = Future()
        try:
            future.set_result(fn(value))
        except Exception as exc:  # pragma: no cover - mirrors Future behavior
            future.set_exception(exc)
        return future

    def shutdown(self, **kwargs):
        self.shutdowns += 1


class _BlockingExecutor:
    def __init__(self):
        self.submits = 0
        self.future = Future()
        self.submitted = threading.Event()

    def submit(self, _fn, _value):
        self.submits += 1
        self.submitted.set()
        return self.future

    def shutdown(self, **kwargs):
        return None


def test_completed_result_is_cached_until_ttl():
    runtime = _runtime()
    clock = [100.0]
    executor = _ImmediateExecutor()
    coordinator = runtime.OptimizeCoordinator(
        executor=executor,
        runner=lambda prepared: {"key": prepared.cache_key},
        cache_ttl_seconds=10,
        clock=lambda: clock[0],
    )

    assert coordinator.run(_Prepared("same")) == {"key": "same"}
    assert coordinator.run(_Prepared("same")) == {"key": "same"}
    assert executor.submits == 1

    clock[0] = 111.0
    assert coordinator.run(_Prepared("same")) == {"key": "same"}
    assert executor.submits == 2


def test_identical_inflight_requests_share_one_future():
    runtime = _runtime()
    executor = _BlockingExecutor()
    coordinator = runtime.OptimizeCoordinator(
        executor=executor,
        runner=lambda prepared: {"key": prepared.cache_key},
        result_timeout_seconds=1,
    )
    results: list[dict] = []

    first = threading.Thread(target=lambda: results.append(coordinator.run(_Prepared("same"))))
    second = threading.Thread(target=lambda: results.append(coordinator.run(_Prepared("same"))))
    first.start()
    assert executor.submitted.wait(timeout=1)
    second.start()
    executor.future.set_result({"key": "same"})
    first.join(timeout=1)
    second.join(timeout=1)

    assert executor.submits == 1
    assert results == [{"key": "same"}, {"key": "same"}]


def test_distinct_request_is_rejected_while_capacity_is_full():
    runtime = _runtime()
    executor = _BlockingExecutor()
    coordinator = runtime.OptimizeCoordinator(
        executor=executor,
        runner=lambda prepared: {"key": prepared.cache_key},
        max_in_flight=1,
        result_timeout_seconds=1,
    )
    first = threading.Thread(target=lambda: coordinator.run(_Prepared("first")))
    first.start()
    assert executor.submitted.wait(timeout=1)

    with pytest.raises(runtime.OptimizeBusyError):
        coordinator.run(_Prepared("second"))

    executor.future.set_result({"key": "first"})
    first.join(timeout=1)
    assert executor.submits == 1


def test_shutdown_closes_an_injected_executor():
    runtime = _runtime()
    executor = _ImmediateExecutor()
    coordinator = runtime.OptimizeCoordinator(
        executor=executor,
        runner=lambda prepared: {"key": prepared.cache_key},
    )

    coordinator.shutdown()

    assert executor.shutdowns == 1
