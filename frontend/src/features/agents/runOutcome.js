// 실행 종료 결과와 종료 확인 문구 — 화면 컴포넌트가 아니라 순수 함수로 두어 테스트한다.
import { exitRules } from "../../lib/positionExits.js";

const USDT = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const PRICE = new Intl.NumberFormat("en-US", { maximumFractionDigits: 6 });
const QTY = new Intl.NumberFormat("en-US", { maximumFractionDigits: 6 });

function signOf(value) {
  const n = Number(value) || 0;
  return n > 0 ? "+" : n < 0 ? "−" : "";
}

const QUOTE_ASSETS = ["USDT", "BUSD", "USDC", "FDUSD", "TUSD", "USD"];
function baseAsset(symbol) {
  const value = String(symbol || "").toUpperCase();
  const quote = QUOTE_ASSETS.find((q) => value.endsWith(q));
  return quote ? value.slice(0, -quote.length) : value;
}

export function toneOf(value) {
  const n = Number(value) || 0;
  return n > 0 ? "up" : n < 0 ? "down" : "flat";
}

export function formatSignedUsdt(value) {
  const n = Number(value) || 0;
  return `${signOf(n)}${USDT.format(Math.abs(n))} USDT`;
}

export function formatSignedPct(value) {
  const n = Number(value) || 0;
  return `${signOf(n)}${Math.abs(n).toFixed(2)}%`;
}

// API 의 KST 라벨("MM/DD HH:MM:SS") 두 개 사이의 경과 시간. 해가 바뀌면 뒤쪽을 한 해 뒤로 본다.
export function elapsedLabel(startedKst, stoppedKst) {
  const parse = (label) => {
    const m = /^(\d{2})\/(\d{2}) (\d{2}):(\d{2}):(\d{2})$/.exec(String(label || "").trim());
    if (!m) return null;
    return Date.UTC(2000, Number(m[1]) - 1, Number(m[2]), Number(m[3]), Number(m[4]), Number(m[5]));
  };
  const start = parse(startedKst);
  let end = parse(stoppedKst);
  if (start == null || end == null) return "";
  if (end < start) end += 366 * 24 * 3600 * 1000;
  const total = Math.max(0, Math.round((end - start) / 1000));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (days) return `${days}일 ${hours}시간`;
  if (hours) return `${hours}시간 ${minutes}분`;
  return `${Math.max(1, minutes)}분`;
}

export function stopModeLabel(mode) {
  if (mode === "close_and_stop") return "청산 후 종료";
  if (mode === "stop_only") return "매크로만 종료";
  return "실행기에서 종료";
}

function marketLabel(session) {
  if (session.market === "futures") {
    return `선물 ${Number(session.leverage) || 1}배 ${session.position_side === "short" ? "숏" : "롱"}`;
  }
  return "현물";
}

