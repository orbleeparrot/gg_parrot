"""매크로 엔진 — 서버와 실행기가 **같은 코드**로 판단하기 위한 공유 패키지.

``app`` 을 거꾸로 참조하지 않는다. 그래야 실행기(PyInstaller 번들)가 이 패키지만
가져다 쓸 수 있다. 바깥 의존성은 실거래 경로 전체에서 ``pydantic`` 하나뿐이다.

실거래 경로:  schema · exchanges · leverage · stepper · candles · driver ·
              live_levels · entry_filter · bundle
서버 전용:    backtest · walkforward · portfolio* · explain · summary · validation
              (pandas · numpy 를 쓴다)

아래 이름들을 모듈 맨 위에서 import 하지 않고 ``__getattr__`` 로 미루는 이유가
바로 그 경계다 — ``import engine.driver`` 한 번에 pandas 가 딸려 오면 실행기
번들에 수십 MB 가 붙고, PyInstaller 가 필요 없는 서버 코드까지 끌어안는다.
"""
from .schema import (
    Fees,
    Macro,
    Period,
    PositionSide,
    Risk,
    RuleType,
)

# 이름 → 실제로 담긴 모듈. 처음 꺼낼 때 그 모듈만 읽는다.
_LAZY = {
    "BacktestResult": "backtest",
    "compact_backtest_result": "backtest",
    "run_backtest": "backtest",
    "human_summary": "summary",
    "Explanation": "explain",
    "explain_result": "explain",
}

__all__ = [
    "Fees",
    "Macro",
    "Period",
    "PositionSide",
    "Risk",
    "RuleType",
    *_LAZY,
]


def __getattr__(name: str):
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(f".{module_name}", __name__), name)
    globals()[name] = value  # 두 번째부터는 __getattr__ 을 거치지 않는다
    return value


def __dir__():
    return sorted(__all__)
