// 껄무새에게 물어볼까? 결과·후보 카드가 보여 줄 값을 만드는 순수 함수들 (2026-09-30 재설계).
// 숫자는 전부 서버가 계산한 값이고, 여기서는 모양만 바꾼다 — 없는 값을 만들어 내지 않는다.
import { FEW_RESULTS_TEXT, NO_RESULTS_TEXT, TOP_RESULTS_TEXT } from "./askCopy.js";

const num = (v) => (v == null || v === "" || Number.isNaN(Number(v)) ? null : Number(v));

export function signedPct(v, digits = 2) {
  const n = num(v);
  if (n == null) return "—";
  return `${n >= 0 ? "+" : ""}${n.toFixed(digits)}%`;
}

// 수익률 − 그냥 들고 있기(%p). 들고 있기 값이 없으면 null.
export function holdDiff(item) {
  const ret = num(item?.metrics?.final_return_pct);
  const hold = num(item?.hold_return_pct);
  return ret == null || hold == null ? null : ret - hold;
}

export function signedPoints(v) {
  const n = num(v);
  if (n == null) return "—";
  return `${n >= 0 ? "+" : "-"}${Math.abs(n).toFixed(1)}%p`;
}

// 결과 여럿의 '그냥 들고 있기' — 같은 종목·같은 기간이라 첫 값을 쓴다.
export function holdOf(results = []) {
  const found = results.find((r) => num(r?.hold_return_pct) != null);
  return found ? Number(found.hold_return_pct) : null;
}

export function resultsHeadline(count) {
  if (!count) return NO_RESULTS_TEXT;
  return count < 3 ? FEW_RESULTS_TEXT : TOP_RESULTS_TEXT;
}

// 조건 한 줄 — 매매 방식의 핵심 설정(최대 2구) + 봉 간격.
export function conditionLines(macro) {
  const p = macro?.params || {};
  const r = macro?.risk || {};
  const lines = [];
  switch (macro?.rule_type) {
    case "A": lines.push(`익절 +${p.take_profit_pct}%`, r.stop_loss_pct != null ? `손절 -${r.stop_loss_pct}%` : "손절 없음"); break;
    case "C": lines.push(`${p.interval_days}일마다 ${Number(p.amount_per_buy).toLocaleString()}원씩 매수`); break;
    case "E": lines.push(`+${p.activation_profit}% 뒤 트레일링 시작`, `고점 대비 -${p.trail_percent}% 에 청산`); break;
    case "F": lines.push(`RSI(${p.rsi_period}) ${p.entry_threshold} 아래 매수`, `${p.exit_threshold} 위 매도`); break;
    case "G": lines.push(`볼린저(${p.bb_period}, ${p.bb_std}σ) 하단 매수`, p.exit_target === "mid" ? "중심선 매도" : "상단 매도"); break;
    case "H": lines.push(`기본 ${Number(p.base_order_size).toLocaleString()} + 세이프티 ${p.max_safety_orders}회`, `평단 +${p.take_profit}% 익절`); break;
    case "I": lines.push(`변동성 돌파 k=${p.k}`, p.exit_mode === "next_open" ? "다음 봉 시가 청산" : `청산: ${p.exit_mode}`); break;
    case "J": lines.push(`${p.ma_type} ${p.fast_period}/${p.slow_period} 골든크로스 매수`, "데드크로스 매도"); break;
    default: break;
  }
  if (macro?.candle_interval) lines.push(intervalLabel(macro));
  return lines;
}

export function intervalLabel(macro) {
  return macro?.candle_interval ? `${macro.candle_interval} 봉` : "";
}

export function marketLabel(macro) {
  if (macro?.market === "futures" || Number(macro?.leverage) > 1) {
    return `선물 · ${macro?.margin_mode === "cross" ? "교차" : "격리"} ${macro?.leverage || 1}배`;
  }
  return "현물 · 1배";
}

// '방식 이름 (창시자)' → 이름과 괄호 속 부제를 나눈다. 예: "I · 변동성 돌파 (래리 윌리엄스)".
export function splitRuleLabel(label = "") {
  const m = String(label).match(/^(.*?)\s*\(([^()]+)\)\s*$/);
  return m ? { name: m[1], by: m[2] } : { name: String(label), by: "" };
}

// 문장 속 숫자를 굵게 조판하려고 글자/숫자 조각으로 나눈다.
const NUM_RE = /([+-]?\d[\d,]*(?:\.\d+)?(?:%p|%|x|배|σ)?)/g;
export function numberParts(text = "") {
  const out = [];
  let last = 0;
  String(text).replace(NUM_RE, (match, _g, offset) => {
    if (offset > last) out.push({ t: "text", v: text.slice(last, offset) });
    out.push({ t: "num", v: match });
    last = offset + match.length;
    return match;
  });
  if (last < text.length) out.push({ t: "text", v: text.slice(last) });
  return out;
}

