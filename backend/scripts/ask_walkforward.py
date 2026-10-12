"""껄무새 성향 재설계 — 워크포워드 측정기 (2026-10-12).

왜: 당일 한 장면에 숫자를 맞추면 그 장면에만 맞는 숫자가 나온다. 최근 몇 주를 1주씩
굴려서, 고른 설정이 다른 주에서도 버티는지 본다. 특히 "균형형과 공격형을 무엇으로
가를 것인가"(사양 §1-5)는 하루치로 고르면 안 된다.

구조: **백테스트는 한 번만 돌린다.** 성향·설정은 select_top 안에서만 갈리므로(순수 함수)
후보 결과를 캐시해 두고 설정 격자를 그 위에 덮어쓴다. 그래서 설정을 늘려도 비용이 안 든다.

돌리는 법 (backend 디렉터리에서):
    ./.venv/Scripts/python.exe -I scripts/ask_walkforward.py --weeks 8
    ./.venv/Scripts/python.exe -I scripts/ask_walkforward.py --weeks 1   # 빨리 확인만
"""
from __future__ import annotations

import argparse
import math
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 윈도 콘솔 기본 인코딩(cp949)이 한국어 표의 일부 글자를 못 찍는다. 측정 결과를
# 파일로 받아 노트에 옮기므로 출력은 늘 UTF-8 로 둔다.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app import ask, ask_candidates  # noqa: E402
from app.data import krw  # noqa: E402
from app.data.binance import PERIOD_PRESET_DAYS  # noqa: E402
from app.engine.backtest import BacktestResult, run_backtest  # noqa: E402
from app.marketdata import fetch_klines_for_macro  # noqa: E402

EXCHANGE = "upbit"
WATCH = "sometimes"          # 4시간봉 — 사양 §1-1 의 성적표와 같은 자
HORIZONS = ("days", "weeks", "months")
PROFILES = ("stable", "balanced", "aggressive")
SYMBOLS_PER_WINDOW = 3
WEEK_MS = 7 * 86_400_000

_BASE_CAPS = {profile: dict(table) for profile, table in ask.MDD_CAP_BY_PERIOD.items()}
_BASE_SCORE = ask.score


def _score_with(kind: str):
    """균형형 점수만 바꿔 끼운다 — 안정형·공격형은 사양이 '그대로' 로 정했다."""
    def scored(profile: str, result: BacktestResult) -> float:
        ret = ask.excess_over_hold(result)
        mdd = float(result.mdd_pct)
        if profile == "stable":
            return ret / max(mdd, 1.0)
        if profile == "balanced":
            if kind == "minus_half":
                return ret - 0.5 * mdd
            if kind == "minus_one_half":
                return ret - 1.5 * mdd
            if kind == "sharpe":
                # 샤프가 없는 결과(곡선이 평평하거나 너무 짧다)는 맨 뒤로. None 이 정렬에
                # 섞이면 비교가 터진다.
                return float(result.sharpe) if result.sharpe is not None else -math.inf
            return ret / math.sqrt(max(mdd, 1.0))   # "over_sqrt"
        return ret
    return scored


@dataclass(frozen=True)
class Config:
    """설정 후보 — 상한 표 배수와 균형형 점수 함수."""
    name: str
    cap_scale: float
    balanced_score: str

    def apply(self) -> None:
        ask.MDD_CAP_BY_PERIOD = {
            profile: {period: value * self.cap_scale for period, value in table.items()}
            for profile, table in _BASE_CAPS.items()
        }
        ask.score = _score_with(self.balanced_score)


CONFIGS = [
    Config("표 그대로 · 수익/√MDD", 1.0, "over_sqrt"),
    Config("표 그대로 · 수익-0.5×MDD", 1.0, "minus_half"),
    Config("표 그대로 · 수익-1.5×MDD", 1.0, "minus_one_half"),
    Config("표 그대로 · 샤프", 1.0, "sharpe"),
    Config("상한 1.25배 · 수익/√MDD", 1.25, "over_sqrt"),
    Config("상한 0.8배 · 수익/√MDD", 0.8, "over_sqrt"),
]


def _symbols() -> list[str]:
    """종목 풀 — 지금 티커로 만든다.

    한계: 거래소 티커는 과거 시점의 24시간 통계를 주지 않으므로 창마다 같은 종목을 본다.
    창이 가르는 것은 **시세 구간**이고 종목 선택은 아니다. 노트에 이 한계를 적는다.
    """
    tickers = krw.get_all_tickers(EXCHANGE)
    pool = ask_candidates.build_pool(tickers, profile="balanced", exchange=EXCHANGE)
    return [c["symbol"] for c in pool[:SYMBOLS_PER_WINDOW]]


