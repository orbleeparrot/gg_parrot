// 실시간 차트 보조지표(오버레이) 계산.
//
// 각 매매 방식(rule_type)이 실제로 무엇을 보고 사고파는지를 초보자가 한눈에
// 알 수 있도록, 캔들 배열로부터 밴드/이동평균/지정가선/신호마커/RSI를 계산한다.
// 순수 함수만 모아 두고, 그리기는 CandleChart 가 맡는다.
//
// 반환 스펙(모두 선택적):
//   legend:     [{ label, color, dash?, kind? }]  범례
//   note:       string                            초보자용 한 줄 설명
//   priceLines: [{ price, color, label?, dash? }] 수평 가격선(지정가·평단·격자…)
//   series:     [{ id, color, label, values, width?, dash? }] 캔들과 평행한 선(밴드·MA)
//   bands:      [{ upper, lower, fill }]           두 선 사이 음영(볼린저 등)
//   markers:    [{ index, kind:'buy'|'sell', label? }] 캔들 인덱스 위 신호 삼각형
//   rsi:        { values, entry, exit } | null      아래 보조창(오실레이터)
//
// series/bands/rsi 의 values 는 candles 와 길이가 같은 배열이며, 계산 불가한
// 구간(초반 등)은 null 로 비워 둔다.
import { fmtPrice } from "./format.js";

// 색 — 캔들의 상승/하락 색은 CSS 변수를 따르고, 보조선은 테마 양쪽에서 보이는
// 중간톤을 직접 지정한다.
const UP = "rgb(var(--chart-up))"; // 매수·이익 방향
const DOWN = "rgb(var(--chart-down))"; // 매도·손실 방향
const MID = "rgb(99 102 241)"; // 인디고 — 중앙선/기준선
const BAND = "rgb(148 163 184)"; // 슬레이트 — 밴드 경계
const FILL = "rgba(99,102,241,0.08)"; // 밴드 음영
const FAST = "rgb(234 88 12)"; // 오렌지 — 단기선
const SLOW = "rgb(99 102 241)"; // 인디고 — 장기선
const NEUTRAL = "rgb(148 163 184)"; // 참고선
const ENTRY = "rgb(99 102 241)"; // 내 평단

const num = (v) => Number(v);
const ok = (v) => Number.isFinite(v);

// --- 지표 수식 ---------------------------------------------------------
export function sma(values, period) {
  const out = new Array(values.length).fill(null);
  if (!(period > 0)) return out;
  let sum = 0;
  const q = [];
  for (let i = 0; i < values.length; i++) {
    q.push(values[i]);
    sum += values[i];
    if (q.length > period) sum -= q.shift();
    if (q.length === period) out[i] = sum / period;
  }
  return out;
}

export function ema(values, period) {
  const out = new Array(values.length).fill(null);
  if (!(period > 0)) return out;
  const k = 2 / (period + 1);
  let prev = null;
  for (let i = 0; i < values.length; i++) {
    if (prev == null) {
      if (i >= period - 1) {
        let s = 0;
        for (let j = i - period + 1; j <= i; j++) s += values[j];
        prev = s / period;
        out[i] = prev;
      }
    } else {
      prev = values[i] * k + prev * (1 - k);
      out[i] = prev;
    }
  }
  return out;
}

// 표본이 아닌 모집단 표준편차(볼린저밴드 관례).
function rollingStd(values, period, means) {
  const out = new Array(values.length).fill(null);
  if (!(period > 0)) return out;
  for (let i = period - 1; i < values.length; i++) {
    const m = means[i];
    if (m == null) continue;
    let s = 0;
    for (let j = i - period + 1; j <= i; j++) {
      const d = values[j] - m;
      s += d * d;
    }
    out[i] = Math.sqrt(s / period);
  }
  return out;
}

