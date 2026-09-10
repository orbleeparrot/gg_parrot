// 채팅 답장 — 본문 맨 앞의 `[reply:id]` 토큰. 서버가 인용할 원 메시지(reply_to)를 붙여 주고,
// 화면은 인용 줄을 그린 뒤 본문에서는 토큰을 뺀다(매크로 언급과 같은 방식).

export const REPLY_TOKEN_RE = /^\[reply:(\d{1,12})\]\s*/;

export function replyText(messageId, text) {
  return `[reply:${messageId}] ${String(text || "").trim()}`.trim();
}

/** 화면에 보일 본문 — 맨 앞 답장 토큰을 뺀 나머지. */
export function stripReplyToken(text) {
  return String(text || "").replace(REPLY_TOKEN_RE, "");
}

/** 답장이 가리키는 메시지 번호(없으면 null). */
export function replyTargetId(text) {
  const match = REPLY_TOKEN_RE.exec(String(text || ""));
  return match ? Number(match[1]) : null;
}