// 해설 제목 — 앞의 🦜 는 얼굴 그림이 대신하므로 뗀다(문구는 그대로).
export function whyHeadline(text = "") {
  return String(text).replace(/^\s*🦜\s*/u, "").trim();
}

// 해설 한 줄 → { fig, label, text }. 서버(engine/explain.py _points)의 문장 틀에서 핵심 숫자와 이름표를 뽑는다.
const RULES = [
  { test: /홀딩/, label: "홀딩 대비", fig: (s) => { const m = s.match(/([\d.]+)%p/); return m ? `${/뒤졌/.test(s) ? "-" : "+"}${m[1]}%p` : ""; } },
  { test: /최종 수익률/, label: "수익률", fig: (s) => (s.match(/([+-]?[\d.]+%)/) || [])[1] || "" },
  { test: /MDD|빠졌/, label: "최대 낙폭", fig: (s) => { const m = s.match(/-([\d.]+)%/); return m ? `-${m[1]}%` : ""; } },
  { test: /승률/, label: "승률", fig: (s) => { const m = s.match(/승률\s*([\d.]+)%/); return m ? `${m[1]}%` : ""; } },
  { test: /연속/, label: "연속 손절", fig: (s) => { const m = s.match(/(\d+)번 연속/); return m ? `${m[1]}번` : ""; } },
  { test: /청산돼|청산/, label: "청산", fig: (s) => { const m = s.match(/(\d+)번 청산/); return m ? `${m[1]}번` : ""; } },
  { test: /샤프/, label: "샤프지수", fig: (s) => (s.match(/샤프지수\s*(-?[\d.]+)/) || [])[1] || "" },
];
export function whyPoints(points = [], limit = 3) {
  return (Array.isArray(points) ? points : []).slice(0, limit).map((raw) => {
    const text = String(raw || "").trim();
    const rule = RULES.find((r) => r.test.test(text));
    if (rule) return { fig: rule.fig(text), label: rule.label, text };
    const first = text.match(/[+-]?\d[\d,]*(?:\.\d+)?(?:%p|%|번|회)?/);
    return { fig: first ? first[0] : "", label: "", text };
  });
}

// 카드의 작은 자산곡선 — 서버가 준 점(최대 40)을 viewBox 안 경로로. 파선은 본전(시작 자금).
export function sparkPaths(curve, initial, { w = 100, h = 44, pad = 3 } = {}) {
  const pts = (Array.isArray(curve) ? curve : []).map(Number).filter((v) => Number.isFinite(v));
  if (pts.length < 2) return null;
  const base = Number.isFinite(Number(initial)) ? Number(initial) : pts[0];
  let lo = Math.min(base, ...pts);
  let hi = Math.max(base, ...pts);
  if (!(hi > lo)) hi = lo + 1;
  const x = (i) => ((i / (pts.length - 1)) * w).toFixed(1);
  const y = (v) => (pad + (1 - (v - lo) / (hi - lo)) * (h - pad * 2)).toFixed(1);
  const line = pts.map((v, i) => `${i ? "L" : "M"}${x(i)} ${y(v)}`).join(" ");
  return { line, area: `${line} L${w} ${h} L0 ${h} Z`, base: `M0 ${y(base)} H${w}`, up: pts[pts.length - 1] >= base };
}

// 카드의 코인 색 번짐 — 로고 색에 가까운 값(주요 코인만). 없으면 번짐 없이 둔다.
const COIN_TINTS = {
  BTC: "#f7931a", ETH: "#627eea", SOL: "#9945ff", XRP: "#23292f", BNB: "#f3ba2f", DOGE: "#c2a633", ADA: "#0033ad",
  TRX: "#ef0027", AVAX: "#e84142", LINK: "#2a5ada", DOT: "#e6007a", TON: "#0098ea", LTC: "#345d9d", BCH: "#8dc351",
  SUI: "#4da2ff", APT: "#00c2a8", ARB: "#28a0f0", OP: "#ff0420", NEAR: "#00c08b", QNT: "#3b4150", PEPE: "#3d9e3f",
  SHIB: "#e42d04", UNI: "#ff007a", ATOM: "#2e3148", FIL: "#0090ff", INJ: "#00a3ff", AAVE: "#9391f7", ETC: "#328332",
};
export function coinTint(base = "") {
  return COIN_TINTS[String(base).toUpperCase().replace(/^1000/, "")] || "";
}
