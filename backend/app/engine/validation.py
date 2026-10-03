"""백테스트 결과를 다시 읽어 내는 검증 지표 — 엔진을 부르지 않는 순수 함수.

``equity_curve`` 하나로 월별 수익 · 집중도 · 낙폭 구간 · 소르티노 · 칼마를 낸다.
거래가 없어 곡선이 비거나 짧으면 지표 대신 None 을 돌려준다(0 으로 나누지 않는다).

같은 곡선과 구간별 수익 · 거래 수로 과최적화 경고 코드(``warnings``)도 낸다. 경고는
서버의 결정론적 규칙이며, 재야 할 값이 없으면(구간 수익이 비었거나 None) 경고하지 않는다.
"""
from __future__ import annotations

import math
from datetime import datetime

_DAY_SECONDS = 86_400.0


def _points(curve) -> list[tuple[datetime, float]]:
    out = []
    for point in curve or []:
        try:
            stamp = datetime.fromisoformat(str(point.t).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        out.append((stamp, float(point.equity)))
    return out


def monthly_returns(curve) -> list[dict]:
    """달력 월별 수익률(%). 각 달의 마지막 값끼리 비교한다."""
    points = _points(curve)
    if len(points) < 2:
        return []
    last_of_month: dict[str, float] = {}
    for stamp, equity in points:
        last_of_month[stamp.strftime("%Y-%m")] = equity
    months = sorted(last_of_month)
    base = points[0][1]
    rows = []
    for month in months:
        end = last_of_month[month]
        rows.append({"month": month,
                     "pct": round((end / base - 1.0) * 100.0, 2) if base > 0 else 0.0})
        base = end
    return rows


def concentration(curve) -> dict:
    """가장 많이 번 달이 전체 '번 돈' 에서 차지하는 몫.

    달마다 자산이 늘어난 금액(원 · 달러)을 구해, 가장 큰 달의 증가분을 증가한
    달들의 증가분 합으로 나눈다. 달별 수익률(%)끼리 나누면 자산이 커진 뒤의 달이
    작게 잡혀 몫이 왜곡되므로 퍼센트가 아니라 금액으로 센다.
    """
    points = _points(curve)
    if len(points) < 2:
        return {"top_month_share_pct": None, "months": 0}
    last_of_month: dict[str, float] = {}
    for stamp, equity in points:
        last_of_month[stamp.strftime("%Y-%m")] = equity
    months = sorted(last_of_month)
    base = points[0][1]
    gains = []
    for month in months:
        end = last_of_month[month]
        if end > base:
            gains.append(end - base)
        base = end
    if not gains:
        return {"top_month_share_pct": None, "months": len(months)}
    return {"top_month_share_pct": round(max(gains) / sum(gains) * 100.0, 2),
            "months": len(months)}


def drawdown_window(curve) -> dict | None:
    """가장 깊었던 낙폭의 시작 · 바닥 · 회복 시점."""
    points = _points(curve)
    if len(points) < 2:
        return None
    peak_stamp, peak = points[0]
    best = None
    for stamp, equity in points:
        if equity > peak:
            peak_stamp, peak = stamp, equity
            continue
        depth = (peak - equity) / peak * 100.0 if peak > 0 else 0.0
        if best is None or depth > best["depth_pct"]:
            best = {"start": peak_stamp, "trough": stamp, "depth_pct": round(depth, 2),
                    "peak": peak}
    if best is None or best["depth_pct"] <= 0:
        return None
    recovered = None
    for stamp, equity in points:
        if stamp > best["trough"] and equity >= best["peak"]:
            recovered = stamp
            break
    days = None if recovered is None else round(
        (recovered - best["trough"]).total_seconds() / _DAY_SECONDS)
    return {"start": best["start"].isoformat(), "trough": best["trough"].isoformat(),
            "recovered": None if recovered is None else recovered.isoformat(),
            "depth_pct": best["depth_pct"], "recovery_days": days}


def _returns(points) -> list[float]:
    out = []
    for (_, before), (_, after) in zip(points, points[1:]):
        if before > 0:
            out.append(after / before - 1.0)
    return out


def sortino(curve) -> float | None:
    """하방 변동만으로 나눈 위험조정 수익 — 샤프보다 손실에 민감하다."""
    points = _points(curve)
    rets = _returns(points)
    if len(rets) < 2:
        return None
    downside = [r for r in rets if r < 0]
    if not downside:
        return None
    deviation = math.sqrt(sum(r * r for r in downside) / len(downside))
    if deviation <= 0:
        return None
    return round((sum(rets) / len(rets)) / deviation * math.sqrt(len(rets)), 2)


# 연환산은 최소 이만큼의 기간이 있어야 의미가 있다. 몇 시간짜리 구간을 1년으로
# 늘리면 1.2 배 수익이 1.2 의 8760 제곱이 되어 값이 터지거나 무의미해진다.
_MIN_CALMAR_DAYS = 30.0


def calmar(curve, mdd_pct: float) -> float | None:
    """연수익 / 최대낙폭. 전문가가 먼저 보는 값이다.

    어떤 곡선이 와도 예외를 내지 않는다. 낙폭이 없거나, 끝 자산이 0 이하이거나,
    기간이 너무 짧거나, 결과가 유한한 수가 아니면 None 이다.
    """
    points = _points(curve)
    if len(points) < 2 or mdd_pct is None:
        return None
    try:
        mdd = float(mdd_pct)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(mdd) or mdd <= 0:
        return None
    start, end = points[0][1], points[-1][1]
    if start <= 0 or end <= 0:
        return None
    days = (points[-1][0] - points[0][0]).total_seconds() / _DAY_SECONDS
    if days < _MIN_CALMAR_DAYS:
        return None
    try:
        annual = ((end / start) ** (365.0 / days) - 1.0) * 100.0
        result = annual / mdd
    except (OverflowError, ZeroDivisionError):
        return None
    if not math.isfinite(result):
        return None
    return round(result, 2)


# --- 과최적화 경고 -----------------------------------------------------------
# 임계값은 합성 곡선(tests/test_validation_warnings.py)의 경계값 시험으로 고정돼
# 있다. 바꾸면 그 시험이 먼저 깨진다.
TOP_MONTH_SHARE_LIMIT = 70.0   # 한 달이 번 돈의 이만큼 이상이면 집중
# 집중 판정에는 달 수와 기간이 모두 필요하다. 달이 둘뿐이면 70/30 으로만 갈려도
# 몫이 70% 를 넘어 신호에 정보가 없고, 달 수는 '걸친 달력 달' 이라 월말을 낀 며칠짜리
# 구간도 2 개월이 된다. 가장자리 달은 번 돈이 거의 없는 조각이라 45~89일 구간은
# 실질 구간이 둘뿐일 수 있다(성장이 둔해지기만 해도 70% 를 넘는다). 90일이면 온전한
# 두 달 + 가장자리가 들어 최대 달이 조각이 아닌 진짜 구간과 겨룬다. 그래서 3 개월
# 이상 + 90 일 이상일 때만 센다.
# 비용: 90일 미만 백테스트에는 한_구간_집중 이 뜨지 않는다. 얇은 표본은 표본_부족 이
# 직접 잡고, 헛경고로 네 경고 모두를 무시하게 되는 쪽보다 덜 경고하는 쪽이 낫다.
# 가정: 곡선이 촘촘하다(엔진은 캔들마다 한 점). 며칠에 한 점뿐인 성긴 곡선은 대상이
# 아니다.
MIN_MONTHS_FOR_CONCENTRATION = 3
MIN_SPAN_DAYS_FOR_CONCENTRATION = 90.0
MIN_TRADES = 10                # 이보다 적으면 통계로 못 쓴다
TOP_TRADE_SHARE_LIMIT = 50.0   # 상위 몇 거래가 수익의 절반 이상이면 운에 가깝다

WARNING_CODES = ("한_구간_집중", "표본_부족", "후반부_음수", "거래_집중")


def _span_days(curve) -> float:
    """곡선의 처음과 끝 시각 사이 일수. 점이 2 개 미만이거나 잴 수 없으면 0.

    시각 표기가 섞여(Z 있음 · 없음) 비교할 수 없으면 못 잰 것으로 보고 0 을 낸다.
    """
    stamps = [stamp for stamp, _ in _points(curve)]
    if len(stamps) < 2:
        return 0.0
    try:
        return (max(stamps) - min(stamps)).total_seconds() / _DAY_SECONDS
    except TypeError:
        return 0.0


def _last_window(window_returns) -> float | None:
    """마지막 구간 수익(%). 구간이 둘 미만이거나 값이 숫자가 아니면 None(못 잼)."""
    if not window_returns or len(window_returns) < 2:
        return None
    try:
        last = float(window_returns[-1])
    except (TypeError, ValueError):
        return None
    return last if math.isfinite(last) else None


def warnings(*, curve, total_trades: int, window_returns: list[float],
             top_trade_share_pct: float | None) -> list[str]:
    """켜진 경고 코드들. 순서는 WARNING_CODES 와 같다.

    입력이 없는 항목은 경고를 만들지 않는다 — 모르는 것과 나쁜 것은 다르다.
    짧은 구간은 집중 경고를 내지 않는다(몫이 달 수에 따라 거의 정해져 버린다).
    그런 구간은 거래 수로 따로 켜지는 표본 부족이 맡는다.
    """
    found = []
    spread = concentration(curve)
    share = spread["top_month_share_pct"]
    if (share is not None and spread["months"] >= MIN_MONTHS_FOR_CONCENTRATION
            and _span_days(curve) >= MIN_SPAN_DAYS_FOR_CONCENTRATION
            and share >= TOP_MONTH_SHARE_LIMIT):
        found.append("한_구간_집중")
    if int(total_trades or 0) < MIN_TRADES:
        found.append("표본_부족")
    last = _last_window(window_returns)
    if last is not None and last < 0:
        found.append("후반부_음수")
    if top_trade_share_pct is not None and float(top_trade_share_pct) >= TOP_TRADE_SHARE_LIMIT:
        found.append("거래_집중")
    return [code for code in WARNING_CODES if code in found]
