# 껄무새에게 물어볼까? — 단타형 (v1.1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 성향 카드에 네 번째 **단타형(`scalper`)** 을 더해, 고르면 기간(1주/1개월)·봉(1분/5분/15분) 선택지가 짧은 쪽으로 바뀌고 전용 프리셋으로 후보를 만든다.

**Architecture:** 백엔드는 `PROFILES` 에 성향별 메타(`short`, `max_symbols`, `min_trades`)를 붙이고 `AskRequest` 검증으로 봉×기간 짝(1분 봉은 1주만)과 종목 상한을 강제한다. 프론트는 `askFlow.js` 에 성향별 옵션 함수(`periodOptions`, `intervalOptions`, `maxSymbols`)를 두고 리듀서가 그 목록으로 검증한다. 대화창은 그 함수로 칩을 그린다. 기존 세 성향의 동작은 바뀌지 않는다.

**Tech Stack:** FastAPI + pydantic v2 · React 18 + Vite · pytest / `node --test`

**Spec:** `docs/superpowers/specs/2026-09-18-ask-parrot-design.md` §9 (단타형)

## Global Constraints

- 봉 × 기간 짝: 단타형은 interval ∈ {1m,5m,15m}, period ∈ {1w,1m}, **`1m` 봉이면 `period == "1w"`**; 그 외 성향은 interval ∈ {1h,4h,1d}, period ∈ {3m,6m,1y}. 위반은 422 (백엔드) / 거부 (리듀서)
- 단타형 종목 최대 **2개**, 다른 성향 3개
- 단타형 유형 `("A","E","F","G","J")`, `mdd_cap None`, `min_trades 10`, `futures True`; 점수는 수익률만
- 새 기간 프리셋을 만들지 않는다 (`PERIOD_PRESET_DAYS` 의 `1w`, `1m` 사용)
- 응답·기록·한도·동의·고지 문구(`DISCLAIMER`, 버전 `ask-v1`)는 그대로. "추천" 금지
- 단타형 결과에만 `SCALPER_NOTE` 한 줄 추가: `짧은 봉은 수수료·슬리피지 영향이 커요 · 실행기보다 페이퍼 트레이딩으로 먼저 확인해요`; 모든 결과 카드에 `수수료 {c}% · 슬리피지 {s}% 포함`
- 커밋 메시지 한국어, 끝에 `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`; 커밋 작성자는 `-c user.name="노희재" -c user.email="hsrohsro1234@gmail.com"` 로 (저장소 기본값이 이미 노희재이므로 확인만)
- 테스트: 백엔드 `backend/.venv/Scripts/python -m pytest backend/tests/test_ask.py -q -p no:warnings` (repo 루트), 프론트 `cd frontend && node --test "tests/*.test.js"`, 빌드 `npx vite build --logLevel error`
- 긴 파일은 Write 도구로; Bash 인자 길이 제한 주의

## File Map

| 파일 | 변경 |
|---|---|
| `backend/app/ask.py` (modify) | `PROFILES` 메타, `_SCALPER_PRESETS`, `_presets_for`, `AskRequest` 검증, `select_top` 최소 거래 수 |
| `backend/tests/test_ask.py` (modify) | 단타형 테스트 7개 |
| `frontend/src/lib/askCopy.js` (modify) | scalper 프로필, 짧은 기간/봉 옵션, 문구 |
| `frontend/src/lib/askFlow.js` (modify) | 옵션 함수, 검증, followUp 규칙 |
| `frontend/tests/askFlow.test.js` (modify) | 단타형 테스트 4개 |
| `frontend/src/components/AskParrotDialog.jsx` (modify) | 성향별 칩, 종목 상한, 수수료·단타 안내 |

---

### Task 1: 백엔드 — 단타형 성향·프리셋·검증

**Files:**
- Modify: `backend/app/ask.py` (PROFILES ~L41-45, AskRequest ~L55-88, `_PRESETS` 뒤, `_allowed_types`/`build_templates` ~L131-200, `select_top` ~L237-244)
- Test: `backend/tests/test_ask.py` (현재 14개 테스트; 끝에 추가)

**Interfaces:**
- Produces: `PROFILES[...]["short"|"max_symbols"|"min_trades"]`, `_SCALPER_PRESETS`, `_presets_for(req) -> dict[str, list[dict]]`, `AskRequest` accepting `interval` `1m|5m|15m` and `period_preset` `1w|1m` under the pairing rules
- Frontend (Task 2/3) sends exactly `{risk_profile:"scalper", period_preset:"1w"|"1m", interval:"1m"|"5m"|"15m", symbols:[≤2]}`

