"""근거 — 백테스트에서 튄 날이 왜 튀었는지 찾는다.

1 층(이 파일의 anomalies/market_context)은 이미 받고 있는 캔들만 쓰므로 전 구간에서
항상 된다. "왜 올랐나" 보다 "시장 전체인가 이 종목인가" 가 먼저 할 질문이다.

순수 함수만 둔다 — 네트워크 · DB · 시계 · AI 를 쓰지 않고, 프레임은 호출자가 이미 가진 것을
받는다. 프레임은 운영 캔들 모양(``timestamp`` · ``close`` · ``volume`` 열)이며, 어느 거래소의
것이든 상관없다. 어떤 기준 종목을 쓸지는 호출자가 고른다.

돌려주는 값은 모두 JSON 으로 그대로 직렬화된다(nan · inf 를 내지 않는다). 변동률을 못 구하는
날(가격이 0 이하이거나 숫자가 아님)은 '변동 없음' 으로 치지 않고 건너뛴다.
"""
from __future__ import annotations

import math
import statistics
from datetime import date, datetime, timezone, tzinfo

SIGMA = 2.0
MAX_ANOMALIES = 5
VOLUME_LOOKBACK = 20
MIN_CHANGES = VOLUME_LOOKBACK // 2  # 변동률이 이보다 적으면 '평소' 를 재지 못한다
MARKET_SHARE = 0.5  # 기준 종목이 같은 방향으로 이 종목 변동의 절반 이상 움직였으면 '시장'


