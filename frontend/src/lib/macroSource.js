// 조건 판 머리의 매크로 출처 배지 (2026-09-23).
// 조건이 어디서 왔는지(껄무새 후보 · 리더보드 복사 · 매크로 업로드 · 시작 가이드 · 공유 링크)를 한 배지로 보여 준다.
// 배지 글자는 출처 이름이 아니라 **티커 + 종목명**이고, 출처는 색과 아이콘으로만 구분한다(DESIGN.md §6 badge · 색은 시맨틱).
import { baseOf } from "./format.js";

export const MACRO_SOURCES = {
  ask: { label: "껄무새 후보", tone: "ask" },
  board: { label: "리더보드에서 복사", tone: "board" },
  file: { label: "매크로 업로드", tone: "file" },
  guide: { label: "시작 가이드에서 고른 설정", tone: "guide" },
  shared: { label: "공유 링크", tone: "board" },
};

// 자주 다루는 종목의 한글 이름 — 없는 종목은 티커만 보여 준다(만들어 내지 않는다).
export const COIN_NAMES = {
  BTC: "비트코인", ETH: "이더리움", BNB: "비앤비", SOL: "솔라나", XRP: "리플", DOGE: "도지코인", ADA: "에이다", TRX: "트론",
  AVAX: "아발란체", LINK: "체인링크", TON: "톤코인", DOT: "폴카닷", MATIC: "폴리곤", POL: "폴리곤", LTC: "라이트코인",
  BCH: "비트코인캐시", SHIB: "시바이누", NEAR: "니어", UNI: "유니스왑", APT: "앱토스", SUI: "수이", ARB: "아비트럼",
  OP: "옵티미즘", ATOM: "코스모스", XLM: "스텔라루멘", ETC: "이더리움클래식", FIL: "파일코인", HBAR: "헤데라",
  PEPE: "페페", "1000PEPE": "페페", WIF: "도그위프햇", SEI: "세이", INJ: "인젝티브", TIA: "셀레스티아", AAVE: "에이브",
  ICP: "인터넷컴퓨터", RENDER: "렌더", FET: "페치", STX: "스택스", IMX: "이뮤터블", ENA: "에테나", ONDO: "온도",
  PENDLE: "펜들", JUP: "주피터", WLD: "월드코인", TAO: "비텐서", ORDI: "오디", BONK: "봉크", "1000BONK": "봉크", FLOKI: "플로키",
  "1000FLOKI": "플로키", "1000SHIB": "시바이누", TRUMP: "트럼프", HYPE: "하이퍼리퀴드", VIRTUAL: "버추얼",
};

export function coinName(symbolOrBase) {
  const base = baseOf(symbolOrBase || "");
  return COIN_NAMES[base] || "";
}

// 저장해 둔 값에서 출처를 읽는다 — 옛 세션의 문자열(loadedFrom)이나 모르는 종류는 버린다.
export function readMacroSource(value) {
  if (!value || typeof value !== "object") return null;
  if (!MACRO_SOURCES[value.kind]) return null;
  return { kind: value.kind, label: typeof value.label === "string" ? value.label : "" };
}

// 배지에 보일 것 — 지금 조건 판의 종목(쉼표로 여러 개면 첫 종목 + N)에서 티커·종목명을 뽑고, 출처에서 색·설명을 뽑는다.
export function macroSourceBadge(source, formSymbol) {
  const symbols = String(formSymbol || "").split(",").map((s) => s.trim()).filter(Boolean);
  const first = symbols[0] || "";
  const ticker = baseOf(first);
  const meta = source && MACRO_SOURCES[source.kind];
  const tone = meta ? meta.tone : "none";
  const more = symbols.length > 1 ? symbols.length - 1 : 0;
  const name = coinName(first);
  const parts = [meta ? meta.label : "직접 설정"];
  if (source?.label) parts.push(source.label);
  if (ticker) parts.push(more ? `${ticker} 외 ${more}종목` : (name ? `${ticker} ${name}` : ticker));
  return { kind: meta ? source.kind : "none", tone, ticker, name, more, title: parts.join(" · ") };
}