export function rsi(values, period) {
  const out = new Array(values.length).fill(null);
  if (!(period > 0) || values.length <= period) return out;
  let gain = 0;
  let loss = 0;
  for (let i = 1; i < values.length; i++) {
    const ch = values[i] - values[i - 1];
    const g = Math.max(0, ch);
    const l = Math.max(0, -ch);
    if (i <= period) {
      gain += g;
      loss += l;
      if (i === period) {
        gain /= period;
        loss /= period;
        out[i] = loss === 0 ? 100 : 100 - 100 / (1 + gain / loss);
      }
    } else {
      gain = (gain * (period - 1) + g) / period;
      loss = (loss * (period - 1) + l) / period;
      out[i] = loss === 0 ? 100 : 100 - 100 / (1 + gain / loss);
    }
  }
  return out;
}

// a 가 b 를 위로/아래로 교차한 지점을 매수/매도 마커로. 연속 신호는 첫 봉만.
function crossMarkers(a, b, buyLabel, sellLabel, invert = false) {
  const out = [];
  for (let i = 1; i < a.length; i++) {
    if (a[i] == null || b[i] == null || a[i - 1] == null || b[i - 1] == null) continue;
    const upCross = a[i - 1] <= b[i - 1] && a[i] > b[i];
    const downCross = a[i - 1] >= b[i - 1] && a[i] < b[i];
    if (upCross) out.push({ index: i, kind: invert ? "sell" : "buy", label: invert ? sellLabel : buyLabel });
    else if (downCross) out.push({ index: i, kind: invert ? "buy" : "sell", label: invert ? buyLabel : sellLabel });
  }
  return out;
}

const priceLine = (price, color, label, dash) =>
  ok(price) && price > 0 ? { price, color, label, dash } : null;

// --- 내 평단(마이페이지 실행 세션용) -----------------------------------
export function entryPriceOverlay(entryPrice, side) {
  const p = num(entryPrice);
  if (!ok(p) || p <= 0) return null;
  return {
    legend: [{ label: "내 평단(평균 진입가)", color: ENTRY, dash: "5 3" }],
    priceLines: [{ price: p, color: ENTRY, dash: "5 3", label: `내 평단 ${fmtPrice(p)}` }],
    note:
      side === "short"
        ? "점선이 내 평단이에요. 가격이 평단보다 내려가면 이익, 올라가면 손실이에요."
        : "점선이 내 평단이에요. 가격이 평단보다 올라가면 이익, 내려가면 손실이에요.",
  };
}

