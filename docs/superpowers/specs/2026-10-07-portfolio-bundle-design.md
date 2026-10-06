# 포트폴리오 묶음 — 설계 (2026-10-07)

프로 빌더 2차의 **2/3**. 1차는 진입 필터(`2571e0b`), 3차는 코치 패널이다.

## 1. 무엇을 만드는가

지금 "포트폴리오" 는 **같은 규칙을 여러 종목에 똑같이 돌리고 자금을 균등하게 나눈 것** 뿐이다.
전문가가 실제로 짜는 묶음은 그렇지 않다 — 종목마다 비중이 다르고, 종목마다 다른 규칙을 쓰고,
묶음 전체에 "한 번에 세 종목까지" 같은 한도를 건다.

이번에 여는 것 세 가지:

| 것 | 지금 | 뒤 |
|---|---|---|
| 비중 | 종목 수만큼 균등(고정) | 종목마다 1~100% 로 지정, 합 100% |
| 규칙 | 묶음 하나에 규칙 하나 | 레그마다 규칙·세부값·진입 조건을 따로 |
| 묶음 한도 | 없음 | 동시 보유 종목 수 상한 · 총 노출 한도 |

묶음 매크로도 **검증(워크포워드)** 을 할 수 있게 된다. 지금은 422 로 거절한다.

## 2. 지금 어떻게 되어 있나

- `Macro.symbols: list[str]` — 여러 종목. `is_portfolio()` 는 `len > 1`.
  `for_symbol(sym, cap)` 이 자금만 바꾼 단일 종목 복사본을 만든다. `MAX_SYMBOLS = 5`.
- 백테스트(`main.py` ~228): `per_cap = 초기자금 / 종목수` → 레그마다 `run_backtest` 를
  **끝까지** 돌린 뒤 `portfolio.aggregate` 로 합산. 자금 가중 평균 + 자산곡선 합.
- 모의·실시간(`paper.py` ~193): `per_leg = initial / len(symbols)` 로 `Leg` 를 만들고
  `StrategyDriver(legs, initial)`. 드라이버는 **레그 하나씩** `tick(price, ts, symbol)` 을
  받는다 — 실시간은 종목별 피드가 각자 돌기 때문이다.
- 검증: `main.py:1207`·`:1358` 이 묶음 매크로를 422.
- 실행기: 네 곳에서 묶음 매크로를 거절한다(`PORTFOLIO_UNSUPPORTED_DETAIL`,
  `PORTFOLIO_REFUSED_NOTE`). **이번에도 그대로 둔다.**

### 설계에 걸린 자리

**묶음 단위 한도는 지금 두 경로 어느 쪽에서도 표현되지 않는다.** 백테스트는 레그를 끝까지
돌린 뒤 합산하므로 "지금 몇 종목 들고 있나" 를 물을 시점이 없고, 실시간은 레그가 각자
틱을 받으므로 서로를 모른다.

1차가 세운 원칙 4(백테스트와 실시간이 **같은 코드로 같은 답**)를 지키려면 선택이 하나뿐이다:
**묶음 한도를 쓸 때 백테스트를 레그 동기(lockstep) 봉 루프로 바꾼다.** 실시간 쪽은 이미
`StrategyDriver` 가 레그 전부를 한 객체에 들고 있어 공유 상태를 놓을 자리가 있다.

"한도는 모의·실시간에서만" 은 거절한다 — 1차에서 차단 결함(거래량 필터가 실시간에만 막힘)을
낸 바로 그 괴리다.

## 3. 설계 원칙

1차의 다섯 원칙을 **그대로 물려받는다.** 묶음 관문을 `CandleSim._entry_blocked` 에 두기
때문에 공짜로 따라온다 — 그 자리는 이미 "새 진입" 한 곳이고, 청산·안전주문·그리드 보충·
K 의 방어 숏은 그 자리를 지나지 않는다.

1. 관문은 **새 진입만** 막는다. 청산은 건드리지 않는다 — 잘못 걸어도 열린 손실이 커지지 않는다.
2. **모르면 막는다.**
3. 판단은 **직전 마감봉** 기준.
4. 백테스트와 실시간이 **같은 코드로 같은 답**.
5. 정리성 주문(마틴게일 안전주문·그리드 보충·K 방어 숏)은 절대 막지 않는다.

이번에 더하는 것:

6. **비중은 자금 분배일 뿐 한도가 아니다.** 비중 30% 레그는 묶음 초기자금의 30% 를 받고,
   그 뒤로는 자기 자금 안에서 기존 규칙대로 움직인다. 번 돈을 다른 레그가 쓰지 않는다
   (레그 사이 자금 이동 없음 — 지금 동작과 같다).
