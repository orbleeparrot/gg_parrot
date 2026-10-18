// 내 에이전트 세션의 '무슨 코인 · 어느 거래소 · 무슨 전략' — 실행 바 선택 목록 · 종료 기록 바 · 차트 머리가 같은 글자를 쓴다.
// 마켓 이름(KRW-B3 · BTCUSDT)은 보이지 않는다 — 거래소마다 마켓이 하나뿐이라 거래소 작은 로고와 겹친다(2026-10-18 시안 B).
// 남기는 건 바이낸스 선물의 '선물 N배'뿐 — 같은 BTC 라도 현물과 선물은 다른 실행이다.
import { RULE_TYPES } from "../../lib/macro.js";
import { baseOf } from "../../lib/format.js";
import { isDomesticSymbol, practiceModeLabel } from "../../lib/exchanges.js";

const KNOWN_EXCHANGES = new Set(["binance", "upbit", "bithumb"]);

/** 큰 글씨 티커 — "KRW-B3" → "B3", "BTCUSDT" → "BTC". */
export function coinTicker(symbol) {
  return baseOf(symbol || "") || String(symbol || "");
}

/** 세션의 거래소. 세션엔 거래소 칸이 없어 매크로에서 읽는다 — 모르는 원화 종목은 업비트 · 빗썸 중 어디인지 모르니 null. */
export function sessionExchange(session, macro = session?.macro) {
  const value = String(macro?.exchange || "").trim().toLowerCase();
  if (KNOWN_EXCHANGES.has(value)) return value;
  return isDomesticSymbol(session?.symbol) ? null : "binance";
}

/** 바이낸스 선물만 "선물 3배"(레버리지를 모르거나 1이면 "선물"). 현물 · 국내 거래소는 빈 글자. */
export function futuresNote(market, leverage) {
  if (market !== "futures") return "";
  const n = Number(leverage);
  return Number.isFinite(n) && n > 1 ? `선물 ${Math.round(n)}배` : "선물";
}

/** 둘째 줄 — "이동평균 크로스 · 실전". 전략 이름 앞 글자표(J · )는 뺀다. */
export function strategyLine(session, macro = session?.macro) {
  const label = RULE_TYPES[macro?.rule_type]?.label || macro?.rule_type || "매크로";
  const strategy = String(label).replace(/^[A-Z] · /, "");
  const mode = session?.testnet ? practiceModeLabel(session?.symbol, session?.mode) : "실전";
  return `${strategy} · ${mode}`;
}

/** 목록 오른쪽 상태 — 종료 · 오류 · 응답대기 · 손익 · 진입 대기 · 실행 중. tone 은 글자 색(down · up · warn · muted). */
export function sessionStatus(session, { waiting = false } = {}) {
  if (session?.status === "stopped") return { text: "종료", tone: "muted" };
  if (session?.status === "error") return { text: "오류", tone: "down" };
  if (!session?.connected) return { text: "응답대기", tone: "warn" };
  if (session?.in_position) {
    // 실행기 화면과 같은 수수료 반영 평가손익, 옛 실행기면 평단 대비 가격(positionPnl).
    const pct = Number(session.unrealized_net_pct ?? session.unrealized_pct);
    if (!Number.isFinite(pct)) return { text: "보유 중", tone: "muted" };
    const sign = pct > 0 ? "+" : pct < 0 ? "−" : "";
    return { text: `${sign}${Math.abs(pct).toFixed(2)}%`, tone: pct > 0 ? "up" : pct < 0 ? "down" : "muted" };
  }
  if (waiting) return { text: "진입 대기", tone: "warn" };
  return { text: "실행 중", tone: "muted" };
}
