const KST_OFFSET_MS = 9 * 60 * 60 * 1000;

export function publicationTime(value) {
  const parsed = typeof value === "number" ? value : Date.parse(value || "");
  return Number.isFinite(parsed) && parsed > 0 && Number.isFinite(new Date(parsed).getTime()) ? parsed : null;
}

export function publicationLabel(value) {
  const parsed = publicationTime(value);
  if (parsed === null) return "게시일 확인 불가";
  const date = new Date(parsed + KST_OFFSET_MS);
  const two = (number) => String(number).padStart(2, "0");
  return `게시 ${date.getUTCFullYear()}.${two(date.getUTCMonth() + 1)}.${two(date.getUTCDate())} ${two(date.getUTCHours())}:${two(date.getUTCMinutes())} KST`;
}

export function positionNewsNotice(state, { hasArticles = false } = {}) {
  if (!state) return "";
  const data = state.data;
  if (state.status === "error" || data?.collection?.status === "error") {
    return "뉴스 검색이 지연되고 있어요. 수집 연결을 다시 확인하고 있어요.";
  }
  if (data?.translation?.status === "partial") {
    const count = Number(data.translation.pending_count) || 0;
    return `${count > 0 ? `뉴스 ${count}건을` : "뉴스를"} 한국어로 번역 중이에요. 완료되면 표시해요.`;
  }
  if ((!data && ["idle", "loading"].includes(state.status))
      || data?.analysis_status === "pending" || data?.collection?.status === "pending") {
    return "관련 기사를 찾고 있어요.";
  }
  if (data?.collection?.freshness === "stale") {
    return hasArticles ? "최신 기사를 다시 확인하고 있어요. 표시된 기사의 게시일을 확인해 주세요."
      : "뉴스 갱신이 지연되고 있어요. 관련 기사를 다시 찾고 있어요.";
  }
  if (data && !(data.items || []).length) {
    return hasArticles ? "새로 수집한 기사가 없어 기존 기사를 유지하고 있어요."
      : "아직 수집한 관련 기사가 없어요.";
  }
  if (data?.content_scope === "archive" || (data?.items?.length
      && data.items.every((item) => item.is_historical))) {
    return "최근 기사가 없어 과거 관련 기사를 보여드려요.";
  }
  return "";
}

export function countNewObservations(visible, previousIds) {
  return visible.filter((event) => event.notify !== false && !previousIds.has(event.id)).length;
}
