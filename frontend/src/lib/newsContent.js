// Source provenance, not an AI judgement of whether a claim is true.
export function newsContentKind(item) {
  return item?.content_type === 'community' || item?.community_post_id
    || /^binance\s*square$/i.test(String(item?.source || '').trim()) ? 'community' : 'news';
}

export function newsContentLabel(item) {
  return newsContentKind(item) === 'community' ? '커뮤니티 의견 · 사실 확인 안 됨' : '보도 기사';
}

export function filterNewsContent(items = [], scope = 'news') {
  const selected = ['news', 'community', 'all'].includes(scope) ? scope : 'news';
  return items.filter((item) => item && (selected === 'all' || newsContentKind(item) === selected));
}
