// 개발자 노트 팝업 — 사이트에 들어오면 한 번 보여 주는 "이번에 바뀐 것". 문구는 한 줄씩, 쉬운 말로.
// 새 노트를 내려면 CURRENT_NOTE 의 id 를 바꾼다(그러면 '오늘 하루만 보기'를 눌렀던 사람에게도 다시 뜬다).
// 저장은 브라우저 안에서만(개인 편의) — 서버는 모른다.

export const CURRENT_NOTE = {
  id: "2026-09-22",
  title: "껄무새가 이렇게 바뀌었어요",
  items: [
    {
      icon: "🔄",
      text: "실행기를 새로 받아야 해요. 예전 실행기로는 매크로가 돌지 않아요.",
      action: { label: "새 실행기 받기", to: "/runner/install" },
    },
    { icon: "💬", text: "리더보드 채팅에서 내 방을 만들 수 있어요. 종목 토론방을 열어 보세요." },
    { icon: "📈", text: "리더보드 수익률이 실시간으로 움직이고, 지금 상태(보유·대기)가 보여요." },
    { icon: "🦜", text: "물어볼까 5번을 다 쓰면 30P로 한 번 더 물어볼 수 있어요." },
    { icon: "🔑", text: "실행기가 키를 기억해 줘요. 다시 입력하지 않아도 돼요." },
  ],
};

const HIDE_KEY = "devnote:hide-today";
const SESSION_KEY = "devnote:closed";

function read(storage, key) {
  try { return storage?.getItem(key) ?? null; } catch { return null; }
}
function write(storage, key, value) {
  try { storage?.setItem(key, value); } catch { /* 사생활 모드·차단 — 그냥 매번 보여 준다 */ }
}

// 보여 줄지: 이 탭에서 이미 닫았거나, 오늘 하루 숨김이 이 노트 id 로 오늘 기록돼 있으면 안 보여 준다.
export function shouldShowDevNote({ local, session, todayKst, note = CURRENT_NOTE }) {
  if (read(session, SESSION_KEY) === note.id) return false;
  const raw = read(local, HIDE_KEY);
  if (!raw) return true;
  try {
    const saved = JSON.parse(raw);
    return !(saved && saved.id === note.id && saved.day === todayKst);
  } catch {
    return true;
  }
}

export function dismissDevNote({ local, session, todayKst, hideToday, note = CURRENT_NOTE }) {
  write(session, SESSION_KEY, note.id);
  if (hideToday) write(local, HIDE_KEY, JSON.stringify({ id: note.id, day: todayKst }));
}

// KST 날짜(YYYY-MM-DD) — '오늘 하루만'의 기준. 리더보드·퀘스트와 같은 자정 기준.
export function todayKst(now = new Date()) {
  return new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
}
