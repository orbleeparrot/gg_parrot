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
  if (state.status === "error") {
    return "뉴스 서버 연결이 지연되고 있어요. 마지막 확인 기사는 유지하며 조회를 다시 시도해요.";
  }
  if (["error", "rate_limited", "unavailable"].includes(data?.collection?.status)) {
    return "뉴스 수집 소스 확인에 실패했어요. 서버 연결 및 번역 상태와는 별개예요.";
  }
  const translation = data?.translation;
  if (translation?.status === "paused") {
    return ["insufficient_quota", "credit_balance_exhausted"].includes(translation.pause_reason)
      ? "AI 크레딧·사용 한도로 번역·요약 처리가 중단됐어요. 기사 수집 및 서버 연결과는 별개예요."
      : "AI 번역·요약 처리가 일시 중단됐어요. 기사 수집 및 서버 연결과는 별개예요.";
  }
  if (translation?.status === "failed") {
    const count = Number(translation.failed_count) || 0;
    return `${count > 0 ? `뉴스 ${count}건의` : "뉴스"} 번역·요약에 실패했어요. 수집한 기사와 처리 실패를 구분해 표시해요.`;
  }
  if (["partial", "pending", "waiting"].includes(translation?.status)) {
    const count = Number(translation.waiting_count ?? translation.pending_count) || 0;
    const failed = Number(translation.failed_count) || 0;
    return `${count > 0 ? `뉴스 ${count}건의` : "뉴스"} 번역·요약 처리 대기 상태예요.${failed > 0 ? ` 처리 실패 ${failed}건이 있어요.`
      : " 실제 처리 중인 건수는 아니며 실패한 항목이 포함될 수 있어요."}`;
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
