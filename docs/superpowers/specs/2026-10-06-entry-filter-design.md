# 진입 필터 설계 (프로 빌더 2차 · 1/3)

**작성일:** 2026-10-06
**범위:** 기존 규칙의 새 진입에 조건 하나를 덧씌우는 "진입 필터"
**후속 스펙:** 포트폴리오 묶음(2/3), 코치 패널(3/3) — 각자 별도 스펙·계획

## 1. 왜

프로 빌더가 기본 빌더와 거의 같다. [`StudioPro.jsx:4`](../../../frontend/src/pages/StudioPro.jsx) 가 기본 빌더와 **똑같은 `Builder` 컴포넌트**를 쓰고, 다른 것은 만든 **뒤**(4구간 검증·지표·근거)뿐이다. 만드는 과정은 한 줄도 다르지 않다.

규칙은 A~K 열한 가지가 이미 있고 양쪽 빌더가 똑같이 다 보여준다. 더 내놓을 규칙이 없다. 전문가용으로 모자란 것은 규칙의 **수**가 아니라 **조건을 겹칠 수 없다는 것**이다. 지금은 한 매크로 = 한 규칙이고, "RSI 과매도이면서 추세가 위일 때" 같은 판단을 표현할 방법이 없다.

진입 필터가 그 구멍을 메운다. 사용자가 기본 빌더로는 만들 수 없는 것을 처음 만들게 되는 지점이다.

## 2. 무엇을 만드는가

**새 진입에만 걸리는 관문 하나.** 마감된 봉에서 판정하고, 통과하지 못하면 그 규칙의 진입이 일어나지 않는다.

> `I · 변동성 돌파` + 필터 `20일 이평선 위` → 돌파가 나와도 추세가 아래면 사지 않는다.

필터는 스스로 사지도 팔지도 않는다. 기존 규칙의 "산다"를 "조건 맞으면 산다"로 바꾸는 것뿐이다.

### 1차에 들어가는 것

- 매크로당 필터 **하나** (`Macro.entry_filter`)
- 필터 네 종류: 이동평균 · RSI · 볼린저 위치 · 거래량 배수
- 규칙 **E·F·G·H·I·J·K** 에 적용
- 백테스트 · 검증 · 모의투자 · 실행기(서버 신호) 네 경로 모두 같은 판정
- 빌더 화면의 필터 칸 (기본 빌더·프로 빌더 공통)

### 1차에 안 들어가는 것

| 빠지는 것 | 왜 |
|---|---|
| 필터 여러 개(AND·OR 체인) | 하나로도 "기본 빌더로는 못 만든다"가 느껴진다. 체인은 화면·검증·설명 비용이 몇 배다 |
| 기준 종목 필터 ("BTC가 위일 때만 ETH") | 엔진이 두 번째 종목 봉을 같은 시각에 맞춰 가져와야 한다. 데이터 경로를 건드리므로 2차. 스키마에 자리는 비워 둔다 |
| 규칙 A·B·C 에 필터 | §6 — 실시간에서 캔들을 받지 않는다 |
| 규칙 D 에 필터 | §6 — 사다리 중간을 막으면 팔 짝 없는 매수가 남는다 |
| 청산 쪽 필터 | 필터는 거래를 줄이는 쪽으로만 작동해야 한다(§3) |

## 3. 원칙

1. **필터는 거래를 줄이는 쪽으로만 작동한다.** 진입만 막고 청산은 건드리지 않는다. 잘못 걸어도 손실이 커지지 않는다.
2. **모르면 막는다.** 지표가 아직 데워지지 않아 값이 없으면 진입을 막는다. "모르겠으니 사지 않는다"가 돈에 대해 안전한 방향이다.
3. **마감된 봉으로만 판정한다.** 진행 중인 봉의 종가를 쓰면 미래를 보는 것이 된다.
4. **백테스트와 실거래가 같은 코드로 판정한다.** 두 경로가 갈라질 수 있는 설계는 채택하지 않는다 — [`stepper.py` 모듈 주석](../../../backend/app/engine/stepper.py)이 세운 규칙 그대로.
5. **이미 들어간 자리를 수습하는 주문은 막지 않는다.** H의 세이프티오더, D의 사다리 보충은 필터와 무관하다.