7. **묶음 한도는 "닿으면 멈춘다" 이지 "넘지 않는다" 가 아니다.** 들어갈 주문의 크기를
   관문이 미리 알 수 없으므로, 이미 한도에 닿았을 때 새 진입을 막는다. 문구도 그렇게 쓴다.
8. **한도를 쓰는 묶음은 모든 레그가 E~K 여야 한다.** A·B(틱 구동)·C(적립식)는 봉 루프에
   들어오지 않고, D(그리드)는 사다리 한 칸을 막으면 짝 없는 매수가 남는다 — 1차가 필터를
   E~K 로 제한한 것과 같은 이유다. 한도 없는 묶음은 규칙을 섞어도 된다.

## 4. 스키마

`schema.py`, `EntryFilter` 블록 뒤 · `class Macro` 앞.

```python
class PortfolioLeg(BaseModel):
    """묶음의 한 다리. 규칙 관련 칸이 None 이면 묶음의 기본값(Macro 자신)을 쓴다."""
    symbol: str
    weight: float = Field(gt=0, le=100)        # 묶음 초기자금 중 이 레그의 몫(%)
    rule_type: Optional[RuleType] = None
    params: Optional[dict] = None              # rule_type 을 바꿨으면 반드시 같이 준다
    entry_filter: Optional[EntryFilter] = None
    # 레그 고유 위험관리는 범위 밖 — 묶음의 risk 를 그대로 물려받는다(§11).


class BundleRisk(BaseModel):
    """묶음 전체에 거는 한도. 둘 중 최소 하나는 있어야 한다."""
    max_positions: Optional[int] = Field(default=None, ge=1, le=5)
    max_exposure_pct: Optional[float] = Field(default=None, gt=0, le=100)

    @model_validator(mode="after")
    def _check(self):
        if self.max_positions is None and self.max_exposure_pct is None:
            raise ValueError("묶음 한도는 최소 하나를 정해야 합니다")
        return self
```

`Macro` 에 두 칸을 더한다 — `entry_filter` 와 `created_at` 사이:

```python
    legs: Optional[list[PortfolioLeg]] = None
    bundle_risk: Optional[BundleRisk] = None
```

`GATEABLE_TYPES` 를 `FILTERABLE_TYPES` 의 별칭으로 둔다(같은 집합, 같은 이유 — 이름만
관문 일반형):

```python
GATEABLE_TYPES = FILTERABLE_TYPES   # 진입 관문을 안전하게 끼울 수 있는 규칙
```

### `Macro._validate` 가 더 보는 것

`symbols` 정규화 **뒤**, 규칙별 params 분기 **앞**:

- `legs` 와 `symbols` 를 **둘 다** 주면 거절: `"symbols 와 legs 는 함께 쓸 수 없습니다"`.
- `legs` 가 있으면: 1개는 거절(`"묶음은 종목 2개 이상이어야 합니다"`), `MAX_SYMBOLS` 초과 거절,
  종목 중복 거절, 종목은 `validate_symbol` 통과. 비중 합이 100 에서 **0.01 이상** 벗어나면
  거절: `"비중의 합이 100% 여야 합니다 (지금 {합:g}%)"`.
- `legs[0].symbol` 을 `self.symbol` 로 맞춘다(슬러그·요약·단일 종목 경로가 그대로 돌게).
- 레그의 `rule_type` 이 묶음과 다르면 `params` 가 반드시 있어야 한다:
  `"레그 {종목}: 규칙을 바꾸면 세부값도 함께 주세요"`. 그 `params` 는 해당 규칙의
  params 모델로 검사하고 `model_dump()` 로 정규화한다 — 묶음 본체와 같은 처리.
- **레그를 펼쳐서 검사한다.** 레그마다 `for_leg` 로 단일 종목 매크로를 만들어 보는 것으로
  검증을 끝낸다 — 규칙별 params·필터 적용 가능 여부·국내 거래소 제약·레버리지까지 `Macro`
  검증기가 이미 다 본다. 레그 검증 로직을 두 벌로 베끼지 않는다. 실패하면 어느 레그인지
  붙여서 올린다: `"레그 {종목} 설정을 확인해 주세요"`.
