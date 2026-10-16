import assert from "node:assert/strict";
import test from "node:test";
import { entryWaitModel, fmtRoughSpan, fmtSpan, isWaitingMacro, typicalWait } from "../src/lib/entryWait.js";

const H = 3_600_000;
const T0 = Date.UTC(2026, 9, 16, 0, 0); // 09:00 KST

// 1시간 봉: closes 배열로 만든다. 마지막 봉은 진행 중(closed:false).
function bars(closes, { range = 1, step = H, formingOpen = null } = {}) {
  return closes.map((c, i) => {
    const last = i === closes.length - 1;
    const o = last && formingOpen !== null ? formingOpen : i ? closes[i - 1] : c;
    return { t: T0 + i * step, o, h: Math.max(o, c) + range / 2, l: Math.min(o, c) - range / 2, c, closed: !last };
  });
}

test("기다리는 매크로만 고른다 — E 는 눌림 진입일 때만", () => {
  assert.equal(isWaitingMacro({ rule_type: "I", params: {} }), true);
  assert.equal(isWaitingMacro({ rule_type: "A", params: {} }), false);
  assert.equal(isWaitingMacro({ rule_type: "E", params: { entry_mode: "immediate" } }), false);
  assert.equal(isWaitingMacro({ rule_type: "E", params: { entry_mode: "dip" } }), true);
  assert.equal(entryWaitModel({ macro: { rule_type: "I", params: {} }, session: { in_position: true } }), null);
});

test("변동성 돌파 — 목표 = 이 봉 시가 + k × 직전 봉 변동폭, 지금과의 거리", () => {
  const candles = bars([100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 101], { range: 4, formingOpen: 100 });
  candles[candles.length - 1].h = 101; // 진행 중 봉은 아직 목표(102)에 닿지 않았다
  const macro = { rule_type: "I", candle_interval: "1h", symbol: "QNTUSDT", params: { k: 0.5 } };
  const m = entryWaitModel({ macro, session: { last_price: 101, started_at: new Date(T0 + 7 * H).toISOString(), symbol: "QNTUSDT" }, candles, now: T0 + 10.5 * H });
  // 직전 봉(마감) 고가-저가 = 4 → 목표 = 100 + 0.5 × 4 = 102
  assert.match(m.sentence, /102\.00 위로 올라가면 사요/);
  assert.match(m.distance, /\+1\.0% 더 오르면/);
  assert.equal(m.reached, false);
  assert.equal(m.priceLine.price, 102);
  assert.equal(m.nextCheck, "20:00"); // 진행 중 봉(19:00 KST 시작)의 마감
  assert.equal(m.checks, 3); // 시작 16:00 KST 뒤로 마감된 봉 17·18·19시
  assert.equal(m.log[0].at, "19:00");
  assert.match(m.log[0].text, /목표 102/);
});

test("RSI — 기준 아래로 내려가야 사고, 거리는 RSI 차이", () => {
  const closes = [];
  for (let i = 0; i < 40; i += 1) closes.push(100 + (i % 2 ? 1 : -1) * 0.5 + i * 0.05);
  const macro = { rule_type: "F", candle_interval: "1h", params: { rsi_period: 7, entry_threshold: 33, exit_threshold: 75 } };
  const m = entryWaitModel({ macro, session: { last_price: closes[closes.length - 1] }, candles: bars(closes) });
  assert.match(m.sentence, /RSI\(7\)가 33 아래로 내려가면 사요/);
  assert.match(m.distance, /지금 RSI \d+\.\d · \d+\.\d 더 내려가면/);
  assert.equal(m.meter.to.value, "33");
  assert.equal(m.meter.from.value, "75");
});

test("볼린저 되돌림은 하단 밴드, 숏은 상단 밴드", () => {
  const closes = Array.from({ length: 30 }, (_, i) => 100 + Math.sin(i) * 2);
  const long = entryWaitModel({ macro: { rule_type: "G", candle_interval: "1h", params: { bb_period: 20, bb_std: 2 } }, candles: bars(closes) });
  assert.match(long.sentence, /하단 밴드 .* 아래면 사요/);
  const short = entryWaitModel({ macro: { rule_type: "G", candle_interval: "1h", position_side: "short", params: { bb_period: 20, bb_std: 2 } }, candles: bars(closes) });
  assert.match(short.sentence, /상단 밴드 .* 위면 팔아서 들어가요/);
});

