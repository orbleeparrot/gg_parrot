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

from app import ask, ask_candidates, ask_daily, steadiness  # noqa: E402
from app.data import krw  # noqa: E402
from app.data.binance import PERIOD_PRESET_DAYS  # noqa: E402
from engine.backtest import BacktestResult, run_backtest  # noqa: E402
from app.marketdata import fetch_klines_for_macro  # noqa: E402

EXCHANGE = "upbit"
# 확인 빈도 → 봉. 기본은 '가끔'(4시간봉)으로 사양 §1-1 의 성적표와 같은 자를 쓴다.
# --watch constantly 로 15분봉 조합만 따로 볼 수 있다(사양 §6 이 요구한 측정).
DEFAULT_WATCH = "sometimes"
HORIZONS = ("days", "weeks", "months")
PROFILES = ("stable", "balanced", "aggressive")
SYMBOLS_PER_WINDOW = 3
WEEK_MS = 7 * 86_400_000

_BASE_CAPS = {profile: dict(table) for profile, table in ask.MDD_CAP_BY_PERIOD.items()}
_BASE_SCORE = ask.score
_BASE_STEADY = steadiness.TRADE_WEIGHT


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
    """설정 후보 — 상한 표 배수 · 균형형 점수 함수 · 안정형 매매 방식 제한.

    ``stable_types`` 는 사양 §6 의 대비책이다("안 눌리면 안정형에만 방식 제한을 일부
    되살린다"). None 이면 세 성향이 여덟 방식을 다 본다. 값이 있으면 안정형에서만 그
    방식들로 좁힌다 — 측정에서는 후보를 미리 걸러서 같은 효과를 낸다(build_templates 가
    애초에 안 만드는 것과 결과가 같다).
    """
    name: str
    cap_scale: float
    balanced_score: str
    stable_types: tuple[str, ...] | None = None
    # 꾸준함 보정의 세기(2026-10-29). 0 이면 끈 것 — 끈 것과 켠 것을 나란히 봐야
    # 보정이 '다음 구간' 성적을 올리는지 말할 수 있다. None 이면 기본값 그대로.
    steady_weight: float | None = None

    def apply(self) -> None:
        weight = _BASE_STEADY if self.steady_weight is None else self.steady_weight
        steadiness.TRADE_WEIGHT = weight
        steadiness.MONTH_WEIGHT = weight
        ask.MDD_CAP_BY_PERIOD = {
            profile: {period: value * self.cap_scale for period, value in table.items()}
            for profile, table in _BASE_CAPS.items()
        }
        ask.score = _score_with(self.balanced_score)


CONFIGS = [
    # 2026-10-29 에 더한 축 — 꾸준함 보정이 '다음 구간' 성적을 올리는가.
    # 이 셋만 서로 견주면 되고, 아래 옛 격자는 성향 설정을 보는 것이라 그대로 둔다.
    Config("꾸준함 끔", 1.0, "over_sqrt", steady_weight=0.0),
    Config("꾸준함 기본(0.3)", 1.0, "over_sqrt", steady_weight=0.3),
    Config("꾸준함 강하게(0.45)", 1.0, "over_sqrt", steady_weight=0.45),
    Config("표 그대로 · 수익/√MDD", 1.0, "over_sqrt"),
    Config("표 그대로 · 수익-0.5×MDD", 1.0, "minus_half"),
    Config("표 그대로 · 수익-1.5×MDD", 1.0, "minus_one_half"),
    Config("표 그대로 · 샤프", 1.0, "sharpe"),
    Config("상한 1.25배 · 수익/√MDD", 1.25, "over_sqrt"),
    Config("상한 0.8배 · 수익/√MDD", 0.8, "over_sqrt"),
    # 사양 §6 대비책 — 완료 기준 4(안정형 MDD < 공격형)가 미달일 때.
    Config("안정형 H·I 제외 · 수익/√MDD", 1.0, "over_sqrt",
           stable_types=("C", "J", "G", "A", "F", "E")),
    Config("안정형 옛 화이트리스트 · 수익/√MDD", 1.0, "over_sqrt",
           stable_types=("C", "J", "G", "A")),
]


