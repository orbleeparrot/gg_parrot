"""Rule-based plain-language explanation of a backtest result (교육용 해설).

Pure + deterministic: the same (macro, result) always yields the same
explanation, so it ships inline with the backtest response for free and can be
cached trivially. It NEVER predicts the future or gives advice — it only narrates
what THIS past simulation did and draws one teaching point from it.

This is the always-on baseline AND the fallback for the optional AI layer: the AI
enrichment (see the ``/api/explain/ai`` follow-up) produces the SAME
``Explanation`` shape, so the frontend renders both identically and can degrade
to this module whenever the model is unavailable.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel

from .backtest import BacktestResult
from .schema import Macro, PositionSide, RuleType

# Mascot moods the frontend maps to a 껄무새 face + accent color. Kept as a small
# closed set so the UI never sees an unknown mood.
MOODS = ("idle", "liquidated", "crash", "loss", "lost_to_hold", "win", "big_win")

_DISCLAIMER = "과거 데이터 기반 시뮬레이션 해설이며, 투자 조언이 아닙니다."


class Explanation(BaseModel):
    """Structured, render-ready explanation (shared by the rule + AI layers)."""

    mood: str  # one of MOODS -> mascot face
    headline: str  # 껄무새 one-liner (the hook)
    points: List[str]  # grounded observations, each tied to a real number
    lesson: str  # one teaching takeaway
    source: str = "rule"  # "rule" | "ai"
    disclaimer: str = _DISCLAIMER


def _coin(symbol: str) -> str:
    s = symbol.upper()
    if s.startswith("KRW-"):
        return s[4:]
    for q in ("USDT", "BUSD", "USDC", "USD"):
        if s.endswith(q):
            return s[: -len(q)]
    return s


def _signed(x: float, digits: int = 1) -> str:
    return f"{x:+.{digits}f}%"


def _decide_mood(r: BacktestResult) -> str:
    if r.total_trades == 0:
        return "idle"
    if r.liquidation_count and r.liquidation_count > 0:
        return "liquidated"
    if r.final_return_pct <= -20:
        return "crash"
    if r.final_return_pct < 0:
        return "loss"
    # profit territory
    bh = r.buy_hold_return_pct
    if bh is not None and r.final_return_pct < bh - 1.0:
        return "lost_to_hold"
    if r.final_return_pct >= 30:
        return "big_win"
    return "win"


_HEADLINES = {
    "idle": "🦜 어라, 한 번도 안 샀네? 진입 조건이 너무 빡빡했나 봐.",
    "liquidated": "🦜 껄……. 청산당했어. 레버리지가 널 잡아먹었다.",
    "crash": "🦜 이건 좀 아팠겠다. 크게 물렸어.",
    "loss": "🦜 아쉽! 이번 판은 마이너스로 끝났어.",
    "lost_to_hold": "🦜 벌긴 벌었는데… 그냥 들고 있는 게 나았어",
    "win": "🦜 오, 좀 하는데? 플러스로 마감했어.",
    "big_win": "🦜 대박! 이번 기간엔 아주 잘 먹혔어.",
}


def _points(macro: Macro, r: BacktestResult) -> List[str]:
    coin = _coin(macro.symbol)
    pts: List[str] = []

    # 1) vs 그냥 홀딩(HODL) — the single most important framing.
    bh = r.buy_hold_return_pct
    if bh is not None:
        diff = r.final_return_pct - bh
        if diff >= 0:
            pts.append(
                f"이 전략은 {_signed(r.final_return_pct)}, 그냥 {coin} 들고 있었으면 "
                f"{_signed(bh)} — 홀딩보다 {abs(diff):.1f}%p 앞섰어."
            )
        else:
            pts.append(
                f"이 전략은 {_signed(r.final_return_pct)}인데 그냥 {coin} 들고 있었으면 "
                f"{_signed(bh)}였어 — 홀딩에 {abs(diff):.1f}%p 뒤졌어."
            )
    else:
        pts.append(f"이 기간 최종 수익률은 {_signed(r.final_return_pct)}야.")

    # 2) MDD — how deep did it dig before ending here.
    if r.total_trades > 0:
        depth = "꽤 깊은 낙폭이야" if r.mdd_pct >= 30 else "감당할 만한 수준"
        pts.append(f"가는 길에 최대 -{r.mdd_pct:.1f}%까지 빠졌어 (MDD) — {depth}.")

    # 3) win rate over N trades.
    if r.total_trades > 0:
        pts.append(f"총 {r.total_trades}번 매매해서 승률 {r.win_rate_pct:.0f}%.")

    # 4) streak risk.
    if (r.max_consecutive_losses or 0) >= 5:
        pts.append(
            f"최대 {r.max_consecutive_losses}번 연속으로 손절났어 — 실제였다면 "
            f"심리적으로 버티기 힘든 구간이야."
        )

    # 5) liquidation damage.
    if r.liquidation_count and r.liquidation_count > 0:
        pts.append(
            f"레버리지 {macro.leverage}배 때문에 기간 중 {r.liquidation_count}번 청산돼 "
            f"증거금을 통째로 날렸어."
        )

    # 6) sharpe read-out (only when meaningful).
    if r.sharpe is not None:
        if r.sharpe >= 1:
            pts.append(f"샤프지수 {r.sharpe:.2f} — 변동성 대비 수익이 준수했어.")
        elif r.sharpe < 0:
            pts.append(f"샤프지수 {r.sharpe:.2f} — 위험을 감안하면 손해 보는 장사였어.")

    # 필터는 '왜 안 샀나' 의 근거다 — 수익률 이야기 뒤에 붙인다.
    if macro.entry_filter is not None:
        from .entry_filter import make_filter
        pts.append(
            f"진입 조건을 하나 더 걸었어 — {make_filter(macro).note()}. "
            "조건이 아닐 때는 사지 않아."
        )

    if macro.bundle_risk is not None:
        from .bundle import BundleGate
        note = BundleGate(macro.bundle_risk, macro.initial_capital or 1.0).note()
        pts.append(f"묶음 한도를 걸어서 {note} 만 들어가. 한도에 닿으면 새로 안 사고, 들고 있는 건 그대로 팔아.")

    return pts


def _lesson(macro: Macro, r: BacktestResult, mood: str) -> str:
    if mood == "liquidated":
        return (
            "레버리지는 수익도 손실도 배로 키워. 한 번 청산되면 남는 게 없어 — "
            "배수를 낮추거나 손절 폭을 넓혀서 청산가와 거리를 둬 봐."
        )
    if mood == "idle":
        return (
            "진입 조건이 한 번도 안 맞았어. 조건을 조금 느슨하게 하거나 봉 단위·기간을 "
            "바꿔서 신호가 실제로 나오는지부터 확인해봐."
        )
    if mood == "lost_to_hold":
        return (
            "매매를 자주 할수록 수수료와 타이밍 손해가 쌓여. 가끔은 '안 파는 것'도 "
            "전략이야 — 홀딩을 못 이긴 이유를 매매 횟수에서 찾아봐."
        )
    if r.mdd_pct >= 30:
        return (
            "수익률만큼 중요한 게 '얼마나 깊게 빠지나'(MDD)야. 실제 계좌라면 이 낙폭을 "
            "버텨야 하니, 투입 비율을 줄이면 곡선이 훨씬 부드러워져."
        )
    if (r.max_consecutive_losses or 0) >= 5:
        return (
            "연속 손절이 길다는 건 규칙이 이 시장과 잘 안 맞는다는 신호일 수 있어. "
            "손절 폭이나 진입 조건 중 하나만 바꿔서 다시 돌려봐."
        )
    if mood in ("win", "big_win"):
        return (
            "잘 나왔지만 이건 '과거의 한 구간'일 뿐이야. 다른 기간·다른 종목에서도 "
            "통하는지 확인해봐 — 한 구간에만 맞춘 전략(과최적화)일 수 있어."
        )
    return (
        "손실도 데이터야. 지표(승률·MDD·손익비)를 보고 뭐가 안 맞았는지 짚은 뒤, "
        "한 번에 하나씩만 바꿔서 비교해봐."
    )


def explain_result(macro: Macro, result: BacktestResult) -> Explanation:
    """Deterministic, advice-free narration of a single backtest result."""
    mood = _decide_mood(result)
    return Explanation(
        mood=mood,
        headline=_HEADLINES[mood],
        points=_points(macro, result),
        lesson=_lesson(macro, result, mood),
        source="rule",
    )