def _run_candidates(symbol: str, horizon: str, end_ms: int) -> list[ask.Evaluated]:
    """이 종목 · 이 호흡 · 이 끝시각에서 모든 템플릿 후보를 한 번씩 돌린다."""
    period = ask.to_period(horizon)
    interval = ask.to_interval(WATCH, period)
    plan = ask._Plan(risk_profile="balanced", market="spot", leverage=1, symbols=[symbol],
                     period_preset=period, interval=interval, exchange=EXCHANGE,
                     account_balance=1_000_000.0)
    start_ms = end_ms - PERIOD_PRESET_DAYS[period] * 86_400_000
    out: list[ask.Evaluated] = []
    frames: dict[str, object] = {}
    for candidate in ask.build_templates(plan):
        key = candidate.macro.candle_interval
        if key not in frames:
            try:
                frames[key], _source = fetch_klines_for_macro(candidate.macro, start_ms, end_ms)
            except Exception as exc:
                print(f"    시세 실패 {symbol} {key}: {type(exc).__name__}")
                frames[key] = None
        df = frames[key]
        if df is None or len(df) < 5:
            continue
        try:
            out.append(ask.Evaluated(candidate, run_backtest(candidate.macro, df)))
        except Exception:
            continue
    return out


def _top3_key(picked: list[tuple[str, list]]) -> list[tuple[str, str]]:
    """이 성향이 낸 상위 세 장의 신원 — 종목 + 조합 이름."""
    flat = [(symbol, e.candidate.label) for symbol, got in picked for e in got]
    return flat[:3]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--weeks", type=int, default=8)
    args = parser.parse_args()

    symbols = _symbols()
    print(f"종목: {symbols}")
    now_ms = int(time.time() * 1000)
    cache: dict[tuple[int, str, str], list[ask.Evaluated]] = {}
    for week in range(args.weeks, 0, -1):
        end_ms = now_ms - (week - 1) * WEEK_MS
        for horizon in HORIZONS:
            for symbol in symbols:
                started = time.monotonic()
                cache[(week, horizon, symbol)] = _run_candidates(symbol, horizon, end_ms)
                print(f"  {args.weeks - week + 1}주째 · {horizon} · {symbol} "
                      f"→ 후보 {len(cache[(week, horizon, symbol)])}개 "
                      f"({time.monotonic() - started:.1f}초)")

    rows = []
    for config in CONFIGS:
        config.apply()
        empty = 0
        combos = 0
        excesses: list[float] = []
        distinct = 0
        mdd_ok = 0
        for week in range(args.weeks, 0, -1):
            for horizon in HORIZONS:
                combos += 1
                period = ask.to_period(horizon)
                interval = ask.to_interval(WATCH, period)
                tops: dict[str, list[tuple[str, list]]] = {}
                for profile in PROFILES:
                    picked = []
                    for symbol in symbols:
                        got = ask.select_top(cache[(week, horizon, symbol)], profile,
                                             period=period, interval=interval)
                        if got:
                            picked.append((symbol, got))
                    tops[profile] = picked
                # 전무 = 세 성향 모두 한 장도 못 낸 조합.
                if not any(tops[profile] for profile in PROFILES):
                    empty += 1
                for picked in tops.values():
                    for _symbol, got in picked:
                        excesses.extend(ask.excess_over_hold(e.result) for e in got)
                if _top3_key(tops["balanced"]) != _top3_key(tops["aggressive"]):
                    distinct += 1
                stable_mdd = max((float(e.result.mdd_pct) for _s, got in tops["stable"] for e in got),
                                 default=0.0)
                wild_mdd = max((float(e.result.mdd_pct) for _s, got in tops["aggressive"] for e in got),
                               default=float("inf"))
                if stable_mdd <= wild_mdd:
                    mdd_ok += 1
        rows.append((config.name, empty, combos, distinct, mdd_ok,
                     statistics.median(excesses) if excesses else 0.0, len(excesses)))

    ask.MDD_CAP_BY_PERIOD = {p: dict(t) for p, t in _BASE_CAPS.items()}
    ask.score = _BASE_SCORE

    print("\n| 설정 | 전무 | 조합 | 균형≠공격 | 안정MDD≤공격 | 품질(중앙값 %p) | 카드 |")
    print("|---|---|---|---|---|---|---|")
    for name, empty, combos, distinct, mdd_ok, quality, cards in rows:
        print(f"| {name} | {empty} | {combos} | {distinct}/{combos} | {mdd_ok}/{combos} "
              f"| {quality:+.2f} | {cards} |")

    # 고르는 규칙 — 완료 기준 3(아홉 중 일곱)·4(항상)를 먼저 통과시키고 그중 품질이 가장
    # 좋은 것. 통과한 게 없으면 그대로 말한다(값을 억지로 고르지 않는다).
    ok = [r for r in rows if r[3] >= math.ceil(r[2] * 7 / 9) and r[4] == r[2]]
    pick = max(ok or rows, key=lambda r: (r[5], -r[1]))
    print(f"\n고른 설정: {pick[0]}  (기준 3·4 통과: {'예' if ok else '아니오 — 전부 미달'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
