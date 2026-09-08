// 투표를 누르는 즉시 화면에 반영하기 위한 순수 계산 — 서버 규칙과 같다:
// 같은 값을 다시 누르면 취소, 다른 값을 누르면 바꾼다.
export function applyVote(entry, value) {
  const next = value > 0 ? 1 : -1;
  const current = Number(entry?.my_vote) || 0;
  let likes = Number(entry?.likes) || 0;
  let dislikes = Number(entry?.dislikes) || 0;
  if (current === 1) likes -= 1;
  if (current === -1) dislikes -= 1;
  const my_vote = current === next ? 0 : next;
  if (my_vote === 1) likes += 1;
  if (my_vote === -1) dislikes += 1;
  return { ...entry, likes: Math.max(0, likes), dislikes: Math.max(0, dislikes), my_vote };
}

// 서버가 확정한 수치를 해당 항목에만 덮어쓴다.
export function settleVote(items, result) {
  if (!result || result.entry_id == null) return items;
  return items.map((entry) => (
    entry.id === result.entry_id
      ? { ...entry, likes: result.likes, dislikes: result.dislikes, my_vote: result.my_vote ?? entry.my_vote }
      : entry
  ));
}
