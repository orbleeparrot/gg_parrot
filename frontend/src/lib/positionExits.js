// 내 에이전트 포지션 스트립의 순수 계산 — 실행 중인 매크로의 청산 규칙을 읽어
// '청산 기준' 글과 게이지 모양을 정하고, 평가손익 금액·실행 시간을 만든다.
// 손절은 모든 유형 공통(risk.stop_loss_pct), 익절은 유형별 값이며 없는 쪽은 "없다"고 말한다
// (차트의 익절선·손절선을 그리는 indicators.js 와 같은 값을 읽는다). node --test 로 검증한다.
import { quoteOf } from "./format.js";

function positive(value) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

export function fmtPct(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return "0";
  return String(Math.round(v * 100) / 100);
}

export function fmtSignedPct(pct) {
  const v = Number(pct) || 0;
  return `${v > 0 ? "+" : v < 0 ? "-" : ""}${Math.abs(v).toFixed(2)}%`;
}

export function fmtSignedMoney(value, symbol) {
  const v = Number(value) || 0;
  const body = Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${v > 0 ? "+" : v < 0 ? "-" : ""}${body} ${quoteOf(symbol)}`;
}

// 청산 규칙: { sl, tp, trailing, kind, summary }
//  sl/tp: { pct, label } | null · trailing: { activation, trail, label } | null
//  kind: "two" | "sl-only" | "tp-only" | "none" · summary: '청산 기준' 칸에 쓰는 한 줄
export function exitRules(macro, side = "long") {
  const params = macro?.params || {};
  const risk = macro?.risk || {};
  const short = (macro?.position_side || side) === "short";
  let sl = positive(risk.stop_loss_pct);
  let tp = 0;
  let trailing = null;
  switch (macro?.rule_type) {
    case "A": tp = positive(params.take_profit_pct); break;
    case "H": tp = positive(params.take_profit); break;
    case "K":
      if (short) {
        tp = positive(params.short_take_profit_pct);
        sl = positive(params.short_stop_loss_pct) || sl;
      } else {
        tp = positive(params.long_take_profit_pct);
      }
      break;
    case "E": {
      const activation = positive(params.activation_profit);
      const trail = positive(params.trail_percent);
      if (activation || trail) trailing = { activation, trail };
      break;
    }
    default: break;
  }
  // F(RSI)·J(이동평균) 등은 선택형 익절 `take_profit` 을 둘 수 있다 — 값이 있으면 그것이 익절선이다.
  if (!tp && !trailing) tp = positive(params.take_profit);
  const slRule = sl ? { pct: sl, label: `손절 -${fmtPct(sl)}%` } : null;
  const tpRule = tp ? { pct: tp, label: `익절 +${fmtPct(tp)}%` } : null;
  const trailingRule = trailing
    ? { ...trailing, label: trailing.activation ? `발동 +${fmtPct(trailing.activation)}% · 고점 -${fmtPct(trailing.trail)}%` : `고점 -${fmtPct(trailing.trail)}%` }
    : null;
  const upside = tpRule || trailingRule;
  const kind = slRule && upside ? "two" : slRule ? "sl-only" : upside ? "tp-only" : "none";
  const parts = [];
  if (tpRule) parts.push(tpRule.label);
  if (trailingRule) parts.push(`트레일링 ${trailingRule.label}`);
  if (slRule) parts.push(slRule.label);
  if (!tpRule && !trailingRule) parts.push(slRule ? "익절은 신호" : "");
  const summary = kind === "none" ? "전략 신호" : parts.filter(Boolean).join(" · ");
  return { sl: slRule, tp: tpRule, trailing: trailingRule, kind, summary };
}

// 게이지: 손절(왼쪽 끝)과 익절·발동(오른쪽 끝) 사이에서 현재 평가손익이 어디쯤인지.
// 한쪽만 있으면 그쪽 값을 반대편에도 같은 폭으로 써서 마커가 움직일 자리를 두고, 끝 라벨은 '없음'으로 쓴다.
export function gaugeModel(unrealizedPct, rules) {
  const pct = Number(unrealizedPct) || 0;
  const leftPct = rules?.sl?.pct || 0;
  const rightPct = rules?.tp?.pct || rules?.trailing?.activation || 0;
  if (!leftPct && !rightPct) return { show: false };
  const left = leftPct || rightPct;
  const right = rightPct || leftPct;
  const position = pct < 0 ? 0.5 - 0.5 * Math.min(-pct / left, 1) : 0.5 + 0.5 * Math.min(pct / right, 1);
  const trailingArmed = Boolean(rules.trailing && rules.trailing.activation && pct >= rules.trailing.activation);
  return {
    show: true,
    position,
    oneSided: !leftPct || !rightPct,
    left: leftPct ? { label: rules.sl.label, tone: "is-down" } : { label: "손절 규칙 없음", tone: "is-muted" },
    right: rules.tp
      ? { label: rules.tp.label, tone: "is-up" }
      : rules.trailing
        ? { label: trailingArmed ? `고점 -${fmtPct(rules.trailing.trail)}% 추적 중` : rules.trailing.label, tone: "is-up" }
        : { label: "익절 규칙 없음 · 신호 청산", tone: "is-muted" },
  };
}

// 평가손익 금액(호가 통화) — 가격 차이 × 수량. 숏이면 부호를 뒤집는다. 값이 없으면 null.
export function unrealizedMoney(session) {
  const entry = Number(session?.entry_price);
  const last = Number(session?.last_price);
  const qty = Number(session?.position_qty);
  if (![entry, last, qty].every(Number.isFinite) || entry <= 0 || last <= 0 || qty <= 0) return null;
  const diff = (last - entry) * qty;
  return session?.position_side === "short" ? -diff : diff;
}

// 실행 시간: "3일 2시간" · "2시간 14분" · "48분" · "1분 미만". 시각이 없으면 "".
export function runningFor(startedAt, now = Date.now()) {
  const started = Date.parse(startedAt || "");
  if (!Number.isFinite(started)) return "";
  const minutes = Math.max(0, Math.floor((now - started) / 60_000));
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  const mins = minutes % 60;
  if (days > 0) return hours ? `${days}일 ${hours}시간` : `${days}일`;
  if (hours > 0) return mins ? `${hours}시간 ${mins}분` : `${hours}시간`;
  return mins > 0 ? `${mins}분` : "1분 미만";
}

export function toneOf(value) {
  const v = Number(value) || 0;
  return v > 0 ? "is-up" : v < 0 ? "is-down" : "";
}

// 포지션 블록의 큰 숫자 — 서버가 투입금(invested_usdt)과 총수익률(return_pct)을 주면 그걸 앞세우고,
// 진입가 대비 평가손익은 보조 문구로. 투입금이 없는 옛 세션은 예전처럼 진입가 대비 %만.
export function headlineReturn(session) {
  const invested = Number(session?.invested_usdt) || 0;
  const total = session?.return_pct;
  if (invested > 0 && total !== null && total !== undefined && Number.isFinite(Number(total))) {
    const investedText = `투입 ${invested.toLocaleString("en-US", { maximumFractionDigits: 2 })} USDT`;
    const note = session?.in_position
      ? `진입가 대비 ${fmtSignedPct(session.unrealized_pct)} · ${investedText}`
      : `실현 ${fmtSignedMoney(session?.realized_pnl, session?.symbol)} · ${investedText}`;
    return { pct: Number(total), label: "투입금 대비 총수익률", note };
  }
  return { pct: Number(session?.unrealized_pct) || 0, label: "평가손익", note: "" };
}
