// 방금 보낸 메시지를 다음 폴링을 기다리지 않고 목록에 붙인다. 같은 id 가 이미 있으면 그대로.
export function appendMessage(items, message) {
  if (!message || message.id == null) return items;
  const list = Array.isArray(items) ? items : [];
  if (list.some((item) => item.id === message.id)) return list;
  return [...list, message];
}