- [ ] **Step 1: Write the failing tests** (append to `backend/tests/test_ask.py`)

```python
def test_scalper_profile_candidates_use_short_presets_and_skip_slow_types():
    req = ask.AskRequest(risk_profile="scalper", symbols=["BTCUSDT"], period_preset="1w", interval="5m")
    cands = ask.build_templates(req)
    types = {c.macro.rule_type.value for c in cands}
    assert types == {"A", "E", "F", "G", "J"}
    a_first = next(c for c in cands if c.macro.rule_type.value == "A")
    assert a_first.macro.params["take_profit_pct"] == 1.0 and a_first.macro.risk.stop_loss_pct == 0.7
    j_first = next(c for c in cands if c.macro.rule_type.value == "J")
    assert (j_first.macro.params["ma_type"], j_first.macro.params["fast_period"], j_first.macro.params["slow_period"]) == ("EMA", 5, 13)
    assert all(c.macro.candle_interval == "5m" and c.macro.period.preset == "1w" for c in cands)
    # 기존 성향은 그대로 긴 프리셋
    slow = ask.build_templates(ask.AskRequest(risk_profile="aggressive", symbols=["BTCUSDT"]))
    assert next(c for c in slow if c.macro.rule_type.value == "A").macro.params["take_profit_pct"] == 3


def test_scalper_request_pairs_interval_and_period():
    ok = ask.AskRequest(risk_profile="scalper", symbols=["BTCUSDT"], period_preset="1w", interval="1m")
    assert (ok.interval, ok.period_preset) == ("1m", "1w")
    ask.AskRequest(risk_profile="scalper", symbols=["BTCUSDT", "ETHUSDT"], period_preset="1m", interval="15m")
    with pytest.raises(ValidationError, match="1분 봉"):
        ask.AskRequest(risk_profile="scalper", symbols=["BTCUSDT"], period_preset="1m", interval="1m")
    with pytest.raises(ValidationError):
        ask.AskRequest(risk_profile="scalper", symbols=["BTCUSDT"], period_preset="3m", interval="5m")
    with pytest.raises(ValidationError):
        ask.AskRequest(risk_profile="scalper", symbols=["BTCUSDT"], period_preset="1w", interval="1h")
    with pytest.raises(ValidationError, match="2개"):
        ask.AskRequest(risk_profile="scalper", symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"], period_preset="1w", interval="5m")


def test_non_scalper_request_rejects_short_options():
    with pytest.raises(ValidationError):
        ask.AskRequest(risk_profile="aggressive", symbols=["BTCUSDT"], period_preset="1w", interval="1h")
    with pytest.raises(ValidationError):
        ask.AskRequest(risk_profile="balanced", symbols=["BTCUSDT"], period_preset="3m", interval="5m")
    # 기본값은 그대로 유효
    assert ask.AskRequest(risk_profile="stable", symbols=["BTCUSDT"]).interval == "1h"


def test_select_top_uses_profile_min_trades():
    def cand(rule_type):
        req = ask.AskRequest(risk_profile="scalper", symbols=["BTCUSDT"], period_preset="1w", interval="5m")
        return ask.Candidate(f"{rule_type}", ask._make_macro(req, rule_type, ask._SCALPER_PRESETS[rule_type][0], ["BTCUSDT"]), "template")
    ev = [ask.Evaluated(cand("A"), _result(5, 2, trades=9)), ask.Evaluated(cand("J"), _result(3, 2, trades=10))]
    assert [e.candidate.macro.rule_type.value for e in ask.select_top(ev, "scalper")] == ["J"]
    assert [e.candidate.macro.rule_type.value for e in ask.select_top(ev, "aggressive")] == ["A", "J"]


def test_scalper_ask_end_to_end(_fake_backtest):
    token, _ = _signup()
    client.post("/api/ask/consent", headers=_auth(token))
    body = {"risk_profile": "scalper", "market": "spot", "leverage": 1, "symbols": ["BTCUSDT"], "period_preset": "1w", "interval": "1m"}
    res = client.post("/api/ask/macros", json=body, headers=_auth(token))
    assert res.status_code == 200, res.text
    data = res.json()
    # _RETURNS: 거래 수 A 8·J 6·G 5·F 2·E 4 → 전부 10 미만이라 단타형 최소 거래 수에 걸려 결과 없음
    assert data["results"] == [] and data["candidate_count"] > 0
    assert all(r["macro"]["candle_interval"] == "1m" for r in data["results"])
    bad = dict(body, period_preset="1m")
    assert client.post("/api/ask/macros", json=bad, headers=_auth(token)).status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_ask.py -q -p no:warnings`