def _daily_history(symbols: list[str], end_ms: int, days: int) -> dict[str, list[tuple[int, float]]]:
    """심볼별 (봉 시각, 종가) 전체 이력. 종목당 **한 번만** 받고 창마다 잘라 쓴다.

    이게 2026-10-29 변경의 핵심이다. 그 전에는 종목 선정을 과거로 돌려 볼 수 없었다 —
    거래소 티커는 과거 시점의 24시간 통계를 주지 않기 때문이다. 일봉은 과거를 주므로
    ``chop_pct`` · ``range_position_pct`` 기반 선정은 창마다 재현할 수 있다.
    """
    start_ms = end_ms - (days + 5) * 86_400_000
    out: dict[str, list[tuple[int, float]]] = {}
    for symbol in symbols:
        try:
            frame, _source = krw.get_klines(symbol, start_ms, end_ms, "1d", exchange=EXCHANGE)
        except Exception as exc:
            print(f"    일봉 실패 {symbol}: {type(exc).__name__}")
            continue
        stamps = [int(ts.timestamp() * 1000) for ts in frame["timestamp"]]
        out[symbol] = list(zip(stamps, (float(v) for v in frame["close"])))
    return out


def _daily_at(history: dict[str, list[tuple[int, float]]], at_ms: int) -> dict[str, list[float]]:
    """그 시점까지의 최근 ``ask_daily.DAYS`` 개 종가 — 앞을 보지 않게 자른다."""
    cut: dict[str, list[float]] = {}
    for symbol, rows in history.items():
        closes = [close for stamp, close in rows if stamp <= at_ms][-ask_daily.DAYS:]
        if len(closes) >= 2:
            cut[symbol] = closes
    return cut


def _symbols_at(tickers: list[dict], history: dict, at_ms: int) -> list[str]:
    """그 창에서 물어볼까가 골랐을 종목들.

    한계로 남는 것: 유동성 순위(거래대금 상위 40)는 여전히 **지금** 티커에서 온다 —
    거래소가 과거 24시간 통계를 주지 않는다. 창마다 갈리는 것은 그 40개 안에서의
    **30일 창 재정렬**이다. 그게 이번에 바꾼 부분이고, 측정하려는 것도 그것이다.
    """
    pool = ask_candidates.build_pool(tickers, profile="balanced", exchange=EXCHANGE,
                                     daily=_daily_at(history, at_ms))
    return [c["symbol"] for c in pool[:SYMBOLS_PER_WINDOW]]


def _run_candidates(symbol: str, horizon: str, end_ms: int,
                    watch: str = DEFAULT_WATCH) -> list[ask.Evaluated]:
    """이 종목 · 이 호흡 · 이 끝시각에서 모든 템플릿 후보를 한 번씩 돌린다."""
    period = ask.to_period(horizon)
    interval = ask.to_interval(watch, period)
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


def _forward(symbol: str, horizon: str, end_ms: int, candidates: list[ask.Evaluated],
             forward_ms: int, watch: str = DEFAULT_WATCH) -> dict[str, float]:
    """후보 레이블 → **다음 구간**(창 끝에서 1주) 홀딩 대비 초과 수익(%p).

    왜 이게 있어야 하나: 기존 '품질' 열은 ``excess_over_hold`` 를 **고른 그 구간**에서
    잰 값이다. 고를 때 쓴 숫자를 성적으로 다시 쓰는 셈이라, 후보 90개 중 그 구간에 가장
    잘 맞은 것을 뽑는 구조에서는 항상 크게 나온다(실측: 1주 창 중앙값 +149%p 가 설정
    8가지에서 전부 같았다). 사용자가 받는 것은 **앞으로** 쓸 설정이므로 앞을 봐야 한다.

    왜 1주인가: 창을 주 단위로 굴리므로 모든 호흡에서 같은 길이의 다음 구간이 생기고
    서로 견줄 수 있다. 3개월짜리를 1주로 재는 것은 분명 거칠지만, 고른 구간에서 다시
    재는 것보다는 앞을 본다.
    """
    period = ask.to_period(horizon)
    interval = ask.to_interval(watch, period)
    out: dict[str, float] = {}
    frames: dict[str, object] = {}
    for evaluated in candidates:
        macro = evaluated.candidate.macro
        key = macro.candle_interval
        if key not in frames:
            try:
                frames[key], _source = fetch_klines_for_macro(macro, end_ms, end_ms + forward_ms)
            except Exception:
                frames[key] = None
        df = frames[key]
        if df is None or len(df) < 5:
            continue
        try:
            out[evaluated.candidate.label] = ask.excess_over_hold(run_backtest(macro, df))
        except Exception:
            continue
    return out