## 4. 필터 모델

`Macro.rule_type` + `params` 를 `_PARAMS_MODEL` 로 검증하는 [기존 방식](../../../backend/app/engine/schema.py)을 그대로 따른다.

```python
class FilterKind(str, Enum):
    MA = "ma"            # 이동평균 위/아래
    RSI = "rsi"          # RSI 구간
    BOLLINGER = "bb"     # 볼린저 밴드 위치
    VOLUME = "volume"    # 거래량 배수

class MAFilterParams(BaseModel):
    ma_type: Literal["SMA", "EMA"] = "SMA"
    period: int = Field(ge=2, le=400)      # WARMUP_CANDLES=500 이 덮는 상한
    side: Literal["above", "below"]        # 종가가 이평선 위/아래일 때 통과

class RSIFilterParams(BaseModel):
    period: int = Field(default=14, ge=2, le=200)
    min: Optional[float] = Field(default=None, ge=0, le=100)
    max: Optional[float] = Field(default=None, ge=0, le=100)
    # 둘 다 None 이면 거부. min <= max 여야 한다.

class BollingerFilterParams(BaseModel):
    period: int = Field(default=20, ge=2, le=400)
    num_std: float = Field(default=2.0, gt=0, le=5)
    zone: Literal["below_lower", "above_upper", "inside"]

class VolumeFilterParams(BaseModel):
    period: int = Field(default=20, ge=2, le=400)
    multiple: float = Field(gt=0, le=100)  # 평균 거래량의 N배 이상일 때 통과

class EntryFilter(BaseModel):
    kind: FilterKind
    params: dict
    # Macro 와 같은 모양의 검증: kind 별 모델로 params 를 다시 검증하고, 남는 키는 거부한다.
```

`Macro` 에 한 줄:

```python
entry_filter: Optional[EntryFilter] = None
```

### 스키마 수준의 금지

`Macro` 검증에서 거부한다 — 이것이 가장 강한 관문이다. 화면·API·엔진이 각각 실수해도 매크로 자체가 만들어지지 않는다.

```python
FILTERABLE_TYPES = frozenset({RuleType.E, RuleType.F, RuleType.G,
                              RuleType.H, RuleType.I, RuleType.J, RuleType.K})

# entry_filter 가 있는데 rule_type 이 FILTERABLE_TYPES 밖이면 ValueError
```

거래량 필터는 **봉의 거래량**이 필요하다. 지금 `on_candle` 은 `(o, h, l, c)` 만 받는다 — §5.4 를 볼 것.

## 5. 엔진

### 5.1 평가기

새 파일 `backend/app/engine/entry_filter.py`. 심(sim) 클래스가 두 파일(`candles.py`, `stepper.py`)에 나뉘어 있으므로 판정을 한 곳에 둔다.

```python
class FilterEval:
    """마감봉 종가를 하나씩 먹고, '지금 진입해도 되는가'를 답한다."""
    def update(self, close: float, volume: Optional[float] = None) -> None: ...
    def allows(self) -> bool: ...        # 값이 아직 없으면 False (원칙 2)
    def note(self) -> str: ...           # "20일 이평선 위" — Fill.reason·설명에 실린다

def make_filter(macro: Macro) -> Optional[FilterEval]:
    """entry_filter 가 없으면 None — 호출부는 None 을 '관문 없음'으로 읽는다."""
```

내부는 기존 상태 기계를 그대로 쓴다: [`RSIState`](../../../backend/app/engine/candles.py) · [`BollingerState`](../../../backend/app/engine/candles.py) · [`MAState`](../../../backend/app/engine/candles.py). 세 클래스 모두 데워지기 전에는 `None` 을 돌려주므로 원칙 2가 자연히 지켜진다. 거래량 평균만 새로 쓴다(고정 창 `deque`).