- **레그가 규칙을 바꾸면 묶음의 `entry_filter` 는 물려받지 않는다** — 그 조건은 다른 규칙을
  위해 쓴 것이다. 그 레그는 자기 `entry_filter` 만 쓴다. 두 곳(`for_leg`·검증)이 같은 답을
  내도록 해석을 한 곳에 둔다:

```python
    def leg_rule(self, leg: "PortfolioLeg") -> RuleType:
        return leg.rule_type if leg.rule_type is not None else self.rule_type

    def leg_filter(self, leg: "PortfolioLeg") -> Optional[EntryFilter]:
        if leg.entry_filter is not None:
            return leg.entry_filter
        if leg.rule_type is not None and leg.rule_type is not self.rule_type:
            return None          # 다른 규칙을 위해 쓴 조건을 물려받지 않는다
        return self.entry_filter
```
- `bundle_risk` 가 있으면 묶음이어야 한다(`"묶음 한도는 종목 2개 이상에서만 쓸 수 있습니다"`),
  그리고 **모든 레그의 실효 규칙이 `GATEABLE_TYPES`** 여야 한다:
  `"묶음 한도는 규칙 {X} 에 쓸 수 없습니다 — E~K 만 지원해요"`.
- `max_positions` 가 레그 수 이상이면 아무 일도 하지 않는 한도이므로 거절:
  `"동시 보유 상한이 종목 수보다 작아야 의미가 있어요"`.

### 편의 접근자

```python
    def leg_specs(self) -> list[PortfolioLeg]:
        """묶음을 레그 목록으로 정규화한다. symbols 형태는 균등 비중으로 변환해 돌려준다."""
        if self.legs:
            return list(self.legs)
        syms = self.all_symbols()
        if len(syms) < 2:
            return []
        w = 100.0 / len(syms)
        return [PortfolioLeg(symbol=s, weight=w) for s in syms]

    def for_leg(self, leg: PortfolioLeg, initial_capital: float) -> "Macro":
        """레그 하나를 단일 종목 매크로로 펼친다. None 칸은 묶음 기본값을 쓴다."""
```

`for_leg` 는 `for_symbol` 과 달리 `rule_type`·`params`·`entry_filter` 덮어쓰기를 반영한다.
`legs`·`bundle_risk`·`symbols` 는 None 으로 지운다. `is_portfolio()` 는
`legs` 도 보게 고친다: `bool(self.legs) or (bool(self.symbols) and len(self.symbols) > 1)`.
`all_symbols()` 는 `legs` 가 있으면 그 종목들을 돌려준다.

## 5. 묶음 관문

새 파일 `engine/bundle.py`. 필터가 `entry_filter.py` 한 곳에 있는 것과 같은 모양이다.

```python
class BundleGate:
    """묶음 한도. 레그 심을 들고 있다가 '지금 새로 들어가도 되나' 를 답한다.

    상태를 따로 세지 않고 **심에게 그때그때 물어본다** — 사건 기록이 어긋날 일이 없다.
    노출은 **진입 기준 명목금액**(수량 × 평균 진입가)으로 센다. 시세를 끌어오지 않으므로
    백테스트와 실시간이 같은 값을 본다(원칙 4).
    """
    def __init__(self, risk: BundleRisk, total_capital: float) -> None: ...
    def register(self, sim) -> None:        # LiveCandleSim 은 .inner 를 벗겨 담는다
    def blocks(self, sim) -> bool:
    def note(self) -> str:
```

`blocks(sim)`:

- `max_positions`: **이 레그가 이미 들고 있으면 통과**(추가 매수는 새 종목이 아니다).
  아니면 `held = 들고 있는 레그 수`; `held >= max_positions` 면 막는다.
- `max_exposure_pct`: `used = Σ(수량 × 평균진입가)`;
  `used >= total_capital × pct / 100` 이면 막는다.
- 한도가 None 인 항목은 보지 않는다.

`note()` → `"한 번에 3종목까지"` / `"총 노출 60% 까지"` / `"한 번에 3종목까지 · 총 노출 60% 까지"`.

### 심에 끼우는 자리

`CandleSim.__init__` 끝에 `self.bundle_gate = None`(기본). `_entry_blocked` 에 한 줄,
필터 바로 뒤:

```python
        if self.bundle_gate is not None and self.bundle_gate.blocks(self):
            return True
```

관문을 **밖에서 꽂는다**(매크로에서 만들지 않는다) — 묶음을 아는 것은 심이 아니라 묶음을
조립하는 쪽(백테스트 루프·`paper.start_session`)이기 때문이다.

## 6. 백테스트 — 레그 동기 루프

새 파일 `engine/portfolio_backtest.py`.

