// 게시판 글줄의 작은 글자 도우미.

const KST_OFFSET_MS = 9 * 60 * 60 * 1000;

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

function kstParts(ms) {
  const d = new Date(ms + KST_OFFSET_MS);
  const pad = (n) => String(n).padStart(2, "0");
  return {
    year: d.getUTCFullYear(),
    month: pad(d.getUTCMonth() + 1),
    day: pad(d.getUTCDate()),
    hh: pad(d.getUTCHours()),
    mm: pad(d.getUTCMinutes()),
    dayKey: `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`,
  };
}

// 목록·글의 시각 — 한국 게시판 관례. 7일 안이면 `방금 전·N분 전·N시간 전·N일 전`, 올해면 `MM.DD`, 그 전이면 `YY.MM.DD`(KST).
// 잘라 쓰는 대신 규칙으로 바꾸므로 같은 칸 폭 안에서 오늘 글이 한눈에 갈린다.
export function boardTime(createdMs, nowMs = Date.now()) {
  if (!Number.isFinite(createdMs)) return "";
  // 최근 글은 "N분 전·N시간 전·N일 전"(한국 게시판 관례), 그보다 오래되면 날짜.
  const diff = Math.max(0, nowMs - createdMs);
  const minute = 60_000; const hour = 60 * minute; const day = 24 * hour;
  if (diff < minute) return "방금 전";
  if (diff < hour) return `${Math.floor(diff / minute)}분 전`;
  if (diff < day) return `${Math.floor(diff / hour)}시간 전`;
  if (diff < 7 * day) return `${Math.floor(diff / day)}일 전`;
  const t = kstParts(createdMs);
  const now = kstParts(nowMs);
  if (t.year === now.year) return `${t.month}.${t.day}`;
  return `${String(t.year).slice(2)}.${t.month}.${t.day}`;
}

// 글 상세·툴팁용 전체 시각 — `2026.09.04 15:59`.
export function boardFullTime(createdMs) {
  const value = Number(createdMs);
  if (!Number.isFinite(value)) return "";
  const t = kstParts(value);
  return `${t.year}.${t.month}.${t.day} ${t.hh}:${t.mm}`;
}

// 쪽 번호 띠 — 현재 쪽을 가운데 두고 최대 5개, 끝에서는 왼쪽으로 당겨 5개를 채운다.
export function pageWindow(page, pages, size = 5) {
  if (pages <= 0) return [];
  const count = Math.min(size, pages);
  const from = Math.max(1, Math.min(page - Math.floor(count / 2), pages - count + 1));
  return Array.from({ length: count }, (_, index) => from + index);
}