지표 상태 클래스를 `candles.py` 에서 `entry_filter.py` 로 **옮기지 않는다** — 기존 규칙 F·G·J 가 쓰고 있고, 이동은 이 작업과 무관한 위험이다. `entry_filter.py` 가 `candles.py` 에서 import 한다.

### 5.2 갱신 시점 — 봉이 끝난 뒤

[`CandleSim.on_candle`](../../../backend/app/engine/candles.py) 은 모든 캔들 심이 지나가는 7줄짜리 단일 통로다. 여기 한 줄을 더한다:

```python
def on_candle(self, o, h, l, c, ts):
    fills = []
    ...  # 숏 펀딩
    self._strategy(o, h, l, c, ts, fills)
    if self.entry_filter is not None:
        self.entry_filter.update(c)      # ← 전략이 끝난 뒤, 이 봉의 종가로
    return fills
```

전략보다 **뒤**에 갱신하는 것이 핵심이다. 그래야 이 봉 안에서 일어나는 진입 판단은 **직전 마감봉**의 필터 값을 본다. 진행 중인 봉으로 자기 자신을 판정하는 미래 참조가 구조적으로 불가능해진다.

### 5.3 관문 — 이미 있는 자리

[`CandleSim._entry_blocked(ts)`](../../../backend/app/engine/candles.py) 는 이미 정지·하루손실·쿨다운을 보는 공용 관문이고, 다섯 군데가 이것을 거친다:

| 규칙 | 호출 지점 |
|---|---|
| E 트레일링 | `candles.py:496` |
| H 마틴게일 (첫 진입만) | `candles.py:663` |
| I 변동성 돌파 | `candles.py:733` |
| F·G·J 지표 | `candles.py:775` |
| K 하락 방어 | `candles.py:998` |

한 줄을 더한다:

```python
def _entry_blocked(self, ts) -> bool:
    ...
    if self.entry_filter is not None and not self.entry_filter.allows():
        return True
    return False
```

**갱신 한 곳, 관문 한 곳.** 일곱 규칙이 함께 따라온다.

### 5.4 거래량

`on_candle(o, h, l, c, ts)` 에 거래량이 없다. 백테스트의 봉 데이터에는 거래량이 있지만 심까지 오지 않는다.

**결정: 거래량 필터를 1차에 넣고, `on_candle` 에 선택 인자 `volume: Optional[float] = None` 을 더한다.** 기본값이 있으므로 기존 호출(테스트 포함)은 그대로 돈다. 거래량을 못 받은 경우 거래량 필터는 `allows() == False` — 원칙 2대로 막는다.

넘겨야 하는 곳 네 군데: 백테스트 봉 루프, `CandleSim.warmup`, [`LiveCandleSim.on_candle`](../../../backend/app/engine/candles.py), [`StrategyDriver.push_candle`](../../../backend/app/engine/driver.py). `push_candle` 은 `t_ms, o, h, l, c = candle` 로 **정확히 5개를 언패킹**하므로, 거래량이 실린 봉을 받으려면 이 줄이 바뀐다 — 봉 모양을 바꾸는 유일한 지점이고, 테스트가 튜플 더미를 쓰므로(`driver.py:203` 주석) 길이 변화에 관대해야 한다.

거래량 필터를 2차로 미루면 이 네 군데를 건드리지 않아도 된다. 그래도 1차에 넣는 이유: 거래량은 돌파·마틴게일과 함께 쓰이는 가장 흔한 필터이고, 나중에 봉 모양을 바꾸는 것이 지금보다 비싸다.

### 5.5 웜업

[`CandleSim.warmup(candles)`](../../../backend/app/engine/candles.py) 이 과거 마감봉을 `on_candle` 로 흘려보내고 장부를 되돌린다. 필터 갱신이 `on_candle` 안에 있으므로 **웜업이 필터도 데운다** — 따로 할 일이 없다.