Expected: 5 new tests FAIL (ValidationError on `risk_profile="scalper"` — not in the Literal), 14 existing pass

- [ ] **Step 3: Implement**

`RiskProfile` and `PROFILES`:

```python
RiskProfile = Literal["stable", "balanced", "aggressive", "scalper"]

# 성향별 규칙 — 후보에 넣는 유형, MDD 상한, 선물 허용, 짧은 봉 여부(short), 종목 상한, 최소 거래 수.
# 순서는 템플릿 우선순위. 단타형은 짧은 봉 전용이라 I(일봉 논리)·C·H(짧은 봉에 의미 없음)를 뺀다.
PROFILES: dict[str, dict] = {
    "stable": {"label": "안정형", "mdd_cap": 10.0, "rule_types": ("C", "J", "G", "A"), "futures": False,
               "short": False, "max_symbols": 3, "min_trades": 3},
    "balanced": {"label": "균형형", "mdd_cap": 20.0, "rule_types": ("C", "J", "G", "A", "F", "E"), "futures": True,
                 "short": False, "max_symbols": 3, "min_trades": 3},
    "aggressive": {"label": "공격형", "mdd_cap": None, "rule_types": ("C", "J", "G", "A", "F", "E", "I", "H"), "futures": True,
                   "short": False, "max_symbols": 3, "min_trades": 3},
    "scalper": {"label": "단타형", "mdd_cap": None, "rule_types": ("A", "E", "F", "G", "J"), "futures": True,
                "short": True, "max_symbols": 2, "min_trades": 10},
}

# 봉 × 기간 짝 — 백테스트 봉 상한(MAX_BACKTEST_BARS=20,000)을 넘지 않는 조합만.
LONG_INTERVALS = ("1h", "4h", "1d")
LONG_PERIODS = ("3m", "6m", "1y")
SHORT_INTERVALS = ("1m", "5m", "15m")
SHORT_PERIODS = ("1w", "1m")
```

`AskRequest` fields and validator:

```python
    period_preset: Literal["3m", "6m", "1y", "1w", "1m"] = "3m"
    interval: Literal["1h", "4h", "1d", "1m", "5m", "15m"] = "1h"
```

`_normalize_symbols`: keep, but drop the dead `if len(seen) > 3` block (the profile validator below checks the per-profile cap).

`_profile_rules` (replace whole method):

```python
    @model_validator(mode="after")
    def _profile_rules(self) -> "AskRequest":
        profile = PROFILES[self.risk_profile]
        if self.market == "futures" and not profile["futures"]:
            raise ValueError("안정형은 현물만 살펴봐요")
        if self.market == "spot" and self.leverage != 1:
            raise ValueError("현물은 레버리지를 쓸 수 없어요")
        if len(self.symbols) > profile["max_symbols"]:
            raise ValueError(f"{profile['label']}은 종목을 최대 {profile['max_symbols']}개까지 살펴봐요")
        if profile["short"]:
            if self.interval not in SHORT_INTERVALS or self.period_preset not in SHORT_PERIODS:
                raise ValueError("단타형은 1분·5분·15분 봉으로 최근 1주 또는 1개월만 살펴봐요")
            if self.interval == "1m" and self.period_preset != "1w":
                raise ValueError("1분 봉은 최근 1주까지만 살펴봐요")
        elif self.interval not in LONG_INTERVALS or self.period_preset not in LONG_PERIODS:
            raise ValueError("이 성향은 1시간·4시간·하루 봉으로 최근 3개월 이상을 살펴봐요")
        return self
```

Presets — add after `_PRESETS`:

