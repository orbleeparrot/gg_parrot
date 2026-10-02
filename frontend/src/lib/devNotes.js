// 개발자 노트 팝업 — 사이트에 들어오면 한 번 보여 주는 "이번에 바뀐 것". 문구는 한 줄씩, 쉬운 말로.
// 새 노트를 내려면 CURRENT_NOTE 의 id 를 바꾼다(그러면 '오늘 하루만 보기'를 눌렀던 사람에게도 다시 뜬다).
// 저장은 브라우저 안에서만(개인 편의) — 서버는 모른다.

export const CURRENT_NOTE = {
  id: "2026-10-02",
  date: "10.02",
  eyebrow: "이번 업데이트",
  title: "껄무새가 이렇게 바뀌었어요",
  // icon 은 components/icons.jsx 의 이름(Lucide). 이모지는 쓰지 않는다.
  items: [
    {
      icon: "mousePointerClick",
      title: "리더보드는 오른쪽 클릭으로",
      text: "복사·수정·삭제를 오른쪽 클릭(휴대폰은 길게 누르기)으로 해요. 내 매크로는 테두리로 보여요.",
    },
    {
      icon: "messageSquare",
      title: "채팅 카드에 수익률",
      text: "채팅에서 / 로 언급한 매크로 카드에 지금 수익률이 크게 보여요.",
    },
    {
      icon: "bookmark",
      title: "종료 기록 · 보관",
      text: "내 에이전트에서 끝난 실행은 종료 기록으로 모여요. 보관한 기록은 30일이 지나도 남아요.",
      action: { label: "내 에이전트 보기", to: "/agents" },
    },
    {
      icon: "trendingUp",
      title: "업비트·빗썸 원화 종목",
      text: "원화(KRW) 종목으로 차트를 보고 백테스트할 수 있어요.",
    },
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