`WARMUP_CANDLES = 500` ([`paper.py`](../../../backend/app/paper.py), [`runner_engine.py`](../../../backend/app/runner_engine.py)) 이 이미 "MA 400봉"을 덮는다. 필터 기간 상한 400은 그 안이다.

## 6. 규칙별 적용 범위와 이유

| 규칙 | 필터 | 이유 |
|---|---|---|
| E 트레일링 | ✅ | 공용 관문을 거친다 |
| F RSI · G 볼린저 · J 이평크로스 | ✅ | 지표형. 두 번째 조건이 가장 자연스러운 자리 |
| H 마틴게일 | ✅ **첫 진입만** | 세이프티오더는 막지 않는다(원칙 5) |
| I 변동성 돌파 | ✅ | 간판 조합 — "추세가 위일 때만 돌파를 산다" |
| K 하락 방어 | ✅ | 최초 롱 진입을 막는다. 전환 뒤 숏은 수습이므로 막지 않는다 |
| **D 그리드** | ❌ | 사다리를 깔고 걸리면 사는 방식이다. 중간을 막으면 **팔 짝이 없는 매수**가 남는다. 공용 관문도 애초에 거치지 않는다 |
| **A · B · C** | ❌ | 실시간에서 **캔들을 아예 받지 않는다** — [`candle_keys()`](../../../backend/app/engine/driver.py) 가 `is_candle_based()` 거짓이면 빈 목록을 돌려주고, `PositionSim`(A·B) 과 `DcaSim`(C) 에는 `on_candle` 이 없다. 백테스트(봉 루프)에서는 필터가 되는데 실거래에서는 안 되므로 원칙 4를 깬다 |

A·B·C 에 필터를 넣으려면 세 규칙에 캔들 심을 만들고 실시간 경로를 바꿔야 한다. 체결 의미가 바뀔 위험이 큰 별개 작업이다. 1차에서는 **스키마가 거부**한다(§4).

## 7. 실행기 경로

[`needs_signals(macro)`](../../../backend/app/runner.py) 가 "A·B는 실행기가 직접 판단, 그 외는 서버 신호"를 정한다. 필터 대상 일곱 규칙은 **모두 이미 서버 신호 경로**다 — A·B가 빠졌으므로.

**그래서 `needs_signals` 도, 실행기도 바뀌지 않는다.** 서버가 필터를 보고 명령을 내리고, 실행기는 지금처럼 명령만 실행한다. 방금 끝난 국내 거래소 작업(v10)을 건드리지 않는다.

실행기 버전 하한도 올리지 않는다. 필터가 걸린 매크로는 신호 지원 실행기(v8+)가 필요한데, 그 게이트는 이미 있다 ([`SIGNAL_REQUIRED_DETAIL`](../../../backend/app/runner.py)).

## 8. 검증 · 설명 · 리더보드

- **검증**([`validation.py`](../../../backend/app/engine/validation.py)): 필터는 거래 수를 줄인다. `MIN_TRADES = 10` 에 걸려 "표본이 모자라다"가 더 자주 나온다. 이것은 고장이 아니라 **맞는 경고**다 — 필터를 조인 결과 거래가 몇 번 없으면 통계로 못 쓰는 것이 사실이다. 검증 쪽은 손대지 않는다.
- **설명**([`explain.py`](../../../backend/app/engine/explain.py)): 필터가 걸린 매크로의 설명에 필터 한 줄이 들어가야 한다. 빠지면 사용자가 "왜 안 샀는지" 를 설명에서 못 찾는다.
- **`Fill.reason`**: 필터는 체결을 만들지 않으므로 새 사유가 생기지 않는다. 진입 사유에 필터 문구를 **끼워 넣지 않는다** — 지표형의 `_signal_note` 조립 문구가 바뀌면 그 문구를 고정한 기존 테스트가 깨지고, 필터는 "왜 안 샀나"의 근거이지 "왜 샀나"의 근거가 아니다. 설명(`explain.py`)이 그 역할을 맡는다.
- **리더보드**: 필터가 걸린 매크로를 복사해 오면 필터도 함께 와야 한다. `Macro` 필드이므로 직렬화는 자동이고, 화면이 필터를 **보여주기만** 하면 된다.

