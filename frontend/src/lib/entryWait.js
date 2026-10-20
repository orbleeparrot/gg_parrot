// 진입을 기다리는 매크로(변동성 돌파 · 트레일링 스탑 눌림 진입 · RSI · 볼린저 · 이평선 교차 · 지정가 밴드)의
// '무엇을 · 얼마나 · 언제' 를 한 모델로 만든다. 내 에이전트(진입 조건 카드 · 확인 기록 · 차트의 사는 선)와
// 빠른 실행(시작 전 안내)이 같은 판단을 쓴다.
//
// 판정은 서버 엔진(backend/engine/candles.py · stepper.py)과 같은 규칙을 공개 캔들로 다시 계산한 것이다.
//  - I  변동성 돌파: 봉 고가 ≥ 그 봉 시가 + k × 직전 봉 변동폭 (+ 이평선 필터면 마감가 ≥ 이평선) — 봉 마감 때 판정
//  - F  RSI: 마감 RSI ≤ 진입 기준(숏은 ≥ 청산 기준)이 확인 봉 수만큼 이어지면
//  - G  볼린저: 되돌림은 마감가 ≤ 하단(숏 ≥ 상단), 돌파는 마감가 ≥ 상단(숏 ≤ 하단)
//  - J  이평선: 단기선 > 장기선(숏은 <)이 확인 봉 수만큼 이어지면
//  - B  지정가 밴드: 가격 ≤ 매수가(숏 ≥ 매도가) — 3초마다 실시간 판정
//  - E  트레일링 스탑 눌림 진입: 서버가 보낸 진입 목표(strategy_levels.entry_target)만 쓴다(기준가는 서버 상태).
// 진입을 막는 쿨다운 · 진입 보류 · 하루 손실 중단은 서버만 알아서 여기서는 다루지 않는다.
import { ema, rsi, sma } from "./indicators.js";
import { fmtPrice } from "./format.js";

export const WAITING_RULES = new Set(["B", "E", "F", "G", "I", "J"]);

const STEP_MS = {
  "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
  "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000, "8h": 28_800_000, "12h": 43_200_000,
  "1d": 86_400_000, "3d": 259_200_000, "1w": 604_800_000,
};
const INTERVAL_LABEL = {
  "1m": "1분", "3m": "3분", "5m": "5분", "15m": "15분", "30m": "30분", "1h": "1시간", "2h": "2시간", "4h": "4시간",
  "6h": "6시간", "8h": "8시간", "12h": "12시간", "1d": "1일", "3d": "3일", "1w": "1주",
};

export function intervalMs(interval) {
  return STEP_MS[interval] || STEP_MS["1d"];
}
export function intervalLabel(interval) {
  return INTERVAL_LABEL[interval] || interval || "1일";
}

const num = (v) => (v === null || v === undefined || v === "" ? NaN : Number(v));
const ok = (v) => Number.isFinite(v);

function kstClock(ms) {
  const d = new Date(ms + 9 * 3_600_000);
  return `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`;
}
function kstDayClock(ms, now) {
  const a = new Date(ms + 9 * 3_600_000);
  const b = new Date(now + 9 * 3_600_000);
  const same = a.getUTCFullYear() === b.getUTCFullYear() && a.getUTCMonth() === b.getUTCMonth() && a.getUTCDate() === b.getUTCDate();
  return same ? kstClock(ms) : `${a.getUTCMonth() + 1}/${a.getUTCDate()} ${kstClock(ms)}`;
}

// "18분" · "2시간 14분" · "1일 3시간"
export function fmtSpan(ms) {
  const m = Math.max(0, Math.round(ms / 60_000));
  if (m < 60) return `${m}분`;
  const h = Math.floor(m / 60);
  if (h < 24) return m % 60 ? `${h}시간 ${m % 60}분` : `${h}시간`;
  const d = Math.floor(h / 24);
  return h % 24 ? `${d}일 ${h % 24}시간` : `${d}일`;
}
// 대략값 — "약 9시간" · "약 2일"
export function fmtRoughSpan(ms) {
  const h = ms / 3_600_000;
  if (h < 1) return `약 ${Math.max(1, Math.round(ms / 60_000))}분`;
  if (h < 48) return `약 ${Math.round(h)}시간`;
  return `약 ${Math.round(h / 24)}일`;
}