```python
# 단타형 전용 프리셋 — 짧은 봉에 맞춘 좁은 익절·손절·지표 기간.
_SCALPER_PRESETS: dict[str, list[dict]] = {
    "A": [
        {"params": {"take_profit_pct": 1.0, "initial_capital": CAPITAL}, "risk": {"stop_loss_pct": 0.7}},
        {"params": {"take_profit_pct": 1.5, "initial_capital": CAPITAL}, "risk": {"stop_loss_pct": 1.0}},
    ],
    "E": [
        {"params": {"entry_mode": "immediate", "activation_profit": 1.5, "trail_percent": 0.8, "initial_capital": CAPITAL}},
        {"params": {"entry_mode": "dip", "entry_dip": 1.0, "activation_profit": 1.2, "trail_percent": 0.6, "initial_capital": CAPITAL}},
    ],
    "F": [
        {"params": {"rsi_period": 7, "entry_threshold": 25, "exit_threshold": 75, "initial_capital": CAPITAL}},
        {"params": {"rsi_period": 14, "entry_threshold": 30, "exit_threshold": 70, "exit_mode": "both", "take_profit": 1.5, "initial_capital": CAPITAL}},
    ],
    "G": [
        {"params": {"bb_period": 20, "bb_std": 2.0, "strategy": "reversion", "exit_target": "mid", "initial_capital": CAPITAL}},
        {"params": {"bb_period": 20, "bb_std": 2.5, "strategy": "reversion", "exit_target": "opposite", "initial_capital": CAPITAL}},
    ],
    "J": [
        {"params": {"ma_type": "EMA", "fast_period": 5, "slow_period": 13, "initial_capital": CAPITAL}},
        {"params": {"ma_type": "EMA", "fast_period": 9, "slow_period": 21, "initial_capital": CAPITAL}},
    ],
}


def _presets_for(req: AskRequest) -> dict[str, list[dict]]:
    return _SCALPER_PRESETS if PROFILES[req.risk_profile]["short"] else _PRESETS
```

`build_templates`: at the top add `presets = _presets_for(req)` and replace every `_PRESETS[...]` inside the function with `presets[...]` (three places + `depth`).

`select_top`: replace `MIN_TRADES` with `PROFILES[profile]["min_trades"]`:

```python
    min_trades = PROFILES[profile]["min_trades"]
    pool = [
        e for e in evaluated
        if e.result.total_trades >= min_trades and (cap is None or float(e.result.mdd_pct) <= cap)
    ]
```

Keep the module constant `MIN_TRADES = 3` (other code/tests may import it) but it is no longer read by `select_top`.

`_ai_system`: no change needed (`_allowed_types` already follows the profile); `prompt` already includes `봉 간격`.

- [ ] **Step 4: Run tests**

Run: `backend/.venv/Scripts/python -m pytest backend/tests/test_ask.py backend/tests/test_quests.py -q -p no:warnings`
Expected: 19 passed in test_ask.py (14 + 5), quests green

- [ ] **Step 5: Commit**

```bash
git add backend/app/ask.py backend/tests/test_ask.py
git commit -m "껄무새에게 물어볼까? 단타형: 짧은 봉·기간 짝 검증, 전용 프리셋, 성향별 최소 거래 수

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: 프론트 — 성향별 옵션과 리듀서 규칙

**Files:**
- Modify: `frontend/src/lib/askCopy.js`
- Modify: `frontend/src/lib/askFlow.js`
- Test: `frontend/tests/askFlow.test.js` (현재 6개; 끝에 추가)

**Interfaces:**
- Produces (askCopy): `PROFILES` (+scalper), `SHORT_PERIODS`, `SHORT_INTERVALS`, `ONE_MINUTE_NEEDS_WEEK`, `SCALPER_NOTE`, `feesNote(commission, slippage)`, `symbolsPrompt(max)`; `STEP_PROMPTS.symbols` stays as the 3-symbol default text
- Produces (askFlow): `isShort(profile)`, `maxSymbols(profile)`, `periodOptions(profile)`, `intervalOptions(profile, period)` → `[{value,label,hint,disabled?,title?}]`, `PROFILE_ORDER` exported

- [ ] **Step 1: Write the failing tests** (append to `frontend/tests/askFlow.test.js`; add `periodOptions, intervalOptions, maxSymbols, isShort` to the import line)

```js
test("scalper unlocks short periods and intervals, and 1m needs 1w", () => {
  assert.deepEqual(periodOptions("aggressive").map((o) => o.value), ["3m", "6m", "1y"]);
  assert.deepEqual(periodOptions("scalper").map((o) => o.value), ["1w", "1m"]);
  assert.deepEqual(intervalOptions("balanced", "3m").map((o) => o.value), ["1h", "4h", "1d"]);
  const week = intervalOptions("scalper", "1w");
  assert.deepEqual(week.map((o) => [o.value, !!o.disabled]), [["1m", false], ["5m", false], ["15m", false]]);
  const month = intervalOptions("scalper", "1m");
  assert.deepEqual(month.map((o) => [o.value, !!o.disabled]), [["1m", true], ["5m", false], ["15m", false]]);
  assert.match(month[0].title, /1주/);
  assert.equal(isShort("scalper"), true);
  assert.equal(maxSymbols("scalper"), 2);
  assert.equal(maxSymbols("stable"), 3);
});

