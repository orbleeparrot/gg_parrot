"""실행기가 **스스로** 내리는 매수 · 매도 판단.

지금까지는 서버가 봉을 받아 판단하고 명령만 내려보냈다. 그 구조에서는 우리 서버가 잠깐
멈추면 사용자는 자기 PC 가 멀쩡한데도 매수 · 매도 시점을 놓친다 — 원인이 전적으로 우리
쪽에 있고, 전 사용자에게 동시에, 사용자는 손쓸 수도 볼 수도 없다.

여기서 도는 것은 **서버와 같은 코드**다(``engine.live_session``). 드라이버를 세우는 법,
체결 의도를 주문 명령으로 바꾸는 비율, 되살릴 때 다시 판정할 봉 — 전부 공유 엔진의
함수를 그대로 부른다. 복사해 오면 두 쪽이 갈리고, 갈린 것은 체결이 난 뒤에야 보인다.

이 모듈은 **주문을 내지 않는다.** 명령 묶음을 만들어 줄 뿐이고, 그걸 거래소로 보내는 것은
``macro_runner`` 가 서버 명령을 실행할 때 쓰는 바로 그 길이다 — 길이 하나여야 주문 상한 ·
최소 수량 · 수수료 보정 · 포지션 불확실 판정이 서버 신호와 로컬 신호에 똑같이 걸린다.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable, List, Optional

try:  # 패키지 import(시험) / 직접 import(PyInstaller 빌드)
    from .shared_engine import bootstrap_import_path
except ImportError:
    from shared_engine import bootstrap_import_path

bootstrap_import_path()

from engine.intervals import INTERVAL_MS  # noqa: E402
from engine.live_session import (  # noqa: E402
    WARMUP_CANDLES,
    build_driver,
    command_from_fill,
    session_capital,
    unjudged_last_close,
)
from engine.live_levels import strategy_levels  # noqa: E402
from engine.schema import Macro  # noqa: E402

#: 로컬 명령의 id 앞에 붙는 말. 서버 명령 id(정수)와 절대 겹치면 안 된다 — 겹치면
#: ``_done_command_ids`` 가 서로의 명령을 '이미 했다' 고 읽어 주문이 통째로 사라진다.
LOCAL_ID_PREFIX = "local-"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _ts(candle) -> datetime:
    """엔진이 받는 시각 — 봉이 열린 시각(ms, UTC)을 datetime 으로."""
    return datetime.fromtimestamp(int(candle[0]) / 1000, timezone.utc)


class LocalStrategy:
    """매크로 하나를 들고 봉마다 판단한다. 거래소도 서버도 모른다.

    쓰는 순서::

        strategy = LocalStrategy(macro)
        strategy.warmup(history)            # 과거 봉으로 지표를 세운다
        strategy.push_candles(new_bars)     # 마감봉 — 무엇을 할지만 정한다
        commands = strategy.decide(price)   # 현재가 — 정해진 것을 주문 명령으로
    """

    #: 틱 한 번에 꺼낼 체결 의도의 상한. 그리드 칸 수보다 넉넉하다 — 이 수에 걸린다는 건
    #: 큐가 끝없이 나온다는 뜻이고, 그러면 주문이 아니라 로그가 필요한 상황이다.
    MAX_FILLS_PER_TICK = 50

    def __init__(self, macro: Macro, symbol: str = "", *,
                 now_ms: Callable[[], int] = _now_ms) -> None:
        self.macro = macro
        self.symbol = (symbol or macro.symbol).upper()
        self.interval = macro.candle_interval or "1d"
        self.capital = session_capital(macro)
        self.driver = build_driver(macro, self.symbol)
        self._now_ms = now_ms
        self._seq = 0
        #: 마지막으로 상태를 저장한 시각 — 되살릴 때 '다시 판정할 봉' 의 기준이다.
        self.checkpoint_ms = 0

    # --- 준비 -------------------------------------------------------
    @property
    def warmup_count(self) -> int:
        """지표를 세우는 데 필요한 과거 봉 수. 서버와 같아야 같은 판정이 난다."""
        return WARMUP_CANDLES

    def warmup(self, candles: List) -> None:
        """과거 마감봉으로 지표를 세운다. 장부는 비운 채로 끝난다(체결을 내지 않는다)."""
        self.driver.warmup({self.symbol: list(candles)})

    def restore(self, state: Optional[dict], *, replay_last: Optional[object] = None) -> None:
        """재기동 — 저장해 둔 전략 상태를 되살린다. 반드시 ``warmup`` 뒤에 부른다.

        ``replay_last`` 는 ``unjudged_candle`` 이 골라 준, 웜업에서 **빼 둔** 봉이다.
        그 봉 하나만 실시간 봉처럼 다시 흘려보낸다 — 의도가 서면 다음 틱에 체결된다.
        더 오래 끊겼더라도 묵은 봉의 신호로 한꺼번에 사고팔지 않게 가장 최근 하나만.
        """
        if state:
            self.driver.restore(state, leg_equity={}, total_equity=self.driver.initial)
            self.driver.trade_count = int(state.get("trade_count") or 0)
            self.checkpoint_ms = int(state.get("checkpoint_ms") or 0)
        if replay_last is not None:
            self.driver.push_candle(self.symbol, replay_last)

    def unjudged_candle(self, candles: List):
        """``candles`` 중 마지막 저장 뒤에 마감돼 **아직 판정 안 된** 봉(없으면 None).

        웜업에서 이 봉을 빼고, ``restore(replay_last=...)`` 로 다시 흘려보낸다. 빼지 않으면
        그 봉의 진입 · 청산 의도가 조용히 사라진다.
        """
        return unjudged_last_close(candles, self.checkpoint_ms, INTERVAL_MS.get(self.interval) or 0)

    # --- 판단 -------------------------------------------------------
    def _commands(self, fills) -> List[dict]:
        out = []
        for fill in fills:
            self._seq += 1
            cmd = command_from_fill(fill, self.capital)
            cmd["id"] = f"{LOCAL_ID_PREFIX}{self._seq}"
            cmd["seq"] = self._seq
            cmd["source"] = "local"
            out.append(cmd)
        if out:
            self.checkpoint_ms = self._now_ms()
        return out

    def push_candles(self, candles: List) -> int:
        """마감봉들을 순서대로 넣는다. 돌려주는 값은 **대기 중인 체결 의도 수**.

        여기서는 주문이 나오지 않는다 — 봉은 '무엇을 할지' 만 정하고, 실제 체결은 다음
        ``decide(price)`` 가 가져간다. 백테스트의 "마감에 판정하고 다음 시가에 체결" 과
        같은 모양이고, 서버 드라이버도 정확히 이렇게 돈다.
        """
        queued = 0
        for candle in candles:
            queued += int(self.driver.push_candle(self.symbol, candle) or 0)
        return queued

    def decide(self, price: float) -> List[dict]:
        """현재가 한 틱 — 대기 중인 체결 의도를 **전부** 꺼내 주문 명령으로 바꾼다.

        드라이버의 ``tick`` 은 한 번에 체결 하나만 준다(서버는 3초마다 한 개씩 가져간다).
        실행기는 그 자리에서 다 비운다 — 그리드처럼 한 봉에 여러 칸이 걸리면 한 개씩
        5초를 기다릴 이유가 없고, 기다리는 동안 가격이 달아난다. 순서는 그대로다.
        """
        if not price or price <= 0:
            return []
        now = datetime.fromtimestamp(self._now_ms() / 1000, timezone.utc)
        fills = []
        while True:
            fill = self.driver.tick(float(price), now, self.symbol)
            if fill is None:
                break
            fills.append(fill)
            if len(fills) >= self.MAX_FILLS_PER_TICK:
                # 큐가 끝없이 나오면 뭔가 잘못된 것이다 — 남은 것은 다음 틱에 가져간다.
                break
        return self._commands(fills)

    # --- 바깥에 보여 줄 것 -------------------------------------------
    def state(self) -> dict:
        """서버에 올려 둘 전략 상태. 재기동 때 ``restore`` 가 그대로 받는다."""
        state = self.driver.state()
        state["checkpoint_ms"] = self.checkpoint_ms or self._now_ms()
        state["trade_count"] = int(getattr(self.driver, "trade_count", 0) or 0)
        return state

    def levels(self, entry_price: float) -> dict:
        """화면에 그릴 가격선(진입 목표 · 트레일 발동 · 트레일 손절).

        서버가 쓰는 함수 그대로다 — 양쪽이 각자 계산하면 실행기 창과 웹 화면에 다른 선이
        뜨고, 둘 중 어느 쪽이 실제 판단인지 아무도 모르게 된다.
        """
        return strategy_levels(self.macro, self.state(), float(entry_price or 0.0))


def is_local_command(cmd: dict) -> bool:
    """이 명령이 실행기 자신이 낸 것인가 — 서버에 ack 를 보내면 안 된다(서버가 모르는 id)."""
    return str((cmd or {}).get("id") or "").startswith(LOCAL_ID_PREFIX)