function pctText(from, to) {
  if (!ok(from) || !ok(to) || from <= 0) return "";
  const pct = ((to - from) / from) * 100;
  const abs = Math.abs(pct);
  return `${pct >= 0 ? "+" : "−"}${abs < 10 ? abs.toFixed(1) : abs.toFixed(0)}%`;
}

function clamp01(v) {
  return ok(v) ? Math.min(1, Math.max(0, v)) : 0;
}

function rollingStd(values, period, means) {
  return values.map((_, i) => {
    if (means[i] == null || i < period - 1) return null;
    let s = 0;
    for (let j = i - period + 1; j <= i; j += 1) s += (values[j] - means[i]) ** 2;
    return Math.sqrt(s / period);
  });
}

// 규칙마다 '봉 하나에서 조건이 맞았는가' 와 그 봉의 값. closed 봉 배열을 받는다.
function barSeries(rule, params, side, bars) {
  const short = side === "short";
  const closes = bars.map((b) => b.c);
  switch (rule) {
    case "I": {
      const k = ok(num(params.k)) ? num(params.k) : 0.5;
      const maPeriod = Math.round(num(params.ma_filter_period)) || 0;
      const ma = maPeriod > 0 ? sma(closes, maPeriod) : null;
      return bars.map((b, i) => {
        if (i === 0) return null;
        const target = b.o + k * (bars[i - 1].h - bars[i - 1].l);
        const maOk = !ma || ma[i] == null || b.c >= ma[i];
        return { hit: b.h >= target && maOk, value: b.h, target };
      });
    }
    case "F": {
      const period = Math.round(num(params.rsi_period)) || 14;
      const entry = ok(num(params.entry_threshold)) ? num(params.entry_threshold) : 30;
      const exit = ok(num(params.exit_threshold)) ? num(params.exit_threshold) : 70;
      const r = rsi(closes, period);
      return r.map((v) => (v == null ? null : { hit: short ? v >= exit : v <= entry, value: v, target: short ? exit : entry }));
    }
    case "G": {
      const period = Math.round(num(params.bb_period)) || 20;
      const k = num(params.bb_std) || 2;
      const mid = sma(closes, period);
      const sd = rollingStd(closes, period, mid);
      const breakout = params.strategy === "breakout";
      // '변동성이 줄어든 구간만 사용' — 엔진처럼 이번 봉 밴드 폭이 최근 squeeze_lookback 봉(이번 봉 포함) 중 가장 좁은 폭의 1.05배 이하일 때만
      const squeeze = Boolean(params.squeeze_filter);
      const lookback = Math.round(num(params.squeeze_lookback)) || 50;
      const widths = [];
      return bars.map((b, i) => {
        if (mid[i] == null || sd[i] == null) return null;
        const upper = mid[i] + k * sd[i];
        const lower = mid[i] - k * sd[i];
        widths.push(mid[i] ? (upper - lower) / mid[i] : 0);
        if (widths.length > lookback) widths.shift();
        const squeezed = !squeeze || widths.length < 2 || widths[widths.length - 1] <= Math.min(...widths) * 1.05;
        const useUpper = breakout ? !short : short;
        const target = useUpper ? upper : lower;
        const hit = (useUpper ? b.c >= upper : b.c <= lower) && squeezed;
        return { hit, value: b.c, target, mid: mid[i] };
      });
    }
    case "J": {
      const fp = Math.round(num(params.fast_period)) || 20;
      const sp = Math.round(num(params.slow_period)) || 60;
      const useEma = params.ma_type === "EMA";
      const fast = useEma ? ema(closes, fp) : sma(closes, fp);
      const slow = useEma ? ema(closes, sp) : sma(closes, sp);
      return bars.map((_, i) => {
        if (fast[i] == null || slow[i] == null) return null;
        const gap = (fast[i] - slow[i]) / slow[i];
        return { hit: short ? gap < 0 : gap > 0, value: gap, fast: fast[i], slow: slow[i] };
      });
    }
    case "B": {
      const buy = num(params.buy_price);
      const sell = num(params.sell_price);
      const target = short ? sell : buy;
      return bars.map((b) => (ok(target) ? { hit: short ? b.h >= target : b.l <= target, value: short ? b.h : b.l, target } : null));
    }
    default:
      return bars.map(() => null);
  }
}