test("scalper flow validates against its own option lists", () => {
  let s = walk({ type: "choose", step: "profile", value: "scalper" }, { type: "choose", step: "market", value: { market: "spot", leverage: 1 } });
  for (const sym of ["BTCUSDT", "ETHUSDT", "SOLUSDT"]) s = reduce(s, { type: "toggleSymbol", symbol: sym });
  assert.deepEqual(s.answers.symbols, ["BTCUSDT", "ETHUSDT"]);  // 단타형은 2개까지
  s = reduce(s, { type: "confirmSymbols" });
  assert.equal(reduce(s, { type: "choose", step: "period", value: "3m" }), s);  // 긴 기간 거부
  s = reduce(s, { type: "choose", step: "period", value: "1m" });
  assert.equal(reduce(s, { type: "choose", step: "interval", value: "1m" }), s);  // 1개월 + 1분 거부
  assert.equal(reduce(s, { type: "choose", step: "interval", value: "1h" }), s);  // 긴 봉 거부
  s = reduce(s, { type: "choose", step: "interval", value: "5m" });
  assert.equal(s.phase, "ready");
  assert.deepEqual(toRequest(s.answers), { risk_profile: "scalper", market: "spot", leverage: 1, symbols: ["BTCUSDT", "ETHUSDT"], period_preset: "1m", interval: "5m" });
  // 기존 성향은 짧은 옵션을 거부
  const slow = walk({ type: "choose", step: "profile", value: "balanced" }, { type: "choose", step: "market", value: { market: "spot", leverage: 1 } }, { type: "toggleSymbol", symbol: "BTCUSDT" }, { type: "confirmSymbols" });
  assert.equal(reduce(slow, { type: "choose", step: "period", value: "1w" }), slow);
});

test("riskier from aggressive becomes scalper and clears period/interval (and oversized symbols)", () => {
  let s = walk(
    { type: "choose", step: "profile", value: "aggressive" }, { type: "choose", step: "market", value: { market: "futures", leverage: 2 } },
    { type: "toggleSymbol", symbol: "BTCUSDT" }, { type: "toggleSymbol", symbol: "ETHUSDT" }, { type: "toggleSymbol", symbol: "SOLUSDT" }, { type: "confirmSymbols" },
    { type: "choose", step: "period", value: "3m" }, { type: "choose", step: "interval", value: "1h" },
  );
  s = reduce(s, { type: "results", results: [], remaining: 3 });
  const r = reduce(s, { type: "followUp", kind: "riskier" });
  assert.equal(r.answers.profile, "scalper");
  assert.equal(r.answers.market, "futures");  // 시장은 유지
  assert.deepEqual(r.answers.symbols, []);     // 3개 > 2개 상한 → 비움
  assert.equal(r.answers.period, null);
  assert.equal(r.answers.interval, null);
  assert.equal(r.step, "symbols");
  assert.equal(r.phase, "cards");
  assert.equal(reduce(r, { type: "followUp", kind: "riskier" }), r);  // results phase 아님 → no-op
});

