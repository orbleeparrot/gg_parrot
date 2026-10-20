"""실행기가 서버와 **같은 코드**로 판단하기 위한 다리.

매매 판단이 서버와 실행기 두 곳에 각각 적혀 있으면 백테스트와 실거래가 조용히 갈라진다.
그래서 판단 코드는 저장소에 한 벌(``backend/engine/``)만 두고 양쪽이 그것을 가져다 쓴다.

사는 길이 둘이다:

* **소스 실행** — ``runner/`` 와 ``backend/`` 가 나란히 있다. ``backend/`` 를 import 경로에 올린다.
* **PyInstaller 번들** — ``engine`` 이 이미 묶여 들어가 있다(빌드할 때 ``--paths ../backend``).
  번들에는 ``backend/`` 폴더가 없으므로 경로를 건드리지 않는다.

바깥 의존성은 ``pydantic`` 하나다. 그 경계는
``backend/tests/test_engine_package_boundary.py`` 가 지킨다 — 엔진에 pandas 나 서버 코드가
흘러들면 거기서 깨진다.
"""
from __future__ import annotations

import sys
from pathlib import Path

#: 소스에서 돌 때 엔진이 있는 자리. 번들에서는 쓰이지 않는다.
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"


def bootstrap_import_path() -> bool:
    """소스 실행이면 ``backend/`` 를 ``sys.path`` 에 올린다. 올렸으면 True.

    번들(``sys.frozen``)이거나 엔진이 거기 없으면 아무것도 하지 않는다 — 번들에서
    경로를 끼워 넣으면 묶인 모듈 대신 엉뚱한 파일을 읽을 수 있다.
    """
    if getattr(sys, "frozen", False):
        return False
    if not (BACKEND_DIR / "engine" / "__init__.py").is_file():
        return False
    path = str(BACKEND_DIR)
    if path not in sys.path:
        sys.path.insert(0, path)
    return True


bootstrap_import_path()

# 실거래 경로. 여기 없는 것(backtest · walkforward · portfolio)은 서버 전용이고
# pandas 를 쓰므로 실행기가 가져오면 번들이 수십 MB 불어난다.
from engine.schema import Macro, RuleType  # noqa: E402
from engine.candles import make_candle_sim  # noqa: E402
from engine.driver import Leg, StrategyDriver, sim_state, sim_strategy_state  # noqa: E402
from engine.live_levels import strategy_levels  # noqa: E402
from engine.stepper import Fill  # noqa: E402
from engine.version import ENGINE_VERSION  # noqa: E402

__all__ = [
    "BACKEND_DIR",
    "ENGINE_VERSION",
    "bootstrap_import_path",
    "Fill",
    "Leg",
    "Macro",
    "RuleType",
    "StrategyDriver",
    "make_candle_sim",
    "sim_state",
    "sim_strategy_state",
    "strategy_levels",
]
