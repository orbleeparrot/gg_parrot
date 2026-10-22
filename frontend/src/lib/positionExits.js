// 내 에이전트 포지션 스트립의 순수 계산 — 실행 중인 매크로의 청산 규칙을 읽어
// '청산 기준' 글과 게이지 모양을 정하고, 평가손익 금액·실행 시간을 만든다.
// 손절은 모든 유형 공통(risk.stop_loss_pct), 익절은 유형별 값이며 없는 쪽은 "없다"고 말한다
// (차트의 익절선·손절선을 그리는 indicators.js 와 같은 값을 읽는다). node --test 로 검증한다.
import { formatQuoteAmount } from "./exchanges.js";

function positive(value) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

export function fmtPct(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return "0";
  return String(Math.round(v * 100) / 100);
}

// 화면 자릿수(원 · USDT 소수 둘째 · % 소수 둘째)로 0 이 되는 값은 0 — '+0원' · '+0.00%'처럼 이익인 척 부호와 초록이
// 붙지 않게(2026-10-21: 마틴게일이 수수료 빼면 0원에 판 것을 초록으로 보였다).
export function shownMoney(value, symbol) {
  const v = Number(value) || 0;
  const step = String(symbol || "").toUpperCase().startsWith("KRW-") ? 1 : 0.01;
  return Math.abs(v) < step / 2 ? 0 : v;
}

export function shownPct(pct) {
  const v = Number(pct) || 0;
  return Math.abs(v) < 0.005 ? 0 : v;
}

export function fmtSignedPct(pct) {
  const v = shownPct(pct);
  return `${v > 0 ? "+" : v < 0 ? "-" : ""}${Math.abs(v).toFixed(2)}%`;
}

export function fmtSignedMoney(value, symbol) {
  const v = shownMoney(value, symbol);
  return `${v > 0 ? "+" : v < 0 ? "-" : ""}${formatQuoteAmount(Math.abs(v), symbol, { fixed: true })}`;
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

// 평단 대비 가격으로 센 평가손익 금액(호가 통화, 수수료 전) — 가격 차이 × 수량. 숏이면 부호를 뒤집는다. 값이 없으면 null.
export function unrealizedMoney(session) {
  const entry = Number(session?.entry_price);
  const last = Number(session?.last_price);
  const qty = Number(session?.position_qty);
  if (![entry, last, qty].every(Number.isFinite) || entry <= 0 || last <= 0 || qty <= 0) return null;
  const diff = (last - entry) * qty;
  return session?.position_side === "short" ? -diff : diff;
}

function finiteOrNull(value) {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

// 수수료 반영 값이 없어 평단 대비 가격으로 보일 때 숫자 옆에 붙이는 말.
export const GROSS_TAG = "수수료 전";

// 실행기 화면과 같은 평가손익 — 실행기(0.20.13+)가 보낸 수수료 반영 값(사고팔 때 수수료를 뺀 '지금 정리하면')을 쓰고,
// 없으면(옛 실행기 · 진입 수수료를 모름) 평단 대비 가격(수수료 전)으로 되돌린다 — feesIncluded 가 false 면 그렇다고 밝힌다.
// 2026-10-18: 실행기 +1.23% · 웹 +1.33% (업비트 수수료 0.05% × 2). 손절 · 익절 게이지는 늘 평단 대비 가격(unrealized_pct).
// final: 종료 보고 때 남긴 청산 직전 포지션의 값.
export function positionPnl(session, { final = false } = {}) {
  const netPct = finiteOrNull(final ? session?.final_unrealized_net_pct : session?.unrealized_net_pct);
  if (netPct !== null) {
    return { pct: netPct, money: finiteOrNull(final ? session?.final_unrealized_net : session?.unrealized_net), feesIncluded: true };
  }
  const pct = Number(final ? session?.final_unrealized_pct : session?.unrealized_pct) || 0;
  return { pct, money: final ? null : unrealizedMoney(session), feesIncluded: false };
}

// 누적 실현손익 — 실행기가 보낸 수수료 반영 값, 모르면 수수료 전 값.
export function realizedPnl(session) {
  const net = finiteOrNull(session?.realized_net);
  if (net !== null) return { money: net, feesIncluded: true };
  return { money: Number(session?.realized_pnl) || 0, feesIncluded: false };
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

// 손익 색 — 금액이면 symbol 을 넘겨 그 통화의 자릿수로, 아니면 % 자릿수로 0 을 가른다.
export function toneOf(value, symbol) {
  const v = symbol === undefined ? shownPct(value) : shownMoney(value, symbol);
  return v > 0 ? "is-up" : v < 0 ? "is-down" : "";
}

// 포지션 블록의 큰 숫자.
// - 보유 중: 실행기 화면과 같은 '평가손익'(수수료 반영, positionPnl). 평단 대비 가격 · 총수익률(앞서 실현한 손익이 있을 때) ·
//   투입금은 보조 문구로 — 2026-10-18 까지는 보유 중에도 총수익률(수수료 전)을 앞세워 실행기와 숫자가 달랐다.
// - 무포지션: 서버가 투입금(invested_usdt)과 총수익률(return_pct)을 주면 그걸 앞세운다. 투입금이 없는 옛 세션은 note 없이.
export function headlineReturn(session) {
  const invested = Number(session?.invested_usdt) || 0;
  const total = finiteOrNull(session?.return_pct);
  const investedText = invested > 0 ? `투입 ${formatQuoteAmount(invested, session?.symbol)}` : "";
  // 서버가 수수료 반영 여부를 알려 주는데(fees_included) 아니라고 하면 총수익률도 수수료 전이다.
  const totalTag = session?.fees_included === false ? ` · ${GROSS_TAG}` : "";
  const realized = realizedPnl(session);
  if (session?.in_position) {
    const pnl = positionPnl(session);
    const note = [
      `평단 대비 ${fmtSignedPct(session.unrealized_pct)}`,
      invested > 0 && total !== null && realized.money !== 0 ? `총수익률 ${fmtSignedPct(total)}${totalTag}` : "",
      investedText,
    ].filter(Boolean).join(" · ");
    return { pct: pnl.pct, money: pnl.money, label: pnl.feesIncluded ? "평가손익" : `평가손익 · ${GROSS_TAG}`, note };
  }
  if (invested > 0 && total !== null) {
    const note = `실현 ${fmtSignedMoney(realized.money, session?.symbol)} · ${investedText}`;
    return { pct: total, money: null, label: `투입금 대비 총수익률${totalTag}`, note };
  }
  return { pct: Number(session?.unrealized_pct) || 0, money: null, label: "평가손익", note: "" };
}