// 확인 봉 수(confirm)까지 이어져야 사는 규칙(F · J)은 '연속' 으로 접는다.
function withConfirm(series, confirm) {
  let streak = 0;
  return series.map((row) => {
    if (!row) { streak = 0; return row; }
    streak = row.hit ? streak + 1 : 0;
    return { ...row, streak, enter: streak >= confirm };
  });
}

// 최근 봉에서 '조건이 새로 맞은 횟수' 와 그 사이 평균 시간 — 서버 백테스트 대신 공개 캔들로 어림한 값.
export function typicalWait(rows, stepMs) {
  const valid = rows.filter(Boolean);
  if (valid.length < 10) return null;
  let episodes = 0;
  let prev = false;
  for (const row of rows) {
    const on = Boolean(row && (row.enter ?? row.hit));
    if (on && !prev) episodes += 1;
    prev = on;
  }
  const spanMs = valid.length * stepMs;
  return { episodes, spanMs, avgMs: episodes > 0 ? spanMs / episodes : null };
}

export function isWaitingMacro(macro) {
  if (!macro || !WAITING_RULES.has(macro.rule_type)) return false;
  if (macro.rule_type === "E") return (macro.params || {}).entry_mode === "dip";
  return true;
}

/**
 * 진입 대기 모델. 해당 없으면 null.
 * @param {{macro: object, session?: object, candles?: object[], levels?: object, now?: number}} input
 *   candles 는 매크로의 봉 간격과 같은 간격이어야 한다(마지막 봉은 closed:false 인 진행 중 봉일 수 있다).
 */
