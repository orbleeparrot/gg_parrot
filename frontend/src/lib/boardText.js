// 게시판 글줄의 작은 글자 도우미.

// 아바타에 쓰는 이름 첫 글자 — 한글은 첫 음절, 영문은 대문자. 이름이 없으면 물음표.
export function initialOf(name) {
  const first = String(name ?? "").trim().charAt(0);
  return first ? first.toUpperCase() : "?";
}

// "2026-09-04 15:59"(KST) → datetime 속성값. 형식이 다르면 빈 문자열(속성 생략).
export function kstDateTime(text) {
  const match = /^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})/.exec(String(text ?? "").trim());
  return match ? `${match[1]}T${match[2]}:00+09:00` : "";
}

// 쪽 번호 띠 — 현재 쪽을 가운데 두고 최대 5개, 끝에서는 왼쪽으로 당겨 5개를 채운다.
export function pageWindow(page, pages, size = 5) {
  if (pages <= 0) return [];
  const count = Math.min(size, pages);
  const from = Math.max(1, Math.min(page - Math.floor(count / 2), pages - count + 1));
  return Array.from({ length: count }, (_, index) => from + index);
}
