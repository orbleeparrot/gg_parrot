import assert from "node:assert/strict";
import test from "node:test";

import {
  describeDeleteConfirm,
  describeRunOutcome,
  describeStopConfirm,
  elapsedLabel,
  formatSignedUsdt,
} from "../src/features/agents/runOutcome.js";

const base = {
  session_id: 1, symbol: "ZECUSDT", market: "futures", leverage: 3, position_side: "long", testnet: false,
  started_kst: "09/04 16:12:03", stopped_kst: "09/04 18:22:10", last_price: 973.15,
  realized_pnl: 128.4, unrealized_pct: 0, in_position: false, position_uncertain: false,
  note: "청산 완료 후 종료", stop_mode: "close_and_stop", status: "stopped",
};

test("a completed close-and-stop run reads as a closed-out result with the total pnl up front", () => {
  const outcome = describeRunOutcome(base);
  assert.equal(outcome.eyebrow, "실행 종료");
  assert.match(outcome.title, /청산 완료 후 종료/);
  assert.equal(outcome.tone, "good");
  assert.equal(outcome.pnl.text, "+128.40 USDT");
  assert.equal(outcome.pnl.tone, "up");
  assert.equal(outcome.rows.find((row) => row.label === "실행 시간").value, "2시간 10분 · 09/04 16:12:03 → 09/04 18:22:10");
  assert.equal(outcome.rows.find((row) => row.label === "종목·환경").value, "ZECUSDT · 선물 3배 롱 · 메인넷(실거래)");
  assert.equal(outcome.rows.find((row) => row.label === "남은 포지션").value, "없음");
});

test("stopping the macro while in position keeps the position visible and warns", () => {
  const outcome = describeRunOutcome({ ...base, stop_mode: "stop_only", note: "", in_position: true,
    position_qty: 0.42, entry_price: 951.2, unrealized_pct: 2.31, realized_pnl: -42.17 });
  assert.match(outcome.title, /매크로만 종료/);
  assert.equal(outcome.tone, "warning");
  assert.equal(outcome.pnl.text, "−42.17 USDT");
  assert.equal(outcome.pnl.tone, "down");
  assert.equal(outcome.rows.find((row) => row.label === "남은 포지션").value, "0.42 @ 951.2 · 평가 +2.31%");
});

test("an error or uncertain position never reads as a clean finish", () => {
  const failed = describeRunOutcome({ ...base, status: "error", note: "청산 실패 — 포지션 남음" });
  assert.equal(failed.eyebrow, "오류 종료");
  assert.equal(failed.tone, "critical");
  assert.equal(failed.detail, "청산 실패 — 포지션 남음");
  const uncertain = describeRunOutcome({ ...base, position_uncertain: true });
  assert.equal(uncertain.eyebrow, "포지션 확인 필요");
  assert.match(uncertain.title, /확인하지 못했어요/);
});

test("elapsed time survives a year rollover and unknown labels", () => {
  assert.equal(elapsedLabel("12/31 23:50:00", "01/01 00:20:00"), "30분");
  assert.equal(elapsedLabel("09/01 10:00:00", "09/03 12:30:00"), "2일 2시간");
  assert.equal(elapsedLabel("", "09/03 12:30:00"), "");
  assert.equal(formatSignedUsdt(0), "0.00 USDT");
});

test("the close-and-stop confirmation warns about mainnet and adapts to a flat session", () => {
  const live = describeStopConfirm("close_and_stop", { ...base, in_position: true });
  assert.equal(live.tone, "danger");
  assert.match(live.description, /시장가로 정리/);
  assert.match(live.warning, /메인넷/);
  const flat = describeStopConfirm("close_and_stop", { ...base, in_position: false, testnet: true });
  assert.match(flat.description, /보유 포지션이 없어요/);
  assert.equal(flat.warning, "");
  const keep = describeStopConfirm("stop_only", { ...base, in_position: true });
  assert.equal(keep.tone, "primary");
  assert.match(keep.description, /포지션은 그대로/);
  assert.match(describeDeleteConfirm({ ...base, status: "error" }).title, /오류로 끝난 ZECUSDT/);
});


test("requesting a stop shows the result screen in a pending state, not a final figure", () => {
  const pending = describeRunOutcome({ ...base, status: "running", stopping: true,
    stop_mode: "close_and_stop", stopped_kst: "", heartbeat_kst: "09/04 18:22:10",
    in_position: true, position_qty: 0.42, entry_price: 951.2, unrealized_pct: -4.86, realized_pnl: 0 });
  assert.equal(pending.eyebrow, "종료 처리 중");
  assert.match(pending.title, /청산하고 종료하는 중/);
  assert.equal(pending.tone, "pending");
  assert.equal(pending.pending, true);
  assert.equal(pending.pnl.text, "집계 중", "확정 전에는 수치를 주장하지 않는다");
  assert.equal(pending.rows.find((row) => row.label === "실행 시간").value,
    "2시간 10분 · 09/04 16:12:03 → 09/04 18:22:10", "끝점은 마지막 heartbeat");
  assert.match(pending.rows.find((row) => row.label === "남은 포지션").value, /^0.42 @ 951.2/);
});

test("stopping only counts while the session is still running", () => {
  const done = describeRunOutcome({ ...base, stopping: true });
  assert.equal(done.pending, false);
  assert.equal(done.pnl.text, "+128.40 USDT");
});
