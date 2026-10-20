"""실거래 한 세션 — 드라이버를 세우고, 체결 의도를 주문 명령으로 바꾼다.

서버와 실행기가 **같은 함수**를 쓴다. 양쪽이 각자 자본을 정하거나 각자 비율을 계산하면,
같은 신호가 서버에서는 전액 · 실행기에서는 절반으로 나가는 식으로 갈린다. 그 차이는
체결이 난 뒤에야 보이고, 그때는 이미 돈이 움직인 뒤다.

여기 있는 것은 산수뿐이다 — DB 도, 네트워크도, 거래소도 모른다. 봉을 어디서 받고 주문을
어떻게 내는지는 부르는 쪽(서버 ``runner_engine`` · 실행기 ``local_engine``)의 일이다.
"""
from __future__ import annotations

from typing import Optional

from .driver import Leg, StrategyDriver
from .schema import Macro
from .stepper import EXIT_SIDES, Fill, make_sim

#: 지표를 세우는 데 쓰는 과거 봉 수. 서버와 실행기가 같아야 한다 — 다르면 이동평균 ·
#: RSI 의 초기값이 달라져 같은 봉에서 다른 판정이 난다.
WARMUP_CANDLES = 500

#: 매크로에 초기자본이 없을 때 쓰는 값. 실행기의 기본 주문 한도와 같다.
FALLBACK_CAPITAL = 100.0


def session_capital(macro: Macro) -> float:
    """이 세션이 굴리는 자본. 진입 금액 비율의 **분모**라 양쪽이 같아야 한다."""
    return float(macro.initial_capital or FALLBACK_CAPITAL)


def build_driver(macro: Macro, symbol: str = "") -> StrategyDriver:
    """실거래 세션용 드라이버 — 종목 하나, 레그 하나.

    ``enable_fill_snapshots`` 를 켠다: 진입 의도가 섰는데 실제 주문이 안 나가면 체결 직전
    상태로 되돌려야 한다(안 되돌리면 장부에만 포지션이 생긴다).
    """
    initial = session_capital(macro)
    sym = (symbol or macro.symbol).upper()
    driver = StrategyDriver([Leg(sym, make_sim(macro, initial_capital=initial), initial)],
                            initial, macro=macro)
    driver.enable_fill_snapshots()
    return driver


def command_from_fill(fill: Fill, initial: float) -> dict:
    """체결 의도 하나를 주문 명령으로.

    진입은 **초기자본 대비 금액 비율**, 청산은 **직전 보유 수량 대비 비율**(모르면 전량).
    서로 기준이 다른 이유: 진입 때는 실제 보유가 없고, 청산 때는 실계좌 수량이 장부와
    조금씩 다를 수 있어 비율로 말해야 실행기가 자기 수량으로 환산할 수 있다.
    """
    if fill.side in EXIT_SIDES:
        frac = 1.0 if fill.qty_before <= 0 else min(1.0, fill.qty / fill.qty_before)
        if frac >= 0.999:
            frac = 1.0
        return {"action": fill.side, "notional_frac": 0.0, "qty_frac": float(frac),
                "signal_price": float(fill.price), "reason": fill.reason or "청산"}
    notional = fill.qty * fill.price
    return {"action": fill.side,
            "notional_frac": float(notional / initial) if initial > 0 else 0.0,
            "qty_frac": 0.0,
            "signal_price": float(fill.price), "reason": fill.reason or "진입"}


def unjudged_last_close(candles, checkpoint_ms: Optional[int], step_ms: int):
    """되살릴 때 **실시간으로 다시 판정할** 봉 — 없으면 None.

    마지막으로 상태를 저장한 뒤에 마감된 봉이 있으면, 앞 프로세스는 그 봉을 판정하지
    못했을 수 있다. 웜업에 넣어 버리면 그 봉의 진입 · 청산 의도가 조용히 사라진다
    (2026-10-18 세션 297: 19:00 마감 직후 배포로 골든크로스 진입을 놓쳤다).

    기준은 봉이 **닫힌** 시각(``t + step``)이지 열린 시각이 아니다 — 열린 시각으로 재면
    저장 직전에 열려 저장 직후에 닫힌 봉을 놓친다.

    체결이 나면 그 자리에서 저장하므로(저장 시각 > 마감 시각), 판정해서 체결까지 간 봉은
    여기 걸리지 않는다. 판정만 하고 체결이 없던 봉이 걸리면 같은 봉으로 같은 결정을 다시
    내릴 뿐이다. 처음 시작(저장 없음)이면 None — 시작하자마자 지난 봉의 신호로 사지 않는다.
    """
    if not checkpoint_ms or not candles or not step_ms:
        return None
    last = candles[-1]
    return last if int(last[0]) + int(step_ms) > int(checkpoint_ms) else None
