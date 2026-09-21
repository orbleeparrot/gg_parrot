# 리더보드 실시간 상태 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 리더보드 각 행이 "왜 멈춰 있는지"(진입 대기·보유 중·청산·오늘 중단·종료)를 보여 주고, 보유 중인 행은 현재가로 미실현 수익률이 실시간 움직이게 한다.

**Architecture:** 시뮬레이터가 `state()`로 포지션 상태를 노출 → 페이퍼 러너가 체크포인트(10s)마다 `PaperSession.state_json`에 저장 → 리더보드 뷰가 이를 읽어 `state`·`legs`·체결 정보를 내려줌 → 프론트는 순수 모듈 `leaderboardState.js`로 상태 문구와 실시간 미실현(`equity + Σ qty·Δprice·dir`)을 계산하고, 새 공개 엔드포인트 `GET /api/prices`를 3s 폴링한다. 정렬은 서버 스냅샷 값 그대로.

**Tech Stack:** FastAPI + SQLModel(sqlite dev / Postgres prod), pytest; React + Vite, node:test.

**Spec:** `docs/superpowers/specs/2026-09-21-leaderboard-live-state-design.md`

## Global Constraints

- 행 상태는 정확히 5종 + none: `stopped` > `halted` > `holding` > `exited` > `waiting` (우선순위 순), 세션 없음 `none`.
- `state()` 반환 키: `in_position, dir(+1|-1), qty, entry_price, cooldown_until_ms(int|None), halted_today`.
- `state_json` 키: `in_position, halted_today, cooldown_until_ms, trade_count, last_fill_ms, last_fill_side, last_fill_return, last_fill_kind("tp"|"sl"|"exit"|""), last_price, checkpoint_ms, legs[{symbol, qty, dir, entry_price, last_price, in_position}]`.
- 엔트리 뷰 추가 키: `state, trade_count, last_fill_kst, last_fill_kind, last_fill_return, cooldown_until_ms, last_price, checkpoint_ms, virtual_balance, legs`. 잠긴 항목도 이 키들은 내려준다(`macro`·`human_summary`만 숨김).
- `PAPER_CHECKPOINT_SECONDS` 기본값 `"10"`.
- `/api/prices`: `symbols` 1~30개, 각 `^[A-Z0-9]{2,20}USDT$`, 위반 시 422 "종목 형식이 잘못됐어요."; 응답 `{"prices": {SYM: float}, "ms": int}`; 공개 읽기.
- 실시간 미실현: `equity_now = equity + Σ qty·(price_now − last_price)·dir`, `return = (equity_now − virtual_balance)/virtual_balance·100`; 보유 leg 중 하나라도 현재가가 없으면 서버 `return_pct`.
- 정렬·순위는 서버값. 색은 `rgb(var(--c-*))` 토큰만. 문구 상수는 `frontend/src/lib/leaderboardCopy.js`(신규).
- 커밋: `git -c user.name="노희재" -c user.email="hsrohsro1234@gmail.com" commit`, 한국어 메시지, 끝에 `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- 백엔드 테스트(레포 루트): `backend/.venv/Scripts/python -m pytest backend/tests/test_leaderboard_state.py -q -p no:warnings`; 관련 회귀 `backend/.venv/Scripts/python -m pytest backend/tests/test_paper_persistence.py backend/tests/test_paper_portfolio.py backend/tests/test_paper_reuse.py backend/tests/test_leaderboard_snapshots.py backend/tests/test_leaderboard_carryover.py -q -p no:warnings`. 프론트: `cd frontend && node --test "tests/*.test.js"`, `npx vite build --logLevel error`.
- 긴 파일은 Write 도구로 쓴다.

---

## File Structure

| 파일 | 역할 |
|---|---|
| `backend/app/engine/stepper.py` | `PositionSim.state()`, `DcaSim.state()` |
| `backend/app/paper.py` | 러너 체결 추적, `_snapshot()["state"]`, `state_json` 저장, 체크포인트 10s |
| `backend/app/db.py` | `PaperSession.state_json` + sqlite/PG 마이그레이션 목록 |
| `supabase/migrations/20260921150000_paper_session_state.sql` | 프로덕션 컬럼 |
| `backend/app/leaderboard.py` | `_durable_statuses` 확장, `derive_row_state`, `_entry_view` 새 키 |
| `backend/app/marketdata.py`, `backend/app/main.py` | `batch_prices`, `GET /api/prices` |
| `backend/tests/test_leaderboard_state.py` (신규) | 백엔드 테스트 전부 |
| `frontend/src/lib/leaderboardCopy.js`, `leaderboardState.js` (신규) | 문구, 순수 계산 |
| `frontend/src/api.js` | `prices()`, `PUBLIC_READS` |
| `frontend/src/pages/Leaderboard.jsx`, `frontend/src/index.css` | 상태 줄, 실시간 값, 펄스 점, 시세 폴링 |
| `frontend/tests/leaderboardState.test.js`, `frontend/tests/chatApi.test.js` | 프론트 테스트 |
| `DEPLOY.md` | 마이그레이션·체크포인트 메모 |

---

### Task 1: 시뮬레이터 `state()`

**Files:**
- Modify: `backend/app/engine/stepper.py` (`PositionSim` ~40–277행, `DcaSim` ~279–381행)
- Test: `backend/tests/test_leaderboard_state.py` (신규)

**Interfaces:**
- Produces: `PositionSim.state() -> dict`, `DcaSim.state() -> dict` (키는 Global Constraints).

- [ ] **Step 1: 실패하는 테스트**

`backend/tests/test_leaderboard_state.py`:

```python
"""리더보드 실시간 상태 — 시뮬레이터 state(), 체크포인트 state_json, 행 상태 파생, /api/prices."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.engine import Macro
from app.engine.stepper import DcaSim, PositionSim, make_sim


def _macro(rule="A", **over):
    base = {
        "name": "t", "symbol": "BTCUSDT", "rule_type": rule, "position_side": "long",
        "market": "spot", "leverage": 1, "candle_interval": "1h",
        "period": {"preset": "3m"},
        "params": {"take_profit_pct": 2, "stop_loss_pct": 1} if rule == "A" else {"amount_per_buy": 100, "interval_days": 1},
        "risk": {"stop_loss_pct": 0, "daily_max_loss_pct": 0, "cooldown_minutes": 30, "max_holding_hours": 0},
        "fees": {"commission_pct": 0, "slippage_pct": 0},
    }
    base.update(over)
    return Macro.model_validate(base)


def test_position_sim_state_before_after_entry_and_exit():
    sim = make_sim(_macro("A"), 1_000.0)
    t0 = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)
    flat = sim.state()
    assert flat == {"in_position": False, "dir": 1, "qty": 0.0, "entry_price": 0.0, "cooldown_until_ms": None, "halted_today": False}
    sim.step(100.0, t0)  # rule A enters immediately
    held = sim.state()
    assert held["in_position"] is True and held["qty"] > 0 and held["entry_price"] == 100.0
    sim.step(98.9, t0 + timedelta(minutes=1))  # stop-loss → cooldown 30m
    after = sim.state()
    assert after["in_position"] is False and after["qty"] == 0.0
    assert after["cooldown_until_ms"] == int((t0 + timedelta(minutes=31)).timestamp() * 1000)
    assert after["halted_today"] is False


def test_position_sim_short_dir_and_daily_halt():
    sim = make_sim(_macro("A", position_side="short", market="futures", leverage=2,
                          risk={"stop_loss_pct": 0, "daily_max_loss_pct": 1, "cooldown_minutes": 0, "max_holding_hours": 0}), 1_000.0)
    t0 = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)
    sim.step(100.0, t0)
    assert sim.state()["dir"] == -1
    sim.step(103.0, t0 + timedelta(minutes=1))  # short loses > 1% of day-start equity → halt
    st = sim.state()
    assert st["in_position"] is False and st["halted_today"] is True
    sim.step(103.0, t0 + timedelta(days=1))  # next day clears the halt
    assert sim.state()["halted_today"] is False


def test_dca_sim_state_tracks_average_cost():
    sim = make_sim(_macro("C"), 1_000.0)
    assert sim.state()["in_position"] is False
    sim.step(100.0, None)
    sim.step(200.0, None)
    st = sim.state()
    assert st["in_position"] is True and st["dir"] == 1
    assert abs(st["entry_price"] - (sim.cost_basis / sim.qty)) < 1e-9
    assert st["cooldown_until_ms"] is None
```

`_macro()`의 필드 이름이 `Macro` 스키마와 다르면 `backend/app/engine/schema.py`를 열어 맞춘다(rule A의 파라미터 키, `risk`/`fees` 이름). DCA의 `step` 호출 간격이 `interval_days`로 막히면 `sim.step`을 `interval`번 반복 호출한다.

- [ ] **Step 2: 실패 확인** — `AttributeError: 'PositionSim' object has no attribute 'state'`

- [ ] **Step 3: 구현**

`PositionSim`에 (equity 메서드 근처):

```python
    def state(self) -> dict:
        """리더보드가 그리는 포지션 상태 — 조회만, 부작용 없음."""
        cooldown = self._cooldown_until
        return {
            "in_position": bool(self.in_pos),
            "dir": 1 if self.side is PositionSide.LONG else -1,
            "qty": float(self.qty),
            "entry_price": float(self.entry_fill) if self.in_pos else 0.0,
            "cooldown_until_ms": int(cooldown.timestamp() * 1000) if cooldown is not None else None,
            "halted_today": self._halted_day is not None and self._halted_day == self._day,
        }
```

`DcaSim`에:

```python
    def state(self) -> dict:
        held = self.qty > 0
        return {
            "in_position": held,
            "dir": 1,
            "qty": float(self.qty),
            "entry_price": float(self.cost_basis / self.qty) if held else 0.0,
            "cooldown_until_ms": None,
            "halted_today": self._halted_day is not None and self._halted_day == self._day,
        }
```

`entry_price`가 청산 후 0.0이 되도록 `in_pos`/`held`로 가드한다(진입가 잔상 방지).

- [ ] **Step 4: 통과 확인** — `3 passed`
- [ ] **Step 5: 커밋** — `git add backend/app/engine/stepper.py backend/tests/test_leaderboard_state.py` / 메시지 `리더보드 상태: 시뮬레이터 state() — 포지션·진입가·쿨다운·일일 중단 노출`

---

### Task 2: 러너 체결 추적 + `state_json` 체크포인트 + 10s

**Files:**
- Modify: `backend/app/paper.py` (`CHECKPOINT_SECONDS` 40행, `_Runner.__init__` ~96–125행, `_tick_and_checkpoint` 302행, `_snapshot` 326행, `_persist_checkpoint` 352행, `_persist_finalize` 424행), `backend/app/db.py` (`PaperSession` 211행, `_migrate` `added["papersession"]` ~1028행, `_PG_ADDED_COLUMNS["papersession"]` ~1175행)
- Create: `supabase/migrations/20260921150000_paper_session_state.sql`
- Test: `backend/tests/test_leaderboard_state.py`

**Interfaces:**
- Consumes: Task 1 `state()`.
- Produces: `PaperSession.state_json: str`; `_snapshot(runner)["state"]` (Global Constraints 키); `_Runner.trade_count`, `_Runner.last_fill`, `_Runner.last_entry_return`; `paper.sim_state(sim) -> dict` (state() 없는 sim에는 기본값).

- [ ] **Step 1: 실패하는 테스트** (파일에 추가 — `test_paper_persistence.py`의 `_FakeSim`/`_runner`/`_run`/`_session_db` 패턴을 그대로 가져온다; 그 파일을 열어 sqlite 임시 DB를 어떻게 붙이는지 확인)

```python
import json
from app import paper
from app.db import PaperSession
from app.engine.stepper import Fill


class _StatefulSim:
    def __init__(self):
        self.liquidations = 0
        self.liquidated_loss = 0.0
        self.in_pos = False
        self.fills = []

    def step(self, price, ts):
        return self.fills.pop(0) if self.fills else None

    def equity(self, price):
        return 1_010.0

    def state(self):
        return {"in_position": self.in_pos, "dir": 1, "qty": 2.0 if self.in_pos else 0.0,
                "entry_price": 100.0 if self.in_pos else 0.0, "cooldown_until_ms": None, "halted_today": False}


def test_snapshot_state_tracks_fills_and_kind():
    sim = _StatefulSim()
    runner = paper._Runner(7, sim, "BTCUSDT", "live", 1_000.0)
    st = paper._snapshot(runner)["state"]
    assert st["in_position"] is False and st["trade_count"] == 0 and st["last_fill_kind"] == "" and st["legs"][0]["symbol"] == "BTCUSDT"
    sim.in_pos = True
    entry = Fill(side="buy", price=100.0, qty=2.0, equity_after=1_000.0, return_pct=0.0)
    paper._note_fill(runner, entry, "BTCUSDT")
    st = paper._snapshot(runner)["state"]
    assert st["trade_count"] == 1 and st["last_fill_side"] == "buy" and st["last_fill_kind"] == "" and st["in_position"] is True
    assert st["legs"][0]["qty"] == 2.0 and st["legs"][0]["entry_price"] == 100.0 and st["legs"][0]["dir"] == 1
    sim.in_pos = False
    exit_ = Fill(side="sell", price=102.0, qty=2.0, equity_after=1_004.0, return_pct=0.4)
    paper._note_fill(runner, exit_, "BTCUSDT")
    st = paper._snapshot(runner)["state"]
    assert st["trade_count"] == 2 and st["last_fill_kind"] == "tp" and st["last_fill_return"] == 0.4
    loss = Fill(side="sell", price=99.0, qty=2.0, equity_after=998.0, return_pct=-0.2)
    paper._note_fill(runner, Fill(side="buy", price=100.0, qty=2.0, equity_after=998.0, return_pct=-0.2), "BTCUSDT")
    paper._note_fill(runner, loss, "BTCUSDT")
    assert paper._snapshot(runner)["state"]["last_fill_kind"] == "sl"


def test_snapshot_state_tolerates_sim_without_state():
    class _Bare:
        liquidations = 0
        liquidated_loss = 0.0
        def step(self, p, ts): return None
        def equity(self, p): return 1_000.0
    runner = paper._Runner(8, _Bare(), "ETHUSDT", "live", 1_000.0)
    st = paper._snapshot(runner)["state"]
    assert st["in_position"] is False and st["legs"][0]["qty"] == 0.0


def test_checkpoint_persists_state_json(tmp_path, monkeypatch):
    # test_paper_persistence.py 의 임시 sqlite 세션 픽스처를 그대로 사용한다(그 파일의 헬퍼를 import 하거나 복사).
    ...  # 세션 행을 만들고 paper._persist_checkpoint(snapshot_with_state, None) 호출 후
    # row.state_json 이 json.dumps(snapshot["state"]) 와 같고, paper._persist_finalize 도 같은 키를 쓰는지 확인.


def test_checkpoint_default_is_10s():
    assert paper.CHECKPOINT_SECONDS == 10.0
```

`test_checkpoint_persists_state_json`는 `...` 대신 실제 코드로 채운다 — `test_paper_persistence.py`에서 DB를 붙이는 방식(`_session_db` 류 컨텍스트 매니저)을 import해 세션 행 하나를 만들고 `_persist_checkpoint` → `state_json` 확인, 이어서 `_persist_finalize` → `status == "stopped"`이고 `state_json`이 갱신됐는지 확인한다.

- [ ] **Step 2: 실패 확인** — `AttributeError: module 'app.paper' has no attribute '_note_fill'` 등.

- [ ] **Step 3: 구현**

`paper.py`:
- `CHECKPOINT_SECONDS = max(1.0, float(os.environ.get("PAPER_CHECKPOINT_SECONDS", "10")))`.
- `_Runner.__init__`에 `self.trade_count = 0`, `self.last_fill: Optional[dict] = None`, `self.last_entry_return: Optional[float] = None` (슬롯을 쓰는 클래스면 `__slots__`에도 추가).
- 헬퍼:

```python
_EXIT_SIDES = {"sell", "cover"}


def sim_state(sim) -> dict:
    """state() 가 없는 시뮬레이터(테스트 더미 등)에는 '포지션 없음' 기본값."""
    getter = getattr(sim, "state", None)
    if getter is None:
        return {"in_position": False, "dir": 1, "qty": 0.0, "entry_price": 0.0, "cooldown_until_ms": None, "halted_today": False}
    return getter()


def _note_fill(runner: _Runner, fill, symbol: str) -> None:
    """체결 하나를 러너의 상태 요약에 반영 — 횟수·마지막 체결·익절/손절 구분."""
    runner.trade_count += 1
    kind = ""
    if fill.side in _EXIT_SIDES:
        kind = "exit" if runner.last_entry_return is None else ("tp" if fill.return_pct > runner.last_entry_return else "sl")
    else:
        runner.last_entry_return = float(fill.return_pct)
    runner.last_fill = {"ms": _now_ms(), "side": fill.side, "return": round(float(fill.return_pct), 4), "kind": kind, "symbol": symbol}
```

- `_tick_and_checkpoint`: `fills.append((leg.symbol, fill))` 직후 `_note_fill(runner, fill, leg.symbol)`.
- `_snapshot()`에 `"state": _state_view(runner)`:

```python
def _state_view(runner: _Runner) -> dict:
    legs = []
    cooldowns = []
    for leg in runner.legs:
        st = sim_state(leg.sim)
        legs.append({"symbol": leg.symbol, "qty": round(st["qty"], 8), "dir": st["dir"],
                     "entry_price": round(st["entry_price"], 4), "last_price": round(leg.last_price, 4),
                     "in_position": bool(st["in_position"])})
        if st["cooldown_until_ms"] is not None:
            cooldowns.append(int(st["cooldown_until_ms"]))
    last = runner.last_fill or {}
    return {
        "in_position": any(l["in_position"] for l in legs),
        "halted_today": any(sim_state(leg.sim)["halted_today"] for leg in runner.legs),
        "cooldown_until_ms": max(cooldowns) if cooldowns else None,
        "trade_count": runner.trade_count,
        "last_fill_ms": last.get("ms"), "last_fill_side": last.get("side", ""),
        "last_fill_return": last.get("return"), "last_fill_kind": last.get("kind", ""),
        "last_price": round(runner.last_price, 4), "checkpoint_ms": _now_ms(), "legs": legs,
    }
```

- `_persist_checkpoint`와 `_persist_finalize`: `row.state_json = json.dumps(snapshot.get("state") or {})`.

`db.py`: `PaperSession`에 `# 리더보드 행 상태(포지션·체결 요약) — paper._state_view 가 체크포인트마다 쓴다.` 주석과 `state_json: str = ""`; `_migrate` `added["papersession"]["state_json"] = "ALTER TABLE papersession ADD COLUMN state_json TEXT DEFAULT ''"`; `_PG_ADDED_COLUMNS["papersession"]`에 `"state_json": "TEXT DEFAULT ''"`. 마이그레이션 문장 목록을 고정하는 테스트(`test_chat_members.py` 등)가 있으면 갱신한다.

`supabase/migrations/20260921150000_paper_session_state.sql`:

```sql
-- 리더보드 실시간 상태 (2026-09-21): papersession.state_json — 포지션·체결 요약(JSON). 체크포인트마다 갱신.
alter table public.papersession add column if not exists state_json text not null default '';
```

- [ ] **Step 4: 통과 확인** — 새 테스트 + `test_paper_persistence.py test_paper_portfolio.py test_paper_reuse.py` 전부 pass.
- [ ] **Step 5: 커밋** — `리더보드 상태: 페이퍼 러너가 체결·포지션 요약을 state_json 에 저장, 체크포인트 20s→10s`

---

### Task 3: 리더보드 뷰 — `derive_row_state` + 새 키

**Files:**
- Modify: `backend/app/leaderboard.py` (`_live_return` 228행, `_entry_view` 247행, `_durable_statuses` 370행)
- Test: `backend/tests/test_leaderboard_state.py`

**Interfaces:**
- Consumes: `PaperSession.state_json`, `paper.get_status()`(러너 메모리 경로 — 여기서도 `state`를 함께 돌려주도록 Task 2의 `_state_view`를 `get_status`에 `"state"`로 추가).
- Produces: `derive_row_state(session_status: str | None, state: dict) -> str`; `_entry_view` 새 키(Global Constraints).

- [ ] **Step 1: 실패하는 테스트**

```python
from app import leaderboard


def test_derive_row_state_priority():
    d = leaderboard.derive_row_state
    assert d(None, {}) == "none"
    assert d("stopped", {"in_position": True}) == "stopped"
    assert d("running", {"halted_today": True, "in_position": True}) == "halted"
    assert d("running", {"in_position": True, "trade_count": 3}) == "holding"
    assert d("running", {"in_position": False, "trade_count": 2}) == "exited"
    assert d("running", {"in_position": False, "trade_count": 0}) == "waiting"
    assert d("running", {}) == "waiting"


def test_entry_view_exposes_state_even_when_locked():
    from types import SimpleNamespace
    row = SimpleNamespace(id=1, user_id="u", nickname="n", username="user", owner_user_id=42, is_ai=False,
                          symbol="BTCUSDT", macro_json="{}", human_summary="비밀 전략", paper_session_id=9,
                          created_at="2026-09-21T00:00:00Z", created_ms=1, streak_days=1, first_created_ms=None)
    status = {"current_return": 1.5, "current_equity": 1015.0, "status": "running", "mode": "live", "virtual_balance": 1000.0,
              "state": {"in_position": True, "halted_today": False, "cooldown_until_ms": None, "trade_count": 1,
                        "last_fill_ms": 1_700_000_000_000, "last_fill_side": "buy", "last_fill_return": 0.0, "last_fill_kind": "",
                        "last_price": 101.0, "checkpoint_ms": 1_700_000_005_000,
                        "legs": [{"symbol": "BTCUSDT", "qty": 2.0, "dir": 1, "entry_price": 100.0, "last_price": 101.0, "in_position": True}]}}
    view = leaderboard._entry_view(row, {}, viewer_id="x", viewer_user_id=7, paper_status=status)
    assert view["locked"] is True and view["human_summary"] == "" and view["macro"] is None
    assert view["state"] == "holding" and view["trade_count"] == 1 and view["last_fill_kst"] is not None
    assert view["virtual_balance"] == 1000.0 and view["legs"][0]["qty"] == 2.0 and view["last_price"] == 101.0
    none_view = leaderboard._entry_view(row, {}, viewer_id="x", viewer_user_id=7, paper_status=None)
    assert none_view["state"] == "none" and none_view["legs"] == [] and none_view["trade_count"] == 0
```

`_entry_view`의 실제 시그니처/`row` 필드에 맞춰 `SimpleNamespace`를 채운다(`streak_days`, `defending` 계산에 필요한 필드 등 — 함수 본문을 읽고 빠진 속성을 추가).

- [ ] **Step 2: 실패 확인** — `AttributeError: derive_row_state`.

- [ ] **Step 3: 구현**

```python
def derive_row_state(session_status: str | None, state: dict) -> str:
    """리더보드 행 상태 — 스펙 §4 우선순위. 세션이 없으면 none."""
    if not session_status:
        return "none"
    if session_status != "running":
        return "stopped"
    if state.get("halted_today"):
        return "halted"
    if state.get("in_position"):
        return "holding"
    if int(state.get("trade_count") or 0) > 0:
        return "exited"
    return "waiting"
```

- `_durable_statuses`: `PaperSession.virtual_balance`, `PaperSession.state_json`도 select; `"virtual_balance": vb, "state": _parse_state(state_json)` (`json.loads` 실패·빈 문자열 → `{}`).
- `paper.get_status()` 러너 경로에 `"state": _state_view(runner)` 추가, DB 경로에 `"state": _parse_state(row.state_json)`(paper.py에 같은 파서 두고 leaderboard는 그걸 import 하거나 자체 보유 — 한 곳에만 둔다: `paper.parse_state(text) -> dict`).
- `_live_return`은 그대로 두고, `_entry_view`에서 `status = paper_status if paper_status is not _STATUS_NOT_PROVIDED else paper_mod.get_status(row.paper_session_id)`를 한 번만 얻어 새 키를 만든다:

```python
    state = (status or {}).get("state") or {}
    view.update({
        "state": derive_row_state((status or {}).get("status") if status else None, state),
        "trade_count": int(state.get("trade_count") or 0),
        "last_fill_kst": _kst_hhmm(state["last_fill_ms"]) if state.get("last_fill_ms") else None,
        "last_fill_kind": state.get("last_fill_kind", ""),
        "last_fill_return": state.get("last_fill_return"),
        "cooldown_until_ms": state.get("cooldown_until_ms"),
        "last_price": state.get("last_price"),
        "checkpoint_ms": state.get("checkpoint_ms"),
        "virtual_balance": (status or {}).get("virtual_balance"),
        "legs": list(state.get("legs") or []),
    })
```

`publish_snapshot`의 public 필드 필터([leaderboard_snapshot.py:163](../../backend/app/leaderboard_snapshot.py))는 제외 목록 방식이라 새 키가 그대로 통과한다 — 확인만 한다.

- [ ] **Step 4: 통과 확인** — 새 테스트 + `test_leaderboard_snapshots.py test_leaderboard_carryover.py` pass.
- [ ] **Step 5: 커밋** — `리더보드 상태: 행 상태 5종 파생 + 체결·포지션·가상자본을 엔트리 뷰에 노출(잠긴 항목 포함)`

---

### Task 4: `GET /api/prices` + `api.prices`

**Files:**
- Modify: `backend/app/marketdata.py`, `backend/app/main.py` (`/api/candles/live` 1224행 근처), `frontend/src/api.js` (`PUBLIC_READS` 17행, `api` 객체)
- Test: `backend/tests/test_leaderboard_state.py`, `frontend/tests/chatApi.test.js`

**Interfaces:**
- Produces: `marketdata.batch_prices(symbols: list[str]) -> dict[str, float]`; `GET /api/prices?symbols=` → `{"prices", "ms"}`; `api.prices(symbols, options) -> Promise`.

- [ ] **Step 1: 실패하는 테스트**

백엔드:

```python
from fastapi.testclient import TestClient
from app.main import app
from app import marketdata

client = TestClient(app)


def test_prices_endpoint_validates_and_skips_failures(monkeypatch):
    monkeypatch.setattr(marketdata, "get_ticker_price_cached", lambda s: {"BTCUSDT": 100.5, "ETHUSDT": None}.get(s))
    r = client.get("/api/prices?symbols=btcusdt,ETHUSDT")
    assert r.status_code == 200 and r.json()["prices"] == {"BTCUSDT": 100.5} and isinstance(r.json()["ms"], int)
    assert client.get("/api/prices?symbols=BTC-KRW").status_code == 422
    assert client.get("/api/prices?symbols=").status_code == 422
    assert client.get("/api/prices?symbols=" + ",".join(f"S{i}USDT" for i in range(31))).status_code == 422
```

프론트 `chatApi.test.js`에 추가:

```js
test("prices request is a public read with comma-joined symbols", async (t) => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, options) => { calls.push({ url, ...options }); return new Response(JSON.stringify({ prices: {} }), { status: 200 }); });
  await api.prices(["BTCUSDT", "ETHUSDT"]);
  const u = new URL(calls[0].url, "https://fixture.invalid");
  assert.equal(u.pathname, "/api/prices");
  assert.equal(u.searchParams.get("symbols"), "BTCUSDT,ETHUSDT");
  assert.equal(calls[0].credentials, "omit");
});
```

- [ ] **Step 2: 실패 확인** — 404 / `api.prices is not a function`.

- [ ] **Step 3: 구현**

`marketdata.py`:

```python
import re
from .data.binance import get_ticker_price_cached

SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,20}USDT$")
MAX_PRICE_SYMBOLS = 30


def batch_prices(symbols: list[str]) -> dict[str, float]:
    """리더보드 실시간 미실현용 일괄 시세 — 2s 캐시 공유, 못 받은 종목은 생략."""
    out: dict[str, float] = {}
    for symbol in symbols:
        price = get_ticker_price_cached(symbol)
        if price:
            out[symbol] = float(price)
    return out
```

`main.py`:

```python
@app.get("/api/prices")
def prices(symbols: str = Query(..., max_length=700)) -> dict:
    """공개 일괄 시세 — 리더보드 보유 중 행의 미실현 수익률용."""
    wanted = list(dict.fromkeys(s.strip().upper() for s in symbols.split(",") if s.strip()))
    if not wanted or len(wanted) > marketdata_mod.MAX_PRICE_SYMBOLS or any(not marketdata_mod.SYMBOL_RE.match(s) for s in wanted):
        raise HTTPException(422, "종목 형식이 잘못됐어요.")
    return {"prices": marketdata_mod.batch_prices(wanted), "ms": int(time.time() * 1000)}
```

(`marketdata_mod` import 이름은 main.py의 기존 관례를 따른다.)

`api.js`: `PUBLIC_READS`에 `"/api/prices"` 추가; `prices: (symbols, options = {}) => req(\`/api/prices?symbols=${encodeURIComponent(symbols.join(","))}\`, { timeoutMs: 8_000, ...options })`.

- [ ] **Step 4: 통과 확인** — 백엔드 새 테스트 pass; `node --test tests/chatApi.test.js` pass.
- [ ] **Step 5: 커밋** — `리더보드 상태: 공개 일괄 시세 GET /api/prices (최대 30종목, 2s 캐시)`

---

### Task 5: 프론트 순수 모듈 — 문구 + 계산

**Files:**
- Create: `frontend/src/lib/leaderboardCopy.js`, `frontend/src/lib/leaderboardState.js`
- Test: `frontend/tests/leaderboardState.test.js`

**Interfaces:**
- Produces: `liveReturn(entry, prices)` → number|null; `stateLine(entry, now)` → `{text, tone}`; `cooldownLabel(untilMs, now)`; `symbolsOf(items)` → string[]; `isLive(entry, prices)` → bool.

- [ ] **Step 1: 실패하는 테스트**

```js
import assert from "node:assert/strict";
import test from "node:test";
import { cooldownLabel, isLive, liveReturn, stateLine, symbolsOf } from "../src/lib/leaderboardState.js";

const holding = { state: "holding", return_pct: 1.5, equity: 1015, virtual_balance: 1000,
  legs: [{ symbol: "BTCUSDT", qty: 2, dir: 1, last_price: 100, in_position: true }] };

test("liveReturn moves with price for a long, inversely for a short, sums a portfolio, falls back without prices", () => {
  assert.equal(liveReturn(holding, { BTCUSDT: 101 }), 1.7);           // 1015 + 2*1 = 1017 → 1.7%
  const short = { ...holding, legs: [{ ...holding.legs[0], dir: -1 }] };
  assert.equal(liveReturn(short, { BTCUSDT: 101 }), 1.3);
  const port = { ...holding, legs: [holding.legs[0], { symbol: "ETHUSDT", qty: 10, dir: 1, last_price: 10, in_position: true }] };
  assert.equal(liveReturn(port, { BTCUSDT: 101, ETHUSDT: 10.5 }), 2.2); // +2 +5 → 1022
  assert.equal(liveReturn(port, { BTCUSDT: 101 }), 1.5);               // ETH 없음 → 서버값
  assert.equal(liveReturn({ ...holding, state: "waiting" }, { BTCUSDT: 101 }), 1.5);
  assert.equal(liveReturn({ ...holding, virtual_balance: null }, { BTCUSDT: 101 }), 1.5);
  assert.equal(isLive(holding, { BTCUSDT: 101 }), true);
  assert.equal(isLive(holding, {}), false);
});

test("stateLine renders the five states", () => {
  const now = 1_700_000_000_000;
  assert.deepEqual(stateLine({ state: "waiting", symbol: "BTCUSDT", last_price: 96412.1, trade_count: 0 }, now), { text: "진입 대기 · BTC 96,412.1", tone: "muted" });
  assert.deepEqual(stateLine({ ...holding, trade_count: 1, legs: [{ ...holding.legs[0], entry_price: 100 }] }, now), { text: "보유 중 · 진입가 100 · 거래 1회", tone: "live" });
  assert.deepEqual(stateLine({ state: "exited", last_fill_kst: "12:41", last_fill_kind: "tp", last_fill_return: 1.8, cooldown_until_ms: null, trade_count: 2 }, now), { text: "12:41 익절 +1.80% · 재진입 대기 · 거래 2회", tone: "good" });
  assert.deepEqual(stateLine({ state: "exited", last_fill_kst: "12:41", last_fill_kind: "sl", last_fill_return: -0.9, cooldown_until_ms: now + 14 * 60_000 + 1, trade_count: 2 }, now), { text: "12:41 손절 -0.90% · 쿨다운 15분 · 거래 2회", tone: "bad" });
  assert.deepEqual(stateLine({ state: "halted", trade_count: 3 }, now), { text: "일일 손실 한도 · 내일 재개 · 거래 3회", tone: "warn" });
  assert.deepEqual(stateLine({ state: "stopped", return_pct: 0.4, trade_count: 0 }, now), { text: "종료됨 · 최종 +0.40%", tone: "muted" });
  assert.equal(stateLine({ state: "none" }, now), null);
});

test("cooldownLabel and symbolsOf", () => {
  const now = 1_700_000_000_000;
  assert.equal(cooldownLabel(now + 61_000, now), "쿨다운 2분");
  assert.equal(cooldownLabel(now - 1, now), "재진입 대기");
  assert.equal(cooldownLabel(null, now), "재진입 대기");
  const items = [holding, { ...holding, legs: [{ symbol: "ETHUSDT", qty: 1, dir: 1, last_price: 1, in_position: true }, { symbol: "BTCUSDT", qty: 0, dir: 1, last_price: 1, in_position: false }] }, { state: "waiting", legs: [{ symbol: "SOLUSDT", in_position: false }] }];
  assert.deepEqual(symbolsOf(items), ["BTCUSDT", "ETHUSDT"]);
});
```

- [ ] **Step 2: 실패 확인** — 모듈 없음.

- [ ] **Step 3: 구현**

`leaderboardCopy.js`:

```js
// 리더보드 행 상태 문구 — 한곳.
export const WAITING = "진입 대기";
export const HOLDING = "보유 중";
export const ENTRY_AT = (p) => `진입가 ${p}`;
export const REENTRY = "재진입 대기";
export const COOLDOWN = (m) => `쿨다운 ${m}분`;
export const HALTED = "일일 손실 한도 · 내일 재개";
export const STOPPED = (r) => `종료됨 · 최종 ${r}`;
export const TRADES = (n) => `거래 ${n}회`;
export const KIND = { tp: "익절", sl: "손절", exit: "청산" };
export const LIVE_TITLE = "현재가로 계산한 미실현 수익률 — 3초마다 갱신";
```

`leaderboardState.js`:

```js
import { COOLDOWN, ENTRY_AT, HALTED, HOLDING, KIND, REENTRY, STOPPED, TRADES, WAITING } from "./leaderboardCopy.js";

const fmtPct = (v) => `${v >= 0 ? "+" : ""}${Number(v).toFixed(2)}%`;
const fmtPrice = (v) => Number(v).toLocaleString("en-US", { maximumFractionDigits: 4 });
const base = (symbol) => String(symbol || "").replace(/USDT$/, "");

export function isLive(entry, prices) {
  if (entry?.state !== "holding" || !(entry.virtual_balance > 0)) return false;
  const held = (entry.legs || []).filter((l) => l.in_position);
  return held.length > 0 && held.every((l) => Number.isFinite(prices?.[l.symbol]));
}

export function liveReturn(entry, prices) {
  if (!isLive(entry, prices)) return entry?.return_pct ?? null;
  const delta = (entry.legs || []).filter((l) => l.in_position)
    .reduce((sum, l) => sum + l.qty * (prices[l.symbol] - l.last_price) * (l.dir || 1), 0);
  const equity = entry.equity + delta;
  return Math.round(((equity - entry.virtual_balance) / entry.virtual_balance) * 100 * 100) / 100;
}

export function cooldownLabel(untilMs, now = Date.now()) {
  if (!untilMs || untilMs <= now) return REENTRY;
  return COOLDOWN(Math.ceil((untilMs - now) / 60_000));
}

export function stateLine(entry, now = Date.now()) {
  if (!entry || !entry.state || entry.state === "none") return null;
  const trades = entry.trade_count > 0 ? [TRADES(entry.trade_count)] : [];
  switch (entry.state) {
    case "waiting":
      return { text: [`${WAITING} · ${base(entry.symbol)} ${fmtPrice(entry.last_price ?? 0)}`, ...trades].join(" · "), tone: "muted" };
    case "holding": {
      const leg = (entry.legs || []).find((l) => l.in_position);
      return { text: [HOLDING, leg ? ENTRY_AT(fmtPrice(leg.entry_price)) : null, ...trades].filter(Boolean).join(" · "), tone: "live" };
    }
    case "exited": {
      const kind = KIND[entry.last_fill_kind] || KIND.exit;
      const head = `${entry.last_fill_kst || ""} ${kind} ${fmtPct(entry.last_fill_return ?? 0)}`.trim();
      return { text: [head, cooldownLabel(entry.cooldown_until_ms, now), ...trades].join(" · "), tone: entry.last_fill_kind === "sl" ? "bad" : "good" };
    }
    case "halted":
      return { text: [HALTED, ...trades].join(" · "), tone: "warn" };
    case "stopped":
      return { text: [STOPPED(fmtPct(entry.return_pct ?? 0)), ...trades].join(" · "), tone: "muted" };
    default:
      return null;
  }
}

export function symbolsOf(items) {
  const out = [];
  for (const e of items || []) {
    if (e?.state !== "holding") continue;
    for (const l of e.legs || []) if (l.in_position && !out.includes(l.symbol)) out.push(l.symbol);
  }
  return out.slice(0, 30);
}
```

테스트의 정확한 문자열(예: `"12:41 익절 +1.80% · 재진입 대기 · 거래 2회"`)과 맞을 때까지 조합 순서를 조정한다 — 문구는 `leaderboardCopy.js`만 바꾼다.

- [ ] **Step 4: 통과 확인** — `node --test tests/leaderboardState.test.js` 3 pass.
- [ ] **Step 5: 커밋** — `리더보드 상태 프론트: 상태 문구·실시간 미실현 계산 순수 모듈`

---

### Task 6: Leaderboard.jsx 통합 + CSS + 브라우저 확인

**Files:**
- Modify: `frontend/src/pages/Leaderboard.jsx` (`ret()` 108행, 행 렌더 ~404–443행, 폴링 훅 ~222행), `frontend/src/index.css` (`.lb-return` 8894행 근처)

**Interfaces:**
- Consumes: Task 4 `api.prices`, Task 5 모듈, 기존 `useAdaptivePolling(load, {intervalMs, maxIntervalMs, pollKey, enabled?})` — 훅 시그니처를 열어 `enabled` 옵션이 있는지 확인, 없으면 `intervalMs`를 크게 주는 대신 심볼이 비면 `load`가 즉시 return 하도록 한다.

- [ ] **Step 1: 시세 폴링 + 실시간 값**

```jsx
import { isLive, liveReturn, stateLine, symbolsOf } from "../lib/leaderboardState.js";
import { LIVE_TITLE } from "../lib/leaderboardCopy.js";
// 컴포넌트 안:
const [prices, setPrices] = useState({});
const symbols = useMemo(() => symbolsOf(items), [items]);
const loadPrices = useCallback(async ({ signal } = {}) => {
  if (!symbols.length) return;
  try { const d = await api.prices(symbols, { signal }); setPrices((p) => ({ ...p, ...(d?.prices || {}) })); } catch { /* 다음 틱 */ }
}, [symbols]);
useAdaptivePolling(loadPrices, { intervalMs: 3_000, maxIntervalMs: 30_000, pollKey: `prices:${symbols.join(",")}` });
```

`ret(e)`를 `ret(e, prices)`로: `const value = liveReturn(e, prices); if (value == null) return { text: "집계중…", cls: "text-slate-500" }; ...` 그리고 `live: isLive(e, prices)`를 함께 돌려준다.

- [ ] **Step 2: 상태 줄 + 펄스 점**

수익률 셀:

```jsx
<div className={"lb-return num " + r.cls} role="cell" aria-label={`수익률 ${r.text}`}>
  <span className="lb-return-value">{r.text}{r.live ? <i className="lb-live-dot" aria-hidden="true" title={LIVE_TITLE} /> : null}</span>
  {line ? <span className={`lb-state is-${line.tone}`}>{line.text}</span> : null}
</div>
```

`const line = stateLine(e, nowMs);` — `nowMs`는 `useState(Date.now())` + 30초 인터벌로 갱신(쿨다운 분 표기용).

- [ ] **Step 3: CSS** (`index.css`의 `.lb-return` 규칙 뒤)

```css
.lb-return { display: flex; flex-direction: column; align-items: flex-end; gap: 2px; }
.lb-state { font-size: 11px; line-height: 1.3; font-weight: 600; white-space: nowrap; color: rgb(var(--c-slate-500)); }
.lb-state.is-live { color: rgb(var(--c-indigo-700)); }
.lb-state.is-good { color: rgb(var(--c-green-700)); }
.lb-state.is-bad { color: rgb(var(--c-red-700)); }
.lb-state.is-warn { color: rgb(var(--c-amber-700)); }
.lb-live-dot { display: inline-block; width: 6px; height: 6px; margin-left: 6px; border-radius: 999px; background: rgb(var(--c-green-600)); vertical-align: middle; animation: lb-live-pulse 1.6s ease-in-out infinite; }
@keyframes lb-live-pulse { 0%, 100% { opacity: .35; } 50% { opacity: 1; } }
@media (prefers-reduced-motion: reduce) { .lb-live-dot { animation: none; } }
```

토큰 이름(`--c-green-700`, `--c-red-700`, `--c-amber-700`, `--c-indigo-700`)이 `index.css` `:root`에 있는지 확인하고 없는 색은 있는 가장 가까운 토큰으로 바꾼다. 모바일 레이아웃(`.lb-row` 폰 규칙, ~8081행 부근 미디어쿼리)에서 `.lb-state`가 잘리면 `white-space: normal; text-align: right`로 푼다.

- [ ] **Step 4: 빌드·테스트·브라우저**

`npx vite build --logLevel error` 무음, `node --test "tests/*.test.js"` 전부 pass.

브라우저(`preview_start frontend-dev`, 백엔드 8000): 리더보드에서
1. 진입 대기 행에 `진입 대기 · BTC 9x,xxx` 캡션이 보인다.
2. 보유 중 행(로컬 DB에 거래 1건인 세션이 있음)에 펄스 점 + 캡션 `보유 중 · 진입가 …`가 보이고, `read_network_requests`로 `/api/prices` 3초 폴링을 확인한다. 수익률 숫자가 `/api/prices` 응답 사이에 바뀌는지 `javascript_tool`로 두 번 읽어 비교(가격이 안 움직이면 같을 수 있음 — 그 경우 `liveReturn` 단위 테스트로 갈음하고 보고서에 적는다).
3. 다크 모드에서 캡션 색 대비 확인.
스크린샷 1장.

- [ ] **Step 5: 커밋** — `리더보드: 행 상태 캡션(대기·보유·청산·중단·종료) + 보유 중 실시간 미실현(3s 시세)`

---

### Task 7: 문서 + 전체 회귀

**Files:**
- Modify: `DEPLOY.md`

- [ ] **Step 1: DEPLOY.md** — 「전략방」 섹션 뒤에:

```markdown
## 리더보드 실시간 상태

- 마이그레이션: `supabase/migrations/20260921150000_paper_session_state.sql` (papersession.state_json). 기동 시 `_PG_ADDED_COLUMNS`도 보강하므로 순서는 무관.
- `PAPER_CHECKPOINT_SECONDS` 기본 10초(이전 20초). DB 쓰기가 부담되면 환경변수로 되돌린다.
- `GET /api/prices`는 공개 읽기, 2초 캐시 공유.
```

- [ ] **Step 2: 회귀** — `backend/.venv/Scripts/python -m pytest backend/tests -q -p no:warnings --continue-on-collection-errors` (기존 prefect/env 실패 3건·수집 오류 6건만), `cd frontend && node --test "tests/*.test.js" && npx vite build --logLevel error`.
- [ ] **Step 3: 커밋** — `리더보드 상태: 배포 메모`
