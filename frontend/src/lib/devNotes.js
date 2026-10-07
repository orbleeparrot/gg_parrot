// 개발자 노트 팝업 — 사이트에 들어오면 한 번 보여 주는 "이번에 바뀐 것". 문구는 한 줄씩, 쉬운 말로.
// 노트는 서버(/api/devnote/current)가 준다 — 관리자가 '개발자 노트에 적용하기'로 올린 [공지]를 AI 가 정리한 것.
// 서버에 노트가 없거나 못 읽으면 아래 CURRENT_NOTE 를 쓴다. id 가 바뀌면 '오늘 하루 보지 않기'를 눌렀던 사람에게도 다시 뜬다.
// 저장은 브라우저 안에서만(개인 편의) — 서버는 모른다.

export const CURRENT_NOTE = {
  id: "2026-10-07",
  date: "10.07",
  eyebrow: "이번 업데이트",
  title: "껄무새가 이렇게 바뀌었어요",
  // icon 은 components/icons.jsx 의 이름(Lucide). 이모지는 쓰지 않는다.
  items: [
    {
      icon: "star",
      title: "코치가 판을 채워 줘요",
      text: "프로 빌더 옆에서 몇 가지만 물어보고 조건을 채워 줘요. 고르기만 하면 돼요.",
      link: "/builder/pro",
      link_label: "프로 빌더 열기",
    },
    {
      icon: "trendingUp",
      title: "종목마다 비중과 방식",
      text: "여러 종목을 담을 때 비중을 따로 정하고, 종목마다 다른 매매 방식을 쓸 수 있어요.",
    },
    {
      icon: "triangleAlert",
      title: "묶음 한도",
      text: "한 번에 몇 종목까지, 자금을 몇 %까지 넣을지 묶음 전체에 걸 수 있어요.",
    },
    {
      icon: "download",
      title: "업비트·빗썸도 파일로",
      text: "매크로 파일(.ggm.json)을 내려받아 실행기에 넣어 돌릴 수 있어요. 실행기 v10 이상이 필요해요.",
      link: "/runner/install",
      link_label: "실행기 받기",
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