export function entryWaitModel({ macro, session = null, candles = [], levels = null, now = Date.now() }) {
  if (!isWaitingMacro(macro)) return null;
  if (session && session.in_position) return null;
  const rule = macro.rule_type;
  const params = macro.params || {};
  const side = session?.position_side || macro.position_side || "long";
  const short = side === "short";
  const interval = macro.candle_interval || "1d";
  const step = intervalMs(interval);
  const all = Array.isArray(candles) ? candles.filter((b) => b && ok(b.c)) : [];
  const closed = all.filter((b) => b.closed !== false);
  const forming = all.length && all[all.length - 1].closed === false ? all[all.length - 1] : null;
  const price = num(session?.last_price) > 0 ? num(session.last_price) : forming ? forming.c : closed.length ? closed[closed.length - 1].c : NaN;
  const confirm = Math.max(1, Math.round(num(params.confirm_candles)) || 1);
  const sym = session?.symbol || macro.symbol || "";
  const fp = (v) => fmtPrice(v, sym);

  const startedMs = session?.started_at ? Date.parse(session.started_at) : NaN;
  const lastClosed = closed.length ? closed[closed.length - 1] : null;
  const lastCloseMs = lastClosed ? lastClosed.t + step : NaN;
  const realtime = rule === "B";
  const nextCheckMs = realtime ? null : forming ? forming.t + step : ok(lastCloseMs) ? lastCloseMs + step : null;
  const checkedBars = ok(startedMs) ? closed.filter((b) => b.t + step > startedMs) : [];

  let series = barSeries(rule, params, side, closed);
  if (rule === "F" || rule === "J") series = withConfirm(series, confirm);
  const waitStats = rule === "E" ? null : typicalWait(series, step);

  const model = {
    rule,
    interval,
    intervalLabel: intervalLabel(interval),
    realtime,
    waitingFor: ok(startedMs) ? fmtSpan(now - startedMs) : "",
    sentence: "",
    distance: "",
    reached: false,
    meter: null,
    priceLine: null,
    nextCheck: realtime ? "실시간" : ok(nextCheckMs) ? kstClock(nextCheckMs) : "—",
    nextCheckIn: realtime ? "가격이 닿는 순간" : ok(nextCheckMs) ? (nextCheckMs > now ? `${fmtSpan(nextCheckMs - now)} 뒤` : "곧") : "",
    checks: realtime ? null : checkedBars.length,
    typical: waitStats,
    log: [],
  };

  const live = (closesTail) => [...closed.map((b) => b.c), closesTail];

  if (rule === "I") {
    const prev = forming ? lastClosed : closed[closed.length - 2];
    const bar = forming || lastClosed;
    if (!bar || !prev) return { ...model, sentence: "첫 봉이 마감되면 사는 가격이 정해져요." };
    const k = ok(num(params.k)) ? num(params.k) : 0.5;
    const target = bar.o + k * (prev.h - prev.l);
    const reached = (forming ? forming.h : NaN) >= target || price >= target;
    Object.assign(model, {
      sentence: `가격이 ${fp(target)} 위로 올라가면 사요.`,
      distance: reached ? "목표를 넘었어요 · 이 봉이 마감되면 사요" : `지금 ${fp(price)} · ${pctText(price, target)} 더 오르면`,
      reached,
      meter: { from: { label: "이 봉 시가", value: fp(bar.o) }, to: { label: "목표", value: fp(target) }, now: fp(price),
        progress: clamp01((price - bar.o) / (target - bar.o)) },
      priceLine: { price: target, label: `사는 선 ${fp(target)} · ${pctText(price, target) || "도달"}` },
      note: `목표는 ${intervalLabel(interval)} 봉마다 새로 정해져요.`,
    });
  } else if (rule === "F") {
    const period = Math.round(num(params.rsi_period)) || 14;
    const entry = ok(num(params.entry_threshold)) ? num(params.entry_threshold) : 30;
    const exit = ok(num(params.exit_threshold)) ? num(params.exit_threshold) : 70;
    const r = rsi(live(ok(price) ? price : closed.length ? closed[closed.length - 1].c : NaN), period);
    const now_ = r[r.length - 1];
    const th = short ? exit : entry;
    const reached = ok(now_) && (short ? now_ >= th : now_ <= th);
    const confirmText = confirm > 1 ? ` ${confirm}봉 연속` : "";
    Object.assign(model, {
      sentence: short ? `RSI(${period})가 ${th} 위로 오르면${confirmText} 팔아서 들어가요.` : `RSI(${period})가 ${th} 아래로 내려가면${confirmText} 사요.`,
      distance: !ok(now_) ? "RSI를 계산하고 있어요" : reached ? `지금 RSI ${now_.toFixed(1)} · 조건이 맞았어요, 봉이 마감되면 사요`
        : `지금 RSI ${now_.toFixed(1)} · ${Math.abs(now_ - th).toFixed(1)} 더 ${short ? "오르면" : "내려가면"}`,
      reached,
      meter: ok(now_) ? { from: { label: "청산 기준", value: String(short ? entry : exit) }, to: { label: "사는 선", value: String(th) },
        now: now_.toFixed(1), progress: clamp01(short ? (now_ - entry) / (exit - entry) : (exit - now_) / (exit - entry)) } : null,
    });
  } else if (rule === "G") {
    const period = Math.round(num(params.bb_period)) || 20;
    const k = num(params.bb_std) || 2;
    const closes = live(ok(price) ? price : NaN);
    const mid = sma(closes, period);
    const sd = rollingStd(closes, period, mid);
    const m = mid[mid.length - 1];
    const s = sd[sd.length - 1];
    if (!ok(m) || !ok(s)) return { ...model, sentence: "밴드를 계산하고 있어요." };
    const breakout = params.strategy === "breakout";
    const useUpper = breakout ? !short : short;
    const target = useUpper ? m + k * s : m - k * s;
    const reached = useUpper ? price >= target : price <= target;
    const name = useUpper ? "상단" : "하단";
    const verb = short ? "팔아서 들어가요" : "사요";
    Object.assign(model, {
      sentence: `봉 마감가가 ${name} 밴드 ${fp(target)} ${useUpper ? "위" : "아래"}면 ${verb}.`,
      distance: reached ? `지금 ${fp(price)} · 밴드를 넘었어요, 봉이 마감되면 ${verb}` : `지금 ${fp(price)} · ${pctText(price, target)} 더 ${useUpper ? "오르면" : "내려가면"}`,
      reached,
      meter: { from: { label: "중앙선", value: fp(m) }, to: { label: `${name} 밴드`, value: fp(target) }, now: fp(price),
        progress: clamp01((price - m) / (target - m)) },
      priceLine: { price: target, label: `사는 선(${name} 밴드) ${fp(target)} · ${pctText(price, target) || "도달"}` },
    });
  } else if (rule === "J") {
    const fpd = Math.round(num(params.fast_period)) || 20;
    const spd = Math.round(num(params.slow_period)) || 60;
    const closes = live(ok(price) ? price : NaN);
    const useEma = params.ma_type === "EMA";
    const fast = useEma ? ema(closes, fpd) : sma(closes, fpd);
    const slow = useEma ? ema(closes, spd) : sma(closes, spd);
    const f = fast[fast.length - 1];
    const sl = slow[slow.length - 1];
    if (!ok(f) || !ok(sl)) return { ...model, sentence: "이동평균을 계산하고 있어요." };
    const gap = (f - sl) / sl;
    const prevRow = series[series.length - 1];
    const before = series.length > 5 ? series[series.length - 6] : null;
    const narrowing = before && ok(before.value) ? Math.abs(gap) < Math.abs(before.value) : null;
    const reached = short ? gap < 0 : gap > 0;
    const streak = prevRow?.streak || 0;
    Object.assign(model, {
      sentence: short ? `${fpd}선이 ${spd}선 아래로 내려가면 팔아서 들어가요.` : `${fpd}선이 ${spd}선 위로 올라가면 사요.`,
      distance: reached
        ? `${fpd}선이 ${short ? "아래" : "위"}에 있어요 · ${Math.max(1, confirm - streak)}봉 더 이어지면 사요`
        : `두 선 간격 ${(Math.abs(gap) * 100).toFixed(2)}%${narrowing === null ? "" : narrowing ? " · 좁혀지는 중" : " · 벌어지는 중"}`,
      reached,
    });
  } else if (rule === "B") {
    const target = short ? num(params.sell_price) : num(params.buy_price);
    const other = short ? num(params.buy_price) : num(params.sell_price);
    if (!ok(target)) return null;
    const reached = short ? price >= target : price <= target;
    Object.assign(model, {
      sentence: short ? `가격이 ${fp(target)} 이상으로 오르면 팔아서 들어가요.` : `가격이 ${fp(target)} 이하로 내려오면 사요.`,
      distance: reached ? "가격이 닿았어요 · 곧 사요" : `지금 ${fp(price)} · ${pctText(price, target)} 더 ${short ? "오르면" : "내려오면"}`,
      reached,
      meter: ok(other) ? { from: { label: short ? "청산가" : "매도가", value: fp(other) }, to: { label: "사는 가격", value: fp(target) }, now: fp(price),
        progress: clamp01((price - other) / (target - other)) } : null,
    });
  } else if (rule === "E") {
    const target = num(levels?.entry_target);
    const dip = ok(num(params.entry_dip)) ? num(params.entry_dip) : 3;
    if (!ok(target) || target <= 0) {
      return { ...model, sentence: `기준가에서 ${dip}% 내려오면 사요.`, distance: "첫 봉이 마감되면 사는 가격이 정해져요." };
    }
    const ref = target / (1 - dip / 100);
    const reached = price <= target;
    Object.assign(model, {
      sentence: `가격이 ${fp(target)}까지 내려오면 사요.`,
      distance: reached ? "가격이 닿았어요 · 봉이 마감되면 사요" : `지금 ${fp(price)} · ${pctText(price, target)} 더 내려오면`,
      reached,
      meter: { from: { label: "기준가", value: fp(ref) }, to: { label: "사는 가격", value: fp(target) }, now: fp(price),
        progress: clamp01((ref - price) / (ref - target)) },
    });
  }

  // 확인 기록 — 실행을 시작한 뒤 마감된 봉마다 한 줄(최근 것이 위).
  if (!realtime && rule !== "E") {
    const startIndex = closed.findIndex((b) => b.t + step > startedMs);
    if (startIndex >= 0) {
      const rows = [];
      for (let i = closed.length - 1; i >= startIndex && rows.length < 6; i -= 1) {
        const row = series[i];
        if (!row) continue;
        rows.push({ at: kstDayClock(closed[i].t + step, now), ...describe(rule, row, fp, confirm), hit: Boolean(row.enter ?? row.hit) });
      }
      model.log = rows;
      model.startedAt = ok(startedMs) ? kstDayClock(startedMs, now) : "";
    }
  }
  return model;
}