// --- 매매 방식별 보조지표 ----------------------------------------------
// form 은 빌더 폼(또는 저장된 매크로 params 를 펼친 것)과 같은 키를 쓴다.
export function computeStrategyOverlay(form, candles) {
  if (!form || !candles || candles.length === 0) return null;
  const closes = candles.map((k) => k.c);
  const last = closes[closes.length - 1];
  const rt = form.rule_type;
  const isShort = form.position_side === "short";

  const buy = { color: UP };
  const sell = { color: DOWN };

  const emptyLines = [];
  const push = (arr, line) => {
    if (line) arr.push(line);
  };

  switch (rt) {
    case "A": {
      // 지금 가격에 진입한다고 가정한 익절·손절선(초보자용 예시).
      const tp = num(form.take_profit_pct);
      const sl = num(form.stop_loss_pct);
      const lines = [];
      push(lines, priceLine(last, NEUTRAL, `현재가 ${fmtPrice(last)}`, "2 3"));
      if (tp > 0) {
        const tpPrice = isShort ? last * (1 - tp / 100) : last * (1 + tp / 100);
        push(lines, priceLine(tpPrice, UP, `익절 ${isShort ? "-" : "+"}${tp}%`, "5 3"));
      }
      if (form.use_stop_loss && sl > 0) {
        const slPrice = isShort ? last * (1 + sl / 100) : last * (1 - sl / 100);
        push(lines, priceLine(slPrice, DOWN, `손절 ${isShort ? "+" : "-"}${sl}%`, "5 3"));
      }
      return {
        legend: [
          { label: "익절선", color: UP },
          { label: "손절선", color: DOWN },
        ],
        priceLines: lines,
        note: "지금 가격에 들어간다고 가정한 익절·손절선이에요. 실제 진입가에 따라 위아래로 함께 움직여요.",
      };
    }

    case "B": {
      const buyP = num(form.buy_price);
      const sellP = num(form.sell_price);
      const lines = [];
      push(lines, priceLine(buyP, UP, `여기서 매수 · ${fmtPrice(buyP)}`, "5 3"));
      push(lines, priceLine(sellP, DOWN, `여기서 매도 · ${fmtPrice(sellP)}`, "5 3"));
      return {
        legend: [
          { label: "매수 지정가", color: UP },
          { label: "매도 지정가", color: DOWN },
        ],
        priceLines: lines,
        note: "가격이 매수선까지 내려오면 사고, 매도선까지 오르면 팔아요. 두 선 사이를 오갈 때 수익이 나요.",
      };
    }

    case "C":
      return {
        legend: [],
        note: "정기 분할매수(DCA)는 정해진 간격마다 같은 금액으로 계속 사요. 특정 가격을 노리지 않아 차트에 매수·매도선이 없어요.",
      };

    case "D": {
      const lo = num(form.lower_price);
      const up = num(form.upper_price);
      const n = Math.max(2, Math.round(num(form.grid_count) || 0));
      if (!(up > lo) || !(lo > 0)) return { note: "가격 범위(하단·상단)를 올바르게 넣으면 격자선이 표시돼요." };
      const mid = (up + lo) / 2;
      const lines = [];
      for (let i = 0; i <= n; i++) {
        const price =
          form.grid_mode === "geometric"
            ? lo * Math.pow(up / lo, i / n)
            : lo + ((up - lo) * i) / n;
        // 칸이 많으면 끝(상단·하단)만 라벨을 달아 어지럽지 않게.
        const label = i === 0 ? `하단 ${fmtPrice(lo)}` : i === n ? `상단 ${fmtPrice(up)}` : "";
        push(lines, { price, color: price < mid ? UP : DOWN, label, dash: "2 4" });
      }
      return {
        legend: [
          { label: "아래쪽 칸(매수)", color: UP },
          { label: "위쪽 칸(매도)", color: DOWN },
        ],
        priceLines: lines,
        note: "가격 범위를 여러 칸으로 나눠, 한 칸 내리면 사고 한 칸 오르면 파는 걸 반복해요. 아래 칸은 매수, 위 칸은 매도 자리예요.",
      };
    }

    case "E": {
      const trail = num(form.trail_percent);
      const lines = [];
      let mx = -Infinity;
      // 고점을 따라 내려오는 트레일링 스탑선(활성화 이후 개념을 단순화해 표시).
      const trailVals = closes.map((c) => {
        mx = Math.max(mx, c);
        return trail > 0 ? mx * (1 - trail / 100) : null;
      });
      if (form.entry_mode === "dip") {
        const dip = num(form.entry_dip);
        if (dip > 0) push(lines, priceLine(last * (1 - dip / 100), UP, `진입 목표 -${dip}%`, "5 3"));
      }
      return {
        legend: [
          { label: `트레일링 스탑(고점 -${trail || "?"}%)`, color: DOWN },
          ...(form.entry_mode === "dip" ? [{ label: "진입 목표", color: UP }] : []),
        ],
        priceLines: lines,
        series: [
          { id: "trail", color: DOWN, label: "트레일링 스탑", values: trailVals, width: 1.5, dash: "4 3" },
        ],
        note: "이익이 나기 시작하면 고점을 따라 스탑선이 올라가요. 가격이 이 선까지 내려오면 이익을 지키며 정리해요.",
      };
    }

    case "F": {
      const period = Math.round(num(form.rsi_period) || 14);
      const ent = num(form.entry_threshold);
      const ext = num(form.exit_threshold);
      const r = rsi(closes, period);
      // RSI 가 진입선 이하로 내려오면 매수, 정리선 이상으로 오르면 매도 신호.
      const markers = [];
      for (let i = 1; i < r.length; i++) {
        if (r[i] == null || r[i - 1] == null) continue;
        if (r[i - 1] > ent && r[i] <= ent) markers.push({ index: i, kind: "buy", label: "RSI 매수" });
        else if (r[i - 1] < ext && r[i] >= ext) markers.push({ index: i, kind: "sell", label: "RSI 매도" });
      }
      return {
        legend: [
          { label: `RSI(${period})`, color: MID },
          { label: "매수 신호", color: UP, kind: "buy" },
          { label: "매도 신호", color: DOWN, kind: "sell" },
        ],
        markers,
        rsi: { values: r, entry: ent, exit: ext },
        note: `아래 보조창이 RSI예요. ${ent} 이하로 내려오면(과매도) 매수, ${ext} 이상이면(과매수) 매도 신호로 봐요.`,
      };
    }

    case "G": {
      const period = Math.round(num(form.bb_period) || 20);
      const k = num(form.bb_std) || 2;
      const mid = sma(closes, period);
      const std = rollingStd(closes, period, mid);
      const upper = mid.map((m, i) => (m == null || std[i] == null ? null : m + k * std[i]));
      const lower = mid.map((m, i) => (m == null || std[i] == null ? null : m - k * std[i]));
      const reversion = form.strategy !== "breakout";
      const markers = [];
      for (let i = 1; i < closes.length; i++) {
        if (upper[i] == null || lower[i] == null) continue;
        if (reversion) {
          // 하단 밴드를 건드리면 매수, 상단(또는 중앙선)에서 매도.
          if (candles[i].l <= lower[i] && candles[i - 1].l > (lower[i - 1] ?? Infinity))
            markers.push({ index: i, kind: "buy", label: "하단 밴드 매수" });
          const exitAt = form.exit_target === "opposite" ? upper[i] : mid[i];
          if (exitAt != null && candles[i].h >= exitAt && candles[i - 1].h < exitAt)
            markers.push({ index: i, kind: "sell", label: form.exit_target === "opposite" ? "상단 밴드 매도" : "중앙선 매도" });
        } else {
          // 돌파: 상단 위로 종가가 뚫으면 매수, 하단 아래로 뚫으면 매도.
          if (closes[i - 1] <= upper[i - 1] && closes[i] > upper[i])
            markers.push({ index: i, kind: "buy", label: "상단 돌파 매수" });
          if (closes[i - 1] >= lower[i - 1] && closes[i] < lower[i])
            markers.push({ index: i, kind: "sell", label: "하단 이탈 매도" });
        }
      }
      return {
        legend: [
          { label: "상단 밴드", color: BAND },
          { label: "중앙선(이동평균)", color: MID },
          { label: "하단 밴드", color: BAND },
          { label: "매수", color: UP, kind: "buy" },
          { label: "매도", color: DOWN, kind: "sell" },
        ],
        series: [
          { id: "bb_upper", color: BAND, label: "상단 밴드", values: upper, width: 1 },
          { id: "bb_mid", color: MID, label: "중앙선", values: mid, width: 1.5, dash: "4 3" },
          { id: "bb_lower", color: BAND, label: "하단 밴드", values: lower, width: 1 },
        ],
        bands: [{ upper, lower, fill: FILL }],
        markers,
        note: reversion
          ? "가운데는 이동평균, 위아래는 변동성 밴드예요. 가격이 하단 밴드에 닿으면 매수, 중앙선·상단에서 매도해요."
          : "가운데는 이동평균, 위아래는 변동성 밴드예요. 가격이 상단 밴드를 위로 뚫으면 매수(추세), 하단을 아래로 뚫으면 매도해요.",
      };
    }

    case "H": {
      const dev = num(form.price_deviation) / 100;
      const stepScale = num(form.safety_order_step_scale) || 1;
      const maxSO = Math.max(0, Math.round(num(form.max_safety_orders) || 0));
      const tp = num(form.take_profit);
      const lines = [];
      push(lines, priceLine(last, UP, `기본 매수 · ${fmtPrice(last)}`, undefined));
      let cumDev = 0;
      let step = dev;
      for (let i = 1; i <= maxSO; i++) {
        cumDev += step;
        step *= stepScale;
        push(lines, priceLine(last * (1 - cumDev), UP, `${i}차 추가매수`, "2 4"));
      }
      if (tp > 0) push(lines, priceLine(last * (1 + tp / 100), DOWN, `익절(평단 +${tp}%)`, "5 3"));
      return {
        legend: [
          { label: "매수/추가매수", color: UP },
          { label: "익절선(평단 기준)", color: DOWN },
        ],
        priceLines: lines,
        note: "가격이 내려갈 때마다 정해진 간격으로 더 사서 평단을 낮춰요. 아래 선들이 추가매수 자리, 위 선이 평단 대비 익절 목표예요.",
      };
    }

    case "I": {
      const k = num(form.k);
      // 각 봉의 돌파 매수선 = 그 봉 시가 + k×(직전 봉 고저 변동폭).
      const target = candles.map((c, i) =>
        i === 0 || !(k >= 0) ? null : c.o + k * (candles[i - 1].h - candles[i - 1].l)
      );
      const markers = [];
      for (let i = 1; i < candles.length; i++) {
        if (target[i] == null) continue;
        if (candles[i].h >= target[i] && candles[i - 1].h < (target[i - 1] ?? Infinity))
          markers.push({ index: i, kind: "buy", label: "돌파 매수" });
      }
      return {
        legend: [
          { label: "돌파 매수선", color: UP, kind: "buy" },
        ],
        series: [{ id: "breakout", color: UP, label: "돌파 매수선", values: target, width: 1.5, dash: "4 3" }],
        markers,
        note: "전 봉 변동폭의 k배만큼 오늘 시가 위로 가격이 뚫으면 매수해요. 주황 계단선이 그 돌파 기준이에요.",
      };
    }

    case "J": {
      const fp = Math.round(num(form.fast_period) || 20);
      const sp = Math.round(num(form.slow_period) || 60);
      const useEma = form.ma_type === "EMA";
      const fast = useEma ? ema(closes, fp) : sma(closes, fp);
      const slow = useEma ? ema(closes, sp) : sma(closes, sp);
      const markers = crossMarkers(fast, slow, "골든크로스 매수", "데드크로스 매도");
      return {
        legend: [
          { label: `단기 ${useEma ? "EMA" : "SMA"}(${fp})`, color: FAST },
          { label: `장기 ${useEma ? "EMA" : "SMA"}(${sp})`, color: SLOW },
          { label: "매수", color: UP, kind: "buy" },
          { label: "매도", color: DOWN, kind: "sell" },
        ],
        series: [
          { id: "ma_fast", color: FAST, label: "단기 이동평균", values: fast, width: 1.5 },
          { id: "ma_slow", color: SLOW, label: "장기 이동평균", values: slow, width: 1.5 },
        ],
        markers,
        note: "단기선이 장기선을 위로 뚫으면(골든크로스) 매수, 아래로 뚫으면(데드크로스) 매도해요. 두 선이 만나는 곳이 신호예요.",
      };
    }

    case "K": {
      const drop = num(form.drop_trigger_pct);
      const ltp = num(form.long_take_profit_pct);
      const stp = num(form.short_take_profit_pct);
      const ssl = num(form.short_stop_loss_pct);
      const lines = [];
      push(lines, priceLine(last, NEUTRAL, `현재가 ${fmtPrice(last)}`, "2 3"));
      const trigger = last * (1 - drop / 100);
      push(lines, priceLine(trigger, DOWN, `방어 시작 -${drop}%`, "5 3"));
      if (ltp > 0) push(lines, priceLine(last * (1 + ltp / 100), UP, `롱 익절 +${ltp}%`, "5 3"));
      if (stp > 0) push(lines, priceLine(trigger * (1 - stp / 100), UP, `숏 익절`, "2 4"));
      if (ssl > 0) push(lines, priceLine(trigger * (1 + ssl / 100), DOWN, `숏 손절`, "2 4"));
      return {
        legend: [
          { label: "방어 시작선", color: DOWN },
          { label: "익절 방향", color: UP },
        ],
        priceLines: lines,
        note: "가격이 진입가보다 방어 시작선까지 내려오면 일부를 팔고, 설정에 따라 숏으로 전환해 추가 하락에 대응해요.",
      };
    }

    default:
      return null;
  }
}