**갈림길은 `bundle_risk` 다:**

- `bundle_risk is None` → **지금 경로 그대로**(레그마다 `run_backtest` 끝까지 → `aggregate`).
  기존 묶음 매크로의 결과가 한 바이트도 바뀌지 않는다. 비중과 레그별 규칙은 `for_leg` 가
  만든 매크로를 넘기기만 하면 되므로 이 경로에서 공짜로 된다.
- `bundle_risk` 가 있으면 → **동기 루프**.

```python
def run_bundle(macro: Macro, frames: dict[str, pd.DataFrame]) -> list[tuple[str, BacktestResult]]:
    """레그를 봉 단위로 나란히 돌린다. 반환값은 aggregate() 가 먹는 모양 그대로."""
```

- 레그마다 `for_leg` 로 매크로를 펼치고 `per_cap = 초기자금 × weight / 100` 를 준다.
- `make_candle_sim` 으로 심을 만들고 `BundleGate` 에 `register` → `sim.bundle_gate = gate`.
- 시각은 **모든 레그의 timestamp 합집합** 을 정렬해 쓴다. 레그는 **자기가 가진 봉에서만**
  `on_candle` 을 받는다(상장 시점이 다른 종목을 잘라내지 않는다).
- 한 시각 안에서 레그 순서는 **`legs` 에 적힌 순서**다. 한도가 걸릴 때 앞 레그가 자리를
  먼저 잡는다 — 결정적이고, 사용자가 순서를 바꿔 뜻을 표현할 수 있다. 문서에 적는다.
- 자산곡선은 레그마다 자기 봉에서만 점을 찍는다. `aggregate` 가 이미 앞 값으로 메꾼다.
- 레그별 결과는 `_metrics` 를 그대로 재사용해 만든다(`buy_hold_return_pct` 는 그 레그의 종가).

`main.py` 의 묶음 분기가 이 둘을 고른다. `aggregate` 호출과 응답 모양은 바뀌지 않는다.

**동기 루프가 옳다는 증명:** 한도를 "사실상 없음"(`max_exposure_pct = 100`)으로 두면
두 경로의 결과가 같아야 한다. 이것을 시험으로 못 박는다.

## 7. 실시간 · 모의

`paper.start_session`: 레그를 만든 **뒤**, 묶음이고 `bundle_risk` 가 있으면 관문 하나를
만들어 레그 심 전부를 등록하고 각 심에 꽂는다. `_Runner`·`StrategyDriver` 는 바뀌지 않는다 —
레그가 한 객체 안에 있으므로 공유 상태가 자연히 성립한다.

자금 분배도 `per_leg = initial / len(symbols)` 에서 `initial × weight / 100` 으로 바꾼다.

**재기동 복구**: 관문은 심의 현재 장부를 그때그때 읽으므로 따로 복원할 상태가 없다.
`driver.restore` 가 레그 장부를 되살리면 관문은 그 즉시 올바른 답을 낸다. 1차에서
복구 경로를 빠뜨려 차단 결함을 냈으므로 **이것을 시험으로 못 박는다.**

실행기는 그대로 거절한다. `RUNNER_VERSION` 은 `"10"` 에서 움직이지 않는다.

## 8. 서명 버전

`Macro` 에 칸이 늘면 `canonical_bytes` 의 결과가 **모든** 매크로에서 바뀐다 — 1차에서
이것을 놓쳐 서명된 파일 전부가 "수정된 파일" 이 될 참이었다.

`SIG_VERSION = 4`. `canonical_bytes` 가 `version < 4` 일 때 `legs`·`bundle_risk` 를 뺀다.
`ACCEPTED_SIG_VERSIONS` 는 이미 범위(`range(1, SIG_VERSION + 1)`)라 저절로 따라온다.
`verify` 는 `version < 4` 인데 `legs` 나 `bundle_risk` 가 있으면 거절한다(v3 서명으로
묶음 매크로를 통과시킬 수 없게).

v1·v2·v3 의 **고정 바이트 시험**을 더한다(1차가 만든 방식 그대로 — `model_dump` 에서
되만드는 시험은 이 결함을 못 잡는다).

## 9. API · 검증

- `POST /api/backtest` 묶음 분기: `leg_specs()` 로 레그를 돌며 `for_leg` 매크로로
  데이터를 받고, `bundle_risk` 유무로 두 경로를 고른다.