## 9. 화면

필터 칸은 **기본 빌더·프로 빌더 공통**이다 — `Builder` 컴포넌트 하나를 둘이 쓰기 때문이고, 필터는 프로 전용 개념이 아니라 규칙의 일부다. 프로 빌더의 차별점은 §3/3(코치)이 담당한다.

- 위치: 조건 판에서 규칙 선택 **아래**, 위험관리 **위**
- 기본값: **꺼짐**. 체크를 켜면 종류를 고르고, 종류에 따라 칸이 바뀐다(규칙별 `params` 칸이 이미 그렇게 동작한다)
- 필터를 못 쓰는 규칙(A·B·C·D)을 고르면 칸이 **사라진다**. 비활성 상태로 남겨 두면 "왜 안 되는지" 를 묻게 된다
- 켜 놓은 상태에서 규칙을 A로 바꾸면 필터를 **버린다**. [`macro.js:165`](../../../frontend/src/lib/macro.js) 가 이미 같은 일을 한다(`K → A` 로 바꿀 때)
- 프런트 검증([`validateDetailed`](../../../frontend/src/lib/macro.js))이 서버와 같은 규칙으로 먼저 막는다

## 10. 사용자에게 보이는 결과

1. 조건 판에 "진입 조건 더 달기" 가 생긴다. 끄면 지금과 완전히 같다
2. `I + 20일 이평선 위` 같은 조합을 만들 수 있다 — 기본 빌더로는 만들 수 없었던 것
3. 필터를 조이면 거래가 줄고, 검증이 "표본이 모자라다"고 말할 수 있다. 맞는 말이다
4. 필터가 걸린 매크로는 실행기 v8+ 가 필요하다 (이미 그런 규칙들이다)
5. A·B·C·D 규칙에서는 필터 칸이 아예 안 보인다

## 11. 무엇을 어떻게 증명하나

| 증명할 것 | 어떻게 |
|---|---|
| 필터가 진입을 **막는다** | 필터 없이 N번 진입하는 봉 열에서, 필터를 걸면 진입이 0 또는 더 적다 |
| 필터가 **청산을 막지 않는다** | 포지션을 들고 있고 필터가 거짓인 상태에서 손절·익절이 그대로 일어난다 |
| **모르면 막는다** | 20봉 이평 필터 + 10봉만 주면 진입이 없다 |
| **미래를 안 본다** | 진입이 일어난 봉의 필터 판정이 **직전 봉** 종가로 계산된 값과 같다 |
| 세이프티오더는 안 막힌다 | H 첫 진입 뒤 필터가 거짓으로 바뀌어도 세이프티오더가 나간다 |
| 금지 규칙이 거부된다 | A·B·C·D + `entry_filter` → `Macro` 검증 `ValueError` |
| 백테스트와 실시간이 같다 | 같은 봉 열을 `warmup`/`on_candle` 로 먹인 두 경로의 체결이 같다 |
| 거래량을 못 받으면 막는다 | `volume=None` 로 봉을 먹인 거래량 필터가 진입을 막는다 |
| 기존 규칙이 안 바뀐다 | `entry_filter=None` 인 기존 테스트 전부 그대로 통과 |

마지막 줄이 제일 중요하다. 기존 심 테스트가 한 줄도 바뀌지 않아야 한다 — 바뀌면 필터가 기존 동작을 건드린 것이다.

## 12. 범위 밖

- 필터 체인(AND·OR)
- 기준 종목 필터 — 2차. `FilterKind` 에 값을 더하는 것으로 끝나도록 모양을 비워 둔다
- 청산 필터
- A·B·C 캔들 심
- D 그리드 필터
- 포트폴리오 묶음 (별도 스펙 2/3)
- 코치 패널 (별도 스펙 3/3)