export function describeRunOutcome(session) {
  const s = session || {};
  // 종료를 요청했지만 실행기가 아직 확정 보고를 하지 않은 구간. 예전에는 이 동안 화면이
  // 그대로여서 채팅에 알림만 먼저 쌓였다 — 결과 화면을 먼저 띄우고 여기서 기다린다.
  const stopping = !!s.stopping && s.status === "running";
  const uncertain = !!s.position_uncertain;
  const failed = s.status === "error";
  const closedOut = s.note === "청산 완료 후 종료";
  const flatExit = s.note === "포지션 없이 종료";
  const keptPosition = !failed && !uncertain && !!s.in_position;

  let eyebrow = "실행 종료";
  let title;
  let detail = "";
  let tone = "neutral";
  let avatar = "calm";
  if (stopping) {
    eyebrow = "종료 처리 중";
    title = s.stop_mode === "close_and_stop" ? "청산하고 종료하는 중이에요" : "매크로를 멈추는 중이에요";
    detail = s.stop_mode === "close_and_stop"
      ? "실행기가 다음 확인에서 포지션을 정리해요. 완료 보고가 오면 결과가 채워집니다."
      : "실행기가 다음 확인에서 매크로를 멈춰요. 포지션은 그대로 남습니다.";
    tone = "pending";
    avatar = "focused";
  } else if (uncertain) {
    eyebrow = "포지션 확인 필요";
    title = "청산 완료를 확인하지 못했어요";
    detail = s.note || "거래소에서 주문과 남은 포지션을 직접 확인하세요.";
    tone = "critical";
    avatar = "critical";
  } else if (failed) {
    eyebrow = "오류 종료";
    title = "오류로 실행이 멈췄어요";
    detail = s.note || "실행기가 오류를 보고했어요. 거래소 포지션을 확인하세요.";
    tone = "critical";
    avatar = "critical";
  } else if (closedOut) {
    title = "청산 완료 후 종료했어요";
    detail = "포지션을 정리하고 매크로를 멈췄어요.";
    tone = "good";
  } else if (keptPosition) {
    title = "매크로만 종료했어요";
    detail = "포지션은 그대로 남아 있어요. 정리는 거래소에서 직접 해야 해요.";
    tone = "warning";
    avatar = "warning";
  } else if (flatExit) {
    title = "포지션 없이 종료했어요";
    detail = "보유 포지션 없이 매크로를 멈췄어요.";
    tone = "good";
  } else {
    title = "실행을 종료했어요";
    detail = s.note || "";
  }

  // 표정은 결과의 부호를 따른다: 포지션이 남았으면 그 평가손익, 청산했으면 청산 직전 평가손익,
  // 그것도 없으면(옛 실행기) 누적 실현손익. 이익이면 환호, 손실이면 놀람, 0이면 평온.
  const finalQty = Number(s.final_position_qty) || 0;
  const resultPct = keptPosition ? Number(s.unrealized_pct) || 0 : finalQty > 0 ? Number(s.final_unrealized_pct) || 0 : null;
  const resultSign = resultPct !== null ? Math.sign(resultPct) : Math.sign(Number(s.realized_pnl) || 0);
  if (!stopping && !uncertain && !failed) {
    avatar = resultSign > 0 ? "signal" : resultSign < 0 ? "warning" : keptPosition ? "warning" : "calm";
  }

  // 아직 끝나지 않았으면 마지막 heartbeat 를 끝점으로 삼는다.
  const endedAt = s.stopped_kst || (stopping ? s.heartbeat_kst : "");
  const elapsed = elapsedLabel(s.started_kst, endedAt);
  const span = [s.started_kst, endedAt].filter(Boolean).join(" → ");
  // 청산 직전(또는 남아 있는) 포지션의 평단·수량·평가손익 — 스트립에서 보던 서브 정보를 결과에도 남긴다.
  const positionEntry = keptPosition ? Number(s.entry_price) || 0 : finalQty > 0 ? Number(s.final_entry_price) || 0 : 0;
  const positionQty = keptPosition ? Number(s.position_qty) || 0 : finalQty;
  const positionLabel = keptPosition ? "" : "(청산 직전)";
  const rows = [
    { label: "실행 시간", value: elapsed ? `${elapsed} · ${span}` : span || "—", numeric: true },
    { label: `평단${positionLabel}`, value: positionEntry > 0 ? PRICE.format(positionEntry) : "—", numeric: true },
    { label: `수량${positionLabel}`, value: positionQty > 0 ? `${QTY.format(positionQty)} ${baseAsset(s.symbol)}`.trim() : "—", numeric: true },
    { label: "마지막 평가손익", value: resultPct !== null ? formatSignedPct(resultPct) : "—", numeric: true, tone: resultPct !== null ? toneOf(resultPct) : "" },
    { label: "종목·환경", value: `${s.symbol || "—"} · ${marketLabel(s)} · ${s.testnet ? "테스트넷" : "메인넷(실거래)"}` },
    { label: "종료 방식", value: stopModeLabel(s.stop_mode) },
    { label: "마지막 가격", value: Number(s.last_price) ? PRICE.format(Number(s.last_price)) : "—", numeric: true },
    // 포지션 블록이 보여 주던 실행기 버전·출처와 청산 기준 — 결과 화면에서도 같은 자리에 남긴다.
    { label: "실행기", value: [s.runner_version ? `v${s.runner_version}` : "", s.macro_origin_label || ""].filter(Boolean).join(" · ") || "—", numeric: true },
    { label: "청산 기준", value: exitRules(s.macro, s.position_side).summary },
    {
      label: "남은 포지션",
      numeric: !!s.in_position,
      value: s.in_position
        ? `${QTY.format(Number(s.position_qty) || 0)} @ ${PRICE.format(Number(s.entry_price) || 0)} · 평가 ${formatSignedPct(s.unrealized_pct)}`
        : "없음",
    },
  ];

  return {
    eyebrow, title, detail, tone, avatar, pending: stopping,
    // 처리 중에는 확정 수치를 주장하지 않는다.
    pnl: stopping
      ? { value: null, text: "집계 중", tone: "flat" }
      : { value: Number(s.realized_pnl) || 0, text: formatSignedUsdt(s.realized_pnl), tone: toneOf(s.realized_pnl) },
    rows,
  };
}

// 종료·삭제 확인 모달의 문구. 브라우저 기본 confirm 을 대체한다.
export function describeStopConfirm(mode, session) {
  const s = session || {};
  const symbol = s.symbol || "이";
  const warning = s.testnet === false ? "메인넷(실거래) 세션이에요. 실제 자금이 움직여요." : "";
  if (mode === "close_and_stop") {
    return {
      title: "청산 후 종료할까요?",
      description: s.in_position === false
        ? `${symbol} 세션에 지금 보유 포지션이 없어요. 매크로를 멈추고 종료 보고를 남겨요.`
        : `${symbol} 포지션을 시장가로 정리한 뒤 매크로를 멈춰요. 실행기가 다음 확인에서 처리하고, 완료 보고가 오면 결과 화면으로 바뀌어요.`,
      confirmLabel: "청산하고 종료",
      tone: "danger",
      warning,
    };
  }
  return {
    title: "매크로만 종료할까요?",
    description: "포지션은 그대로 두고 매크로 실행만 멈춰요. 남은 포지션은 거래소에서 직접 정리해야 해요.",
    confirmLabel: "매크로만 종료",
    tone: "primary",
    warning: s.in_position ? warning : "",
  };
}

export function describeDeleteConfirm(session) {
  const s = session || {};
  const label = s.status === "error" ? "오류로 끝난" : "응답이 끊긴";
  return {
    title: `${label} ${s.symbol || ""} 세션을 목록에서 지울까요?`.replace(/\s+/g, " "),
    description: "기록만 사라지고 거래는 건드리지 않아요.",
    confirmLabel: "목록에서 삭제",
    tone: "danger",
    warning: "",
  };
}
