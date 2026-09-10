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

// 목록의 시각 — 한국 게시판 관례. 오늘(KST)이면 `HH:MM`, 올해면 `MM.DD`, 그 전이면 `YY.MM.DD`.
// 잘라 쓰는 대신 규칙으로 바꾸므로 같은 칸 폭 안에서 오늘 글이 한눈에 갈린다.
export function boardTime(createdMs, nowMs = Date.now()) {
  const value = Number(createdMs);
  if (!Number.isFinite(value)) return "";
  const t = kstParts(value);
  const n = kstParts(nowMs);
  if (t.dayKey === n.dayKey) return `${t.hh}:${t.mm}`;
  if (t.year === n.year) return `${t.month}.${t.day}`;
  return `${String(t.year).slice(-2)}.${t.month}.${t.day}`;
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

/** 본문 안 사진 자리 — `[사진1]` 처럼 첨부 순서(1부터). 백엔드 IMAGE_MARK 와 같은 규칙. */
export const IMAGE_MARK_RE = /\[사진\s*(\d+)\]/g;

export function imageMark(index1) {
  return `[사진${index1}]`;
}

/**
 * 본문을 글 조각과 사진 조각으로 나눈다. 본문에 없는 사진은 `trailing` 으로 뒤에 붙인다.
 * images: [{id, url}] 첨부 순서. 반환 { segments: [{type:"text", text} | {type:"image", image, index}], trailing: [{image, index}] }
 */
export function splitBodyWithImages(body, images = []) {
  const text = String(body || "");
  const segments = [];
  const used = new Set();
  let last = 0;
  for (const match of text.matchAll(IMAGE_MARK_RE)) {
    const index = Number(match[1]);
    const image = images[index - 1];
    if (!image) continue; // 없는 번호는 글자 그대로 둔다
    if (match.index > last) segments.push({ type: "text", text: text.slice(last, match.index) });
    segments.push({ type: "image", image, index });
    used.add(index);
    last = match.index + match[0].length;
  }
  if (last < text.length) segments.push({ type: "text", text: text.slice(last) });
  const cleaned = segments
    .map((seg) => (seg.type === "text" ? { ...seg, text: seg.text.replace(/^\n+|\n+$/g, "") } : seg))
    .filter((seg) => seg.type !== "text" || seg.text.trim());
  const trailing = images.map((image, i) => ({ image, index: i + 1 })).filter((item) => !used.has(item.index));
  return { segments: cleaned, trailing };
}

/** 사진 하나를 뺐을 때 본문의 자리 번호를 다시 매긴다 — 뺀 번호는 지우고, 그 뒤 번호는 하나씩 당긴다. */
export function renumberImageMarks(body, removedIndex1) {
  return String(body || "").replace(IMAGE_MARK_RE, (whole, n) => {
    const index = Number(n);
    if (index === removedIndex1) return "";
    if (index > removedIndex1) return imageMark(index - 1);
    return whole;
  });
}