def _top3_key(got: list) -> list[str]:
    """이 성향이 **이 종목**에서 낸 상위 세 장의 신원.

    왜 종목마다 세나: 사용자는 한 번에 한 종목을 묻는다(run_ask 가 symbol 하나를 받는다).
    세 종목의 카드를 이어 붙인 뒤 앞 셋만 보면 첫 종목이 세 장을 냈을 때 나머지 둘이
    비교에서 아예 사라지고, 한쪽만 카드를 낸 종목이 섞이면 **서로 다른 종목**을 견주게
    된다 — 둘 다 §4-3 이 묻는 값이 아니다.
    """
    return [e.candidate.label for e in got[:3]]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--weeks", type=int, default=8)
    parser.add_argument("--watch", default=DEFAULT_WATCH, choices=sorted(ask._WATCH_INTERVAL))
    # 종목 선정 축은 격자로 못 돌린다 — 어떤 종목을 백테스트했는지가 달라져 캐시가
    # 통째로 바뀌기 때문이다. 끈 상태로 한 번, 켠 상태로 한 번 돌려 비교한다.
    parser.add_argument("--no-daily", action="store_true",
                        help="30일 창을 끄고 예전 24시간 지표로 종목을 고른다(전후 비교용)")
    # 다음 구간을 얼마나 길게 볼지. week 는 모든 호흡에서 1주라 서로 견주기 쉽지만
    # 3개월짜리 설정을 1주로 재는 셈이다. period 는 그 호흡의 기간만큼 본다 — 공정한
    # 길이지만 창을 그만큼 더 뒤로 밀어야 해서 최근 장을 덜 본다.
    parser.add_argument("--forward", default="week", choices=("week", "period"),
                        help="다음 구간 길이: week(1주 고정) 또는 period(호흡의 기간만큼)")
    args = parser.parse_args()
    watch = args.watch

    now_ms = int(time.time() * 1000)
    tickers = krw.get_all_tickers(EXCHANGE)
    universe = ask_candidates.liquid_symbols(tickers, exchange=EXCHANGE)
    if args.no_daily:
        history = {}
        print(f"30일 창 **끔** — 예전 24시간 지표로 종목을 고른다 · 확인 빈도: {watch}")
    else:
        print(f"유동성 상위 {len(universe)}종목의 일봉을 받는다 (종목당 한 번) …")
        history = _daily_history(universe, now_ms, args.weeks * 7 + ask_daily.DAYS + 5
                                 + (max(PERIOD_PRESET_DAYS[ask.to_period(h)] for h in HORIZONS)
                                    if args.forward == "period" else 7))
        print(f"  일봉 확보 {len(history)}/{len(universe)}종목 · 확인 빈도: {watch}")

    cache: dict[tuple[int, str, str], list[ask.Evaluated]] = {}
    ahead: dict[tuple[int, str, str], dict[str, float]] = {}
    window_symbols: dict[tuple[int, str], list[str]] = {}
    positions: list[float] = []
    for week in range(args.weeks, 0, -1):
        print(f"  {args.weeks - week + 1}주째")
        for horizon in HORIZONS:
            period = ask.to_period(horizon)
            forward_ms = (WEEK_MS if args.forward == "week"
                          else PERIOD_PRESET_DAYS[period] * 86_400_000)
            # **창을 다음 구간 길이만큼 뒤로 민다.** 예전에는 가장 최근 창이 오늘로
            # 끝나 채점할 다음 구간이 아예 없었다. 이제 모든 창 뒤에 그만큼이 남는다.
            end_ms = now_ms - (week - 1) * WEEK_MS - forward_ms
            symbols = _symbols_at(tickers, history, end_ms)
            window_symbols[(week, horizon)] = symbols
            for coin in ask_candidates.build_pool(tickers, profile="balanced", exchange=EXCHANGE,
                                                  daily=_daily_at(history, end_ms)):
                if coin.get("range_pos_30d") is not None:
                    positions.append(float(coin["range_pos_30d"]))
            print(f"    {horizon} 종목: {symbols}")
            for symbol in symbols:
                started = time.monotonic()
                got = _run_candidates(symbol, horizon, end_ms, watch)
                cache[(week, horizon, symbol)] = got
                ahead[(week, horizon, symbol)] = _forward(symbol, horizon, end_ms, got,
                                                          forward_ms, watch)
                print(f"      {symbol} → 후보 {len(got)}개 "
                      f"· 앞 구간 {len(ahead[(week, horizon, symbol)])}개 "
                      f"({time.monotonic() - started:.1f}초)")

    rows = []
    for config in CONFIGS:
        config.apply()
        # 세는 단위를 사양에 맞춘다.
        #  · 전무: (주 × 호흡 × **성향**) 에서 추천 종목 **전부** 0장 — 사양 §1-1 의 정의.
        #  · 구별: (주 × 호흡 × **종목**) 에서 균형형과 공격형의 상위 3장이 다른가 — 사용자는
        #    한 번에 한 종목을 묻는다.
        #  · 안정형 MDD: 둘 다 카드를 냈을 때만 센다. 한쪽이 0장이면 0.0 <= inf 로 공짜 통과다.
        empty = empty_cells = 0
        distinct = distinct_cells = 0
        mdd_ok = mdd_cells = mdd_worse = 0
        excesses: list[float] = []
        forwards: list[float] = []
        shares: list[float] = []
        by_horizon: dict[str, list[float]] = {h: [] for h in HORIZONS}
        for week in range(args.weeks, 0, -1):
            for horizon in HORIZONS:
                symbols = window_symbols[(week, horizon)]
                period = ask.to_period(horizon)
                interval = ask.to_interval(watch, period)
                per_profile: dict[str, dict[str, list]] = {}
                for profile in PROFILES:
                    allowed = config.stable_types if profile == "stable" else None
                    per_profile[profile] = {
                        symbol: ask.select_top(
                            [e for e in cache[(week, horizon, symbol)]
                             if allowed is None or e.candidate.macro.rule_type.value in allowed],
                            profile, period=period, interval=interval)
                        for symbol in symbols
                    }
                for profile in PROFILES:
                    empty_cells += 1
                    if not any(per_profile[profile].values()):
                        empty += 1
                    for symbol, got in per_profile[profile].items():
                        excesses.extend(ask.excess_over_hold(e.result) for e in got)
                        # 앞 구간 — 사용자가 실제로 받는 쪽. 고른 구간 숫자와 따로 센다.
                        next_window = ahead.get((week, horizon, symbol), {})
                        hit = [next_window[e.candidate.label] for e in got
                               if e.candidate.label in next_window]
                        forwards.extend(hit)
                        by_horizon[horizon].extend(hit)
                        shares.extend(float(e.result.top_trade_share_pct) for e in got
                                      if e.result.top_trade_share_pct is not None)
                for symbol in symbols:
                    balanced, wild = per_profile["balanced"][symbol], per_profile["aggressive"][symbol]
                    if not balanced and not wild:
                        continue        # 둘 다 0장이면 '구별' 을 물을 수 없다
                    distinct_cells += 1
                    if _top3_key(balanced) != _top3_key(wild):
                        distinct += 1
                    calm = per_profile["stable"][symbol]
                    if calm and wild:
                        mdd_cells += 1
                        calm_mdd = max(float(e.result.mdd_pct) for e in calm)
                        wild_mdd = max(float(e.result.mdd_pct) for e in wild)
                        if calm_mdd < wild_mdd:
                            mdd_ok += 1
                        elif calm_mdd > wild_mdd:
                            # 안정형이 공격형보다 **더** 흔들리는 카드를 1등으로 냈다 —
                            # 같은 카드가 양쪽에서 이긴 '동률' 과 달리 이건 진짜 결함이다.
                            mdd_worse += 1
        rows.append((config.name, empty, empty_cells, distinct, distinct_cells,
                     mdd_ok, mdd_cells, mdd_worse,
                     statistics.median(excesses) if excesses else 0.0, len(excesses),
                     statistics.median(forwards) if forwards else 0.0, len(forwards),
                     statistics.median(shares) if shares else 0.0,
                     {h: (statistics.median(v) if v else None, len(v)) for h, v in by_horizon.items()}))

    ask.MDD_CAP_BY_PERIOD = {p: dict(t) for p, t in _BASE_CAPS.items()}
    ask.score = _BASE_SCORE
    steadiness.TRADE_WEIGHT = steadiness.MONTH_WEIGHT = _BASE_STEADY

    if positions:
        print("")
        print(f"후보 풀의 '한 달 범위 내 위치' 분포 — 중앙값 {statistics.median(positions):.1f} · "
              f"85 초과 {sum(1 for p in positions if p > 85.0)}/{len(positions)}개. "
              f"내려갈수록 '이미 고점에 올라간 코인' 이 빠졌다는 뜻이다.")

    print("")
    print("| 설정 | 전무(성향칸) | 균형≠공격(종목칸) | 안정MDD<공격 | 안정형이 더 흔들림 "
          "| 고른구간(중앙값 %p) | **다음구간(중앙값 %p)** | 상위3거래몫(중앙값 %) | 카드 |")
    print("|---|---|---|---|---|---|---|---|---|")
    for (name, empty, ec, distinct, dc, mdd_ok, mc, mdd_worse,
         quality, cards, forward, fcards, share, _by_h) in rows:
        print(f"| {name} | {empty}/{ec} | {distinct}/{dc} | {mdd_ok}/{mc} | {mdd_worse}/{mc} "
              f"| {quality:+.2f} | **{forward:+.2f}** ({fcards}장) | {share:.1f} | {cards} |")
    print("")
    print("'고른구간' 은 **고를 때 쓴 숫자를 성적으로 다시 쓴 것**이라 항상 크게 나온다 "
          "— 후보 90개 중 그 구간에 가장 잘 맞은 것을 뽑기 때문이다. 판단은 '다음구간' 으로 한다.")

    # 호흡별로 나눠 본다 — 전체 중앙값 하나로는 어느 호흡이 끌어내렸는지 알 수 없고,
    # 1주 호흡과 6개월 호흡은 애초에 같은 난이도가 아니다.
    print("")
    print("| 설정 | " + " | ".join(f"{h} 다음구간" for h in HORIZONS) + " |")
    print("|---" * (len(HORIZONS) + 1) + "|")
    for row in rows:
        cells = []
        for horizon in HORIZONS:
            median, count = row[13][horizon]
            cells.append("—" if median is None else f"{median:+.2f} ({count}장)")
        print(f"| {row[0]} | " + " | ".join(cells) + " |")

    # 고르는 규칙 — 완료 기준 3(일곱/아홉 = 78% 이상)·4(항상)를 먼저 통과시키고 그중
    # **다음 구간** 성적이 가장 좋은 것. 2026-10-29 전에는 '고른 구간' 으로 골랐는데,
    # 그건 고를 때 쓴 숫자라 설정을 바꿔도 거의 움직이지 않았다(실측: 8개 설정 전부
    # 중앙값 +149.44%p 로 동일). 통과한 게 없으면 그대로 말한다.
    ok = [r for r in rows if r[4] and r[3] / r[4] >= 7 / 9 and r[6] and r[5] == r[6]]
    pick = max(ok or rows, key=lambda r: (r[10], -r[1]))
    print("")
    print(f"고른 설정: {pick[0]}  (기준 3·4 통과: {'예' if ok else '아니오 — 전부 미달'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