test("이평선 — 단기선이 아래면 간격, 위에 있으면 남은 확인 봉 수", () => {
  const down = Array.from({ length: 80 }, (_, i) => 200 - i);
  const m = entryWaitModel({ macro: { rule_type: "J", candle_interval: "1h", params: { fast_period: 5, slow_period: 20 } }, candles: bars(down) });
  assert.match(m.sentence, /5선이 20선 위로 올라가면 사요/);
  assert.match(m.distance, /두 선 간격 \d+\.\d+%/);
  const up = Array.from({ length: 80 }, (_, i) => 100 + i);
  const m2 = entryWaitModel({ macro: { rule_type: "J", candle_interval: "1h", params: { fast_period: 5, slow_period: 20, confirm_candles: 3 } }, candles: bars(up) });
  assert.equal(m2.reached, true);
  assert.match(m2.distance, /5선이 위에 있어요/);
});

test("지정가 밴드는 실시간, 눌림 진입은 서버 목표만", () => {
  const b = entryWaitModel({ macro: { rule_type: "B", symbol: "KRW-WAL", params: { buy_price: 60, sell_price: 70 } }, session: { last_price: 62, symbol: "KRW-WAL" }, candles: [] });
  assert.match(b.sentence, /60 이하로 내려오면 사요/);
  assert.match(b.distance, /−3\.2% 더 내려오면/);
  assert.equal(b.nextCheck, "실시간");
  const e = entryWaitModel({ macro: { rule_type: "E", params: { entry_mode: "dip", entry_dip: 3 } }, session: { last_price: 100 }, levels: { entry_target: 97 } });
  assert.match(e.sentence, /97\.00까지 내려오면 사요/);
  assert.match(e.distance, /−3\.0% 더 내려오면/);
  const pending = entryWaitModel({ macro: { rule_type: "E", params: { entry_mode: "dip", entry_dip: 3 } }, session: { last_price: 100 }, levels: { pending: true } });
  assert.match(pending.distance, /첫 봉이 마감되면/);
});

test("평소 기다린 시간 — 조건이 새로 맞은 횟수로 나눈다", () => {
  const rows = [false, true, true, false, false, true, false, false, false, false, true, false].map((hit) => ({ hit }));
  const stat = typicalWait(rows, H);
  assert.equal(stat.episodes, 3);
  assert.equal(stat.spanMs, 12 * H);
  assert.equal(stat.avgMs, 4 * H);
  assert.equal(typicalWait(rows.slice(0, 5), H), null, "봉이 너무 적으면 어림하지 않는다");
  assert.equal(fmtRoughSpan(9 * H), "약 9시간");
  assert.equal(fmtRoughSpan(60 * H), "약 3일");
  assert.equal(fmtSpan(134 * 60_000), "2시간 14분");
});

test("서버 entry_wait 를 화면 모델로 옮긴다", async () => {
  const { entryWaitFromServer } = await import("../src/lib/entryWait.js");
  const now = Date.UTC(2026, 9, 16, 13, 13);
  const m = entryWaitFromServer({
    rule: "I", interval: "1h", interval_label: "1시간", realtime: false, sentence: "가격이 209.52 위로 올라가면 사요.",
    distance: "지금 207.46 · +1.0% 더 오르면", reached: false, meter: { from: { label: "이 봉 시가", value: "207.20" }, to: { label: "사는 선", value: "209.52" }, now: "207.46", progress: 0.1 },
    buy_line: { price: 209.52, label: "사는 선 209.52" }, next_check_ms: Date.UTC(2026, 9, 16, 14, 0), checks: 3,
    typical: { episodes: 74, span_ms: 300 * 3_600_000, avg_ms: 4 * 3_600_000, longest_ms: 0 },
    log: [{ at_ms: Date.UTC(2026, 9, 16, 13, 0), text: "고가 211.42 · 사는 선 212.93", hit: false }],
  }, { started_at: new Date(now - 135 * 60_000).toISOString(), last_price: 207.46 }, now);
  assert.equal(m.nextCheck, "23:00");
  assert.equal(m.nextCheckIn, "47분 뒤");
  assert.equal(m.waitingFor, "2시간 15분");
  assert.equal(m.log[0].at, "22:00");
  assert.match(m.priceLine.label, /사는 선 209\.52 · \+1\.0%/);
  assert.equal(m.typical.avgMs, 4 * 3_600_000);
  assert.equal(entryWaitFromServer(null), null);
});