test("safer from scalper becomes aggressive, keeps ≤2 symbols, clears period/interval", () => {
  let s = walk(
    { type: "choose", step: "profile", value: "scalper" }, { type: "choose", step: "market", value: { market: "spot", leverage: 1 } },
    { type: "toggleSymbol", symbol: "BTCUSDT" }, { type: "confirmSymbols" },
    { type: "choose", step: "period", value: "1w" }, { type: "choose", step: "interval", value: "1m" },
  );
  s = reduce(s, { type: "results", results: [], remaining: 3 });
  const r = reduce(s, { type: "followUp", kind: "safer" });
  assert.equal(r.answers.profile, "aggressive");
  assert.deepEqual(r.answers.symbols, ["BTCUSDT"]);
  assert.equal(r.answers.symbolsConfirmed, true);
  assert.equal(r.answers.period, null);
  assert.equal(r.step, "period");
  assert.equal(r.phase, "cards");
  assert.equal(answerLabel("interval", { ...s.answers }), "초단타 (1분 봉)");
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && node --test tests/askFlow.test.js`
Expected: 4 new FAIL (`periodOptions` is not exported), 6 pass

- [ ] **Step 3: `askCopy.js` changes**

```js
export const PROFILES = [
  { value: "stable", label: "안정형", hint: "-10%까지" },
  { value: "balanced", label: "균형형", hint: "-20%까지" },
  { value: "aggressive", label: "공격형", hint: "제한 없음" },
  { value: "scalper", label: "단타형", hint: "짧은 봉, 빠르게 · 제한 없음" },
];
```

Keep `PERIODS`/`INTERVALS` as they are (긴 성향용) and add:

```js
// 단타형 — 백테스트 봉 상한(20,000)을 넘지 않는 짧은 짝만. 1분 봉은 최근 1주까지.
export const SHORT_PERIODS = [
  { value: "1w", label: "최근 1주" },
  { value: "1m", label: "최근 1개월" },
];
export const SHORT_INTERVALS = [
  { value: "1m", label: "초단타", hint: "1분 봉" },
  { value: "5m", label: "단타", hint: "5분 봉" },
  { value: "15m", label: "빠르게", hint: "15분 봉" },
];
export const ONE_MINUTE_NEEDS_WEEK = "1분 봉은 최근 1주까지만 살펴봐요";
export const SCALPER_NOTE = "짧은 봉은 수수료·슬리피지 영향이 커요 · 실행기보다 페이퍼 트레이딩으로 먼저 확인해요";
export const feesNote = (commission, slippage) => `수수료 ${commission}% · 슬리피지 ${slippage}% 포함`;
export const symbolsPrompt = (max) => `어떤 종목이 궁금해요? (최대 ${max}개)`;
```

- [ ] **Step 4: `askFlow.js` changes**

Imports: add `SHORT_INTERVALS, SHORT_PERIODS, ONE_MINUTE_NEEDS_WEEK` to the import from `./askCopy.js`.

Replace `const PROFILE_ORDER = [...]` and `MAX_SYMBOLS` with:

```js
export const PROFILE_ORDER = ["stable", "balanced", "aggressive", "scalper"];
export const MAX_SYMBOLS = 3;  // 긴 성향 기본값 — 실제 상한은 maxSymbols(profile)

export function isShort(profile) { return profile === "scalper"; }
export function maxSymbols(profile) { return isShort(profile) ? 2 : MAX_SYMBOLS; }
export function periodOptions(profile) { return isShort(profile) ? SHORT_PERIODS : PERIODS; }
// 1분 봉은 최근 1주까지만 — 기간이 1개월이면 1분 칩을 비활성으로 돌려준다.
export function intervalOptions(profile, period) {
  if (!isShort(profile)) return INTERVALS;
  return SHORT_INTERVALS.map((o) => (o.value === "1m" && period === "1m" ? { ...o, disabled: true, title: ONE_MINUTE_NEEDS_WEEK } : o));
}
```

Reducer edits:
- `choose period`: `if (!periodOptions(answers.profile).some((p) => p.value === value)) return state;`
- `choose interval`: `const opt = intervalOptions(answers.profile, answers.period).find((i) => i.value === value); if (!opt || opt.disabled) return state;`
- `toggleSymbol`: `if (!has && answers.symbols.length >= maxSymbols(answers.profile)) return state;`
- `followUp safer/riskier` — replace the block after `const profile = PROFILE_ORDER[nextIdx];`:

```js
        const next = { ...answers, profile };
        if (profile === "stable" && next.market === "futures") { next.market = "spot"; next.leverage = 1; }
        // 단타형 ↔ 그 외는 기간·봉 선택지가 달라 답을 지운다. 종목이 새 상한을 넘으면 종목도 지운다.
        if (isShort(profile) !== isShort(answers.profile)) { next.period = null; next.interval = null; }
        if (next.symbols.length > maxSymbols(profile)) { next.symbols = []; delete next.symbolsConfirmed; }
        return { ...settle(state, next), results: null };
```

`answerLabel`: `period` and `interval` must search both lists: `[...PERIODS, ...SHORT_PERIODS].find(...)` and `[...INTERVALS, ...SHORT_INTERVALS].find(...)`.

- [ ] **Step 5: Run tests**

Run: `cd frontend && node --test tests/askFlow.test.js` → 10 passed; then `node --test "tests/*.test.js"` → all pass

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/askCopy.js frontend/src/lib/askFlow.js frontend/tests/askFlow.test.js
git commit -m "껄무새에게 물어볼까? 단타형 프론트: 성향별 기간·봉 옵션, 종목 상한 2, followUp 규칙

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: 대화창 — 성향별 칩·안내 + 브라우저 확인

**Files:**
- Modify: `frontend/src/components/AskParrotDialog.jsx` (`Chips` ~L46-55, `SymbolsCard` ~L58-108, `StepCard` ~L112-128, `ResultCard`, results block ~L270-295, prompt bubbles L248/L258)

**Interfaces:**
- Consumes: Task 2 exports (`periodOptions`, `intervalOptions`, `maxSymbols`, `isShort`, `SCALPER_NOTE`, `feesNote`, `symbolsPrompt`)

- [ ] **Step 1: `Chips` supports per-option disabled/title**

```jsx
function Chips({ options, value, onPick, label }) {
  return (
    <div className="ask-chips" role="group" aria-label={label}>
      {options.map((opt) => (
        <button key={opt.value} type="button" className="ask-chip t-small" aria-pressed={value === opt.value}
          disabled={!!opt.disabled} title={opt.title} onClick={() => onPick(opt.value)}>
          {opt.label}{opt.hint ? <span className="ask-chip-hint">{opt.hint}</span> : null}
        </button>
      ))}
      {options.some((opt) => opt.disabled && opt.title) ? <span className="t-caption text-slate-500">{options.find((opt) => opt.disabled && opt.title).title}</span> : null}
    </div>
  );
}
```

- [ ] **Step 2: Profile-aware cards**

- `StepCard` period: `<Chips options={periodOptions(answers.profile)} .../>`; interval: `<Chips options={intervalOptions(answers.profile, answers.period)} .../>`.
- `SymbolsCard`: replace every `MAX_SYMBOLS` with `maxSymbols(answers.profile)` (`full`, `disabled` checks).
- Prompt bubbles: where `STEP_PROMPTS[s]` / `STEP_PROMPTS[state.step]` are rendered, use a helper `const promptFor = (step) => (step === "symbols" ? symbolsPrompt(maxSymbols(state.answers.profile)) : STEP_PROMPTS[step]);` and render `promptFor(s)` / `promptFor(state.step)`.
- Remove the now-unused `MAX_SYMBOLS` import if nothing else uses it.

- [ ] **Step 3: Results notes**

In `ResultCard`, right after the `<dl className="ask-card-metrics">…</dl>`, add:

```jsx
      <p className="t-caption text-slate-500">{feesNote(item.macro?.fees?.commission_pct ?? 0.1, item.macro?.fees?.slippage_pct ?? 0.05)}</p>
```

In the results block, right after `<div className="ask-disclaimer" role="note">{DISCLAIMER}</div>`:

```jsx
                  {isShort(state.answers.profile) ? <div className="ask-disclaimer" role="note">{SCALPER_NOTE}</div> : null}
```

- [ ] **Step 4: Build + tests**

`cd frontend && npx vite build --logLevel error` and `node --test "tests/*.test.js"` — pass. grep the file for "추천" — none.

- [ ] **Step 5: Browser check**

`preview_start {name:"backend-dev"}` (restart if already running so Task 1 code loads), `preview_start {name:"frontend-dev"}`. Sign up a throwaway local account if needed, open `/builder?ask=1`, consent, pick **단타형** → confirm the 종목 bubble says "(최대 2개)" and a third symbol is refused → pick 최근 1개월 → confirm the 1분 chip is disabled with the hint → pick 5분 → wait for results (BTCUSDT 5m × 1 month must fetch ~8,640 candles; allow up to 30 s) → confirm every card shows the 수수료·슬리피지 line and the SCALPER_NOTE appears under the disclaimer. Then "처음부터" → 공격형 → 3개월 → 1시간 still works. Take one screenshot; check `read_console_messages` for errors. Record observations in the report (NOT VERIFIED if the tools are unavailable).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/AskParrotDialog.jsx
git commit -m "껄무새에게 물어볼까? 대화창: 단타형 칩(1분 봉 비활성 규칙), 종목 상한, 수수료·단타 안내

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```