- **검증을 연다.** `main.py:1207`·`:1358` 의 422 를 없애고, 워크포워드 창을 레그마다
  같은 시각으로 자른다. 한도가 있으면 창마다 동기 루프를 돌린다 — 그래야 검증이
  백테스트와 같은 답을 낸다. `PORTFOLIO_MESSAGE` 상수와 그 프런트 분기를 지운다.
- `realtrade_macro_file`(~2307)·실행기 티켓의 묶음 거절은 **그대로**.
- 레그 수만큼 캔들을 받으므로 기존 `_validate_limiter` 를 그대로 쓰고, 레그 상한은
  `MAX_SYMBOLS = 5` 가 이미 막는다.

## 10. 화면

`Builder.jsx` 의 `SymbolPicker` 가 이미 **비중 칸을 가진 행 목록**을 그린다(`bd-symrow-w`,
지금은 `portfolioWeight(n)` 로 균등값을 보여 주기만 한다). 그 칸을 입력으로 바꾸는 것이
이 작업의 뼈대다.

- 종목 행의 비중이 **입력**이 된다. 종목을 넣으면 남은 몫을 균등하게 다시 나눈다.
  합이 100 이 아니면 행 목록 아래 한 줄: `"비중의 합이 {합:g}% 예요 · 100% 로 맞춰 주세요"`.
  `"균등하게"` 버튼 하나.
- 레그마다 `"규칙 바꾸기"` 를 펼치면 그 레그의 규칙·세부값·진입 조건을 따로 정한다.
  **프로 빌더에서만** 보인다(기본 빌더는 지금 모양 그대로 — 균등·단일 규칙).
- 묶음 한도는 위험관리 블록 **안**에, 묶음일 때만: `"동시 보유 종목 수"`,
  `"총 노출 한도(%)"`. 라벨에 `"묶음"` 을 붙여 레그 위험관리와 섞이지 않게 한다.
- `macro.js`: 폼 키(`leg_weights`, `leg_rules`, `use_bundle_risk`, `bundle_max_positions`,
  `bundle_max_exposure_pct`), `buildLegs(form)`, `buildBundleRisk(form)`,
  `macroToForm` 왕복, `validateDetailed` 에 비중 합·정수·범위.
- `MacroCard.macroFacts`: 균등일 때는 지금 문구 그대로, 비중이 다르면
  `"BTC 50% · ETH 30% · SOL 20%"`. 한도가 있으면 `"묶음 한도"` 행 하나.
- 서버 `human_summary`: 비중과 한도를 한 줄씩 붙인다. 1차에서 배운 것 —
  요약 끝에 붙은 문구는 모든 화면에서 **가장 먼저 잘린다.** 그래서 구조화된 칸이 본진이고
  요약은 보조다.

## 11. 범위 밖

- **레그 고유 위험관리**(레그마다 다른 손절·보유기간). 레그는 묶음의 `risk` 를 물려받는다.
  규칙과 진입 조건까지 열면 이번 분량이 이미 크다.
- **레그 사이 자금 이동 · 리밸런싱.** 비중은 시작 시점의 분배다.
- **기준 종목 필터**(BTC 가 이동평균 위일 때만 알트 진입) — 1차에서 2차로 미룬 항목이지만,
  레그 간 참조는 동기 루프가 생긴 뒤에야 정직하게 된다. 3차 이후.
- **실행기 실거래.** 네 곳의 거절을 유지한다.
- **A·B·C·D 레그에 묶음 한도.** §3-8 이 거절한다.
- 숫자 칸 정수 검사·모순 조합 경고 등 1차 보류 항목은 그 문서에 남겨 둔다.

## 12. 검토에서 미리 막은 것

1차가 가르친 것을 이번 설계에 선반영한 자리들 — 구현 때 이 줄을 근거로 삼는다.

1. **매크로 칸 추가 = 모든 서명 변경.** §8. 고정 바이트 시험까지 같이.
2. **복구 경로.** 관문은 상태를 따로 들지 않아 복구가 공짜다. 그래도 §7 에서 시험으로 못 박는다.
3. **실시간이 그 데이터를 싣고 오나.** 관문이 쓰는 값(수량·평균진입가)은 심이 이미 들고 있다.
   새로 실어 올 데이터가 없다 — 1차의 거래량 결함과 달리 이 축은 비어 있다.
4. **관문은 청산을 건드리지 않는다.** `_entry_blocked` 한 자리에만 꽂는다.
5. **두 경로가 같은 답을 내는지.** §6 의 "한도 사실상 없음" 대조 시험.
6. **문구가 화면에서 잘린다.** §10 — 구조화 칸이 본진.
