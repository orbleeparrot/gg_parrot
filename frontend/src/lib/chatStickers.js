// 채팅 스티커 — 껄무새 표정 6종. 메시지 본문에 "[sticker:id]" 토큰으로 실어 보내고
// 화면에서 이미지로 그린다(서버는 텍스트를 그대로 저장하므로 변경 없음).
export const STICKERS = [
  { id: "calm", label: "여유", src: "/brand/agent/ggparrot-agent-calm-v1.svg" },
  { id: "curious", label: "궁금", src: "/brand/agent/ggparrot-agent-curious-v1.svg" },
  { id: "focused", label: "집중", src: "/brand/agent/ggparrot-agent-focused-v1.svg" },
  { id: "signal", label: "환호", src: "/brand/agent/ggparrot-agent-signal-v1.svg" },
  { id: "warning", label: "깜짝", src: "/brand/agent/ggparrot-agent-warning-v1.svg" },
  { id: "critical", label: "분노", src: "/brand/agent/ggparrot-agent-critical-v1.svg" },
];

const TOKEN = /^\[sticker:([a-z]+)\]$/;

export function stickerText(id) {
  return `[sticker:${id}]`;
}

// 본문 전체가 스티커 토큰일 때만 스티커다 — 글 사이에 낀 토큰은 그냥 글자로 둔다.
export function stickerFromText(text) {
  const match = TOKEN.exec(String(text || "").trim());
  if (!match) return null;
  return STICKERS.find((sticker) => sticker.id === match[1]) || null;
}