def _number(value) -> float | None:
    """유한한 실수만 통과시키고, 아니면 None."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _moment(value) -> datetime | None:
    """시각 값을 UTC 기준 시각(tz 있는 datetime)으로. 읽을 수 없으면(NaT 등) None.

    tz 정보가 없는 시각은 UTC 로 본다(적재기가 내는 값이 UTC 다). pd.NaT 는 datetime 의 하위
    값이라 isinstance 를 통과하므로 자기 자신과 같지 않다는 점으로 먼저 걸러야 한다. 이 검사는
    try 안에 둔다 — pd.NA 는 ``!=`` 가 pd.NA 를 돌려줘 bool 변환에서 TypeError 가 나는데, 밖에 두면
    그 한 행이 프레임 전체를 버리게 만든다(안에 두면 str 경로로 떨어져 None).
    """
    if value is None:
        return None
    try:
        if value != value:
            return None
        moment = value.to_pydatetime(warn=False) if hasattr(value, "to_pydatetime") else value
        if isinstance(moment, datetime):
            if moment != moment:
                return None
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            return moment
    except (AttributeError, TypeError, ValueError, OverflowError):
        pass
    try:
        day = date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def _label(moment: datetime | None, tz: tzinfo) -> str | None:
    """시각을 tz 기준 'YYYY-MM-DD' 로. 일봉이 KST 0 시에 열리는 프레임(= UTC 전날 15 시)은
    tz 를 KST 로 줘야 그 날짜가 되고, UTC 날짜를 그대로 쓰면 하루 앞선 날짜가 붙는다."""
    if moment is None:
        return None
    try:
        return moment.astimezone(tz).date().isoformat()
    except (ValueError, OverflowError):
        return None


def _series(df, tz: tzinfo = timezone.utc):
    """(날짜, 종가, 거래량) 세 리스트 — 길이가 같고, 못 읽은 칸은 None.

    ``timestamp`` 나 ``close`` 열이 없으면 빈 리스트 셋. ``volume`` 이 없으면 거래량만 전부 None.

    순서 보장:
    - 모든 행의 시각을 읽었으면 ``timestamp`` 로 안정 정렬한다. 변동률은 이웃한 두 행의 차이라,
      순서가 섞인 프레임은 '평소' 만 부풀려 급등을 조용히 놓치고 빈 결과를 낸다(틀린 날짜보다
      알아채기 어렵다). 적재기가 돌려주는 프레임이 정렬돼 있다는 보장이 없어서 여기서 보장한다.
    - 못 읽은 행이 있으면 정렬하지 않는다. 그 행이 원래 어느 날 사이에 있었는지 알 수 없어서,
      어디에 두든 이웃이 아니던 두 날이 이웃이 되어 급등이 엉뚱한 날로 찍힌다. 읽은 행들이 이미
      오름차순이면 받은 순서를 믿고 못 읽은 행의 날짜만 None 으로 둔다(앞뒤 변동은 건너뜀).
      오름차순이 아니면 믿을 수 없으므로 날짜를 모두 None 으로 돌려 빈 결과로 닫는다 — 틀린 날짜가
      뉴스 검색까지 흘러가는 것보다 근거 칸이 비는 편이 낫다. 같은 시각이 겹치는 것은 오름차순으로
      본다(캔들 자료에 있을 수 있다).
    한계: 못 읽은 행 '하나만' 제자리를 벗어나고 읽은 행들은 오름차순이면 이를 알아낼 단서가 없어
    받은 순서를 믿으므로, 그 행에 닿은 급등이 하루 늦게 찍힐 수 있다(순서 위반이 읽은 행에
    드러나는 경우만 닫을 수 있다).
    """
    try:
        moments = [_moment(value) for value in df["timestamp"]]
        closes = [_number(value) for value in df["close"]]
    except (AttributeError, KeyError, TypeError, ValueError):
        return [], [], []
    try:
        volumes = [_number(value) for value in df["volume"]]
    except (KeyError, TypeError, ValueError):
        volumes = [None] * len(closes)
    if len(volumes) != len(closes) or len(moments) != len(closes):
        return [], [], []
    known = [moment for moment in moments if moment is not None]
    if len(known) == len(moments):
        order = sorted(range(len(moments)), key=lambda index: moments[index])
        return ([_label(moments[index], tz) for index in order],
                [closes[index] for index in order],
                [volumes[index] for index in order])
    if any(before > after for before, after in zip(known, known[1:])):
        return [None] * len(closes), closes, volumes
    return [_label(moment, tz) for moment in moments], closes, volumes


def _changes(closes, stamps) -> list[float | None]:
    """일간 변동률(%). 원소 i 는 closes[i] → closes[i + 1] 이라 길이가 하나 적다.

    앞뒤 가격 중 하나라도 없거나 0 이하면 None — 0 으로 두면 '평소' 가 왜곡된다. 앞뒤 행 중
    하나라도 날짜를 못 읽었으면 그것도 None — 그 변동이 어느 날 닿았는지 모르므로 이웃 날의
    것으로 옮겨 적지 않고, '평소' 측정에도 넣지 않는다.
    """
    out: list[float | None] = []
    for index, (before, after) in enumerate(zip(closes, closes[1:])):
        change = None
        if (before is not None and after is not None and before > 0 and after > 0
                and stamps[index] is not None and stamps[index + 1] is not None):
            try:
                change = (after / before - 1.0) * 100.0
            except (OverflowError, ZeroDivisionError):
                change = None
            if change is not None and not math.isfinite(change):
                change = None
        out.append(change)
    return out


def _volume_ratio(volumes, day: int) -> float | None:
    """day 번째 날 거래량 ÷ 그 전 VOLUME_LOOKBACK 일 평균. 못 구하면 None."""
    today = volumes[day]
    if today is None or today < 0:
        return None
    window = [value for value in volumes[max(0, day - VOLUME_LOOKBACK):day]
              if value is not None and value >= 0]
    average = sum(window) / len(window) if window else 0.0
    if not (math.isfinite(average) and average > 0):
        return None
    ratio = today / average
    return round(ratio, 2) if math.isfinite(ratio) else None


def anomalies(df, *, sigma: float = SIGMA, limit: int = MAX_ANOMALIES,
              tz: tzinfo = timezone.utc) -> list[dict]:
    """일간 변동이 표준편차의 sigma 배를 넘는 날들. 절댓값이 큰 순으로 최대 limit 개.

    날짜는 변동이 '닿은' 날이다 — 변동률은 전날 종가에서 그날 종가까지의 값이다.
    volume_ratio 는 그날 거래량을 그 전 20 일 평균(당일 제외)으로 나눈 값이며, 거래량 열이
    없거나 평균이 0 이면 None. 변동이 평소와 다르지 않은 종목은 빈 리스트를 돌려준다(오류가
    아니라 정상 결과) — 짧은 시계열이나 0 이하 가격이 있어도 마찬가지다. limit 이 0 이하면 빈 리스트.

    tz 는 날짜를 매기는 시간대다(기본 UTC). 일봉이 KST 0 시(= UTC 전날 15 시)에 열리는
    프레임은 KST 를 넘겨야 그 거래소의 날짜와 같아진다. UTC 0 시에 열리는 프레임은 기본 그대로
    둔다. 시각을 읽을 수 없는 행에 닿거나 거기서 떠나는 변동은 날짜를 매길 수 없어 건너뛰고,
    읽을 수 없는 행이 있는데 읽은 행의 순서도 오름차순이 아닌 프레임은 빈 결과를 돌려준다.
    기준 종목과 함께 쓸 때는 두 쪽의 tz 가 어긋나지 않게 market_evidence 를 쓴다.
    """
    try:
        cap = int(limit)
    except (TypeError, ValueError, OverflowError):
        cap = MAX_ANOMALIES
    if cap <= 0:
        return []
    multiple = _number(sigma)
    multiple = SIGMA if multiple is None else max(0.0, multiple)

    stamps, closes, volumes = _series(df, tz)
    changes = _changes(closes, stamps)
    valid = [change for change in changes if change is not None]
    if len(valid) < MIN_CHANGES:
        return []
    try:
        deviation = statistics.pstdev(valid)
    except (statistics.StatisticsError, OverflowError):
        return []
    if not (math.isfinite(deviation) and deviation > 0):
        return []
    threshold = deviation * multiple

    rows = []
    for index, change in enumerate(changes):
        # changes[index] 는 closes[index] → closes[index + 1] 이므로 그 변동이 닿은 날은 index + 1
        day = index + 1
        if change is None or change == 0 or abs(change) < threshold or stamps[day] is None:
            continue
        rows.append({"date": stamps[day], "change_pct": round(change, 2),
                     "volume_ratio": _volume_ratio(volumes, day)})
    rows.sort(key=lambda row: abs(row["change_pct"]), reverse=True)
    return rows[:cap]


def market_context(rows: list[dict], btc_df, *, tz: tzinfo) -> list[dict]:
    """같은 날 기준 종목이 얼마나 움직였나로 '시장' 과 '종목' 을 가른다.

    btc_df 는 호출자가 고른 기준 종목의 캔들 프레임이다(필드 이름이 btc_ 인 것은 응답 규약일
    뿐, 어떤 거래소 · 어떤 종목이든 된다). 기준 종목이 이 종목과 같은 방향으로 변동의
    MARKET_SHARE 배 이상 움직였으면 verdict 는 '시장', 아니면 '종목' — 시장이 반대로 움직였는데
    이 종목만 간 것은 시장으로 설명되지 않는다. 기준 종목이 정확히 0 이면 자료는 있고 안 움직인
    것이므로 '종목'(자료가 없는 None 과 다르다). 기준 종목 자료가 그 날짜에 없거나 이 종목의
    변동이 0 이면 단정할 근거가 없으므로 verdict 를 빈 문자열로, btc_change_pct 는 None 으로 둔다.
    tz 는 rows 의 날짜를 매길 때 쓴 것과 같아야 한다(어긋나면 기준 종목의 급등이 하루 앞뒤로
    붙어 그럴듯하게 틀린다). 그래서 기본값 없이 꼭 적게 했다 — 보통은 market_evidence 가 맞춰 준다.
    받은 행은 고치지 않고 새 딕셔너리로 돌려준다.
    """
    stamps, closes, _volumes = _series(btc_df, tz)
    by_date = {}
    for index, change in enumerate(_changes(closes, stamps)):
        if change is not None:
            by_date[stamps[index + 1]] = change
    out = []
    for row in rows or []:
        benchmark = by_date.get(row.get("date"))
        own = _number(row.get("change_pct"))
        verdict = ""
        if benchmark is not None and own:
            same_way = benchmark * own > 0
            verdict = "시장" if same_way and abs(benchmark) >= abs(own) * MARKET_SHARE else "종목"
        out.append({**row,
                    "btc_change_pct": None if benchmark is None else round(benchmark, 2),
                    "verdict": verdict})
    return out


def market_evidence(df, benchmark_df, *, tz: tzinfo = timezone.utc,
                    sigma: float = SIGMA, limit: int = MAX_ANOMALIES) -> list[dict]:
    """튄 날을 찾고(anomalies) 같은 날 기준 종목과 견줘 라벨을 붙인다(market_context).

    두 쪽에 같은 tz 를 넘기는 단일 진입점이다. 날짜 기준이 어긋나면 에러 없이 그럴듯하게
    틀린 답이 나오므로, 호출자는 이 함수로 한 번에 부르는 것이 안전하다.
    benchmark_df 는 호출자가 고른 기준 종목 프레임이며, 같은 거래소의 것이어야 날짜가 맞는다.
    """
    found = anomalies(df, sigma=sigma, limit=limit, tz=tz)
    return market_context(found, benchmark_df, tz=tz)