function describe(rule, row, fp, confirm) {
  switch (rule) {
    case "I":
      return { text: `고가 ${fp(row.value)} · 목표 ${fp(row.target)}` };
    case "F":
      return { text: `RSI ${row.value.toFixed(1)} · 사는 선 ${row.target}${confirm > 1 ? ` · ${row.streak}/${confirm}봉` : ""}` };
    case "G":
      return { text: `마감 ${fp(row.value)} · 밴드 ${fp(row.target)}` };
    case "J":
      return { text: `두 선 간격 ${(row.value * 100).toFixed(2)}%${confirm > 1 ? ` · ${row.streak}/${confirm}봉` : ""}` };
    default:
      return { text: "" };
  }
}

// 서버가 보낸 진입 대기(세션 응답 entry_wait — 실행기 하트비트와 같은 값)를 이 화면의 모델 모양으로 옮긴다.
// 서버 값이 있으면 화면 계산 대신 이것을 써서 실행기와 웹이 같은 숫자를 보게 한다.
export function entryWaitFromServer(wait, session = null, now = Date.now()) {
  if (!wait || typeof wait !== "object" || !wait.sentence) return null;
  const startedMs = session?.started_at ? Date.parse(session.started_at) : NaN;
  const price = Number(session?.last_price);
  const next = Number(wait.next_check_ms);
  const typical = wait.typical
    ? { episodes: wait.typical.episodes, spanMs: wait.typical.span_ms, avgMs: wait.typical.avg_ms ?? null }
    : null;
  const line = wait.buy_line && Number.isFinite(Number(wait.buy_line.price))
    ? { price: Number(wait.buy_line.price), label: `${wait.buy_line.label} · ${pctText(price, Number(wait.buy_line.price)) || "도달"}` }
    : null;
  return {
    rule: wait.rule,
    interval: wait.interval,
    intervalLabel: wait.interval_label || intervalLabel(wait.interval),
    realtime: Boolean(wait.realtime),
    waitingFor: Number.isFinite(startedMs) ? fmtSpan(now - startedMs) : "",
    sentence: wait.sentence,
    // 맨 위 큰 글씨 — 지금 상태 한 줄(옛 서버엔 없어 조건 문장으로)
    headline: wait.headline || String(wait.sentence || "").replace(/\.$/, ""),
    distance: wait.distance || "",
    // 언제 판정되는지 — "11:45 마감까지 5분". 지정가는 3초마다 봐서 '실시간 확인'.
    closeText: wait.realtime ? "실시간 확인" : Number.isFinite(next) && next > 0
      ? (next - now >= 60_000 ? `${kstClock(next)} 마감까지 ${fmtSpan(next - now)}` : `${kstClock(next)} 마감 곧`) : "",
    // 가격 막대 하나에 선 여럿(사는 선 · 20봉 평균) — 두 조건이 다 있는 매크로만
    // scale 은 같은 모양에 사는 구역 방향(zone_side)을 담은 값 눈금(RSI · 밴드 · 지정가 · 눌림) — 있으면 먼저.
    ladder: [wait.scale, wait.ladder].find((l) => l && Array.isArray(l.marks) && l.now) || null,
    reached: Boolean(wait.reached),
    // 함께 맞아야 하는 조건 목록 — 첫 줄은 주 조건. 두 줄 이상일 때만 카드에 체크 목록으로 보인다.
    conditions: Array.isArray(wait.conditions)
      ? wait.conditions.filter((row) => row && row.label).map((row) => ({
        key: String(row.key || row.label), label: String(row.label), now: String(row.now || ""),
        ok: row.ok === true ? true : row.ok === false ? false : null, atClose: Boolean(row.at_close),
      }))
      : [],
    meter: wait.meter || null,
    priceLine: line,
    nextCheck: wait.realtime ? "실시간" : Number.isFinite(next) && next > 0 ? kstClock(next) : "—",
    nextCheckIn: wait.realtime ? "가격이 닿는 순간" : Number.isFinite(next) && next > 0 ? (next > now ? `${fmtSpan(next - now)} 뒤` : "곧") : "",
    checks: wait.checks ?? null,
    typical,
    log: (wait.log || []).map((row) => ({ at: kstDayClock(Number(row.at_ms), now), text: row.text, hit: Boolean(row.hit) })),
    startedAt: Number.isFinite(startedMs) ? kstDayClock(startedMs, now) : "",
  };
}
