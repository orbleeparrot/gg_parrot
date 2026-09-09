// AI 시장 요약(평문, 줄바꿈으로 3~4줄)을 머리글 타이포로 나눈다: 첫 줄은 굵은 리드, 나머지는 본문.
// 옛 캐시처럼 줄바꿈이 없는 한 덩어리면 첫 문장을 리드로 떼어낸다.
const SENTENCE_END = /(다\.|요\.|\.)\s+/;

export function splitSummary(text) {
  const lines = String(text || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  if (!lines.length) return null;
  if (lines.length === 1) {
    const match = SENTENCE_END.exec(lines[0]);
    if (match && match.index + match[0].length < lines[0].length) {
      const cut = match.index + match[1].length;
      return { lead: lines[0].slice(0, cut).trim(), body: [lines[0].slice(cut).trim()] };
    }
    return { lead: lines[0], body: [] };
  }
  return { lead: lines[0], body: lines.slice(1) };
}
