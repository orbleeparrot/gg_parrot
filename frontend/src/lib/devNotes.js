// 개발자 노트 팝업 — 사이트에 들어오면 한 번 보여 주는 "이번에 바뀐 것". 문구는 한 줄씩, 쉬운 말로.
// 노트는 서버(/api/devnote/current)가 준다 — 관리자가 '개발자 노트에 적용하기'로 올린 [공지]를 AI 가 정리한 것.
// 서버에 노트가 없거나 못 읽으면 아래 CURRENT_NOTE 를 쓴다. id 가 바뀌면 '오늘 하루 보지 않기'를 눌렀던 사람에게도 다시 뜬다.
// 저장은 브라우저 안에서만(개인 편의) — 서버는 모른다.

export const CURRENT_NOTE = {
  id: "2026-10-08",
  date: "10.08",
  eyebrow: "이번 업데이트",
  title: "껄무새가 이렇게 바뀌었어요",
  // icon 은 components/icons.jsx 의 이름(Lucide). 이모지는 쓰지 않는다.
  items: [
    {
      icon: "triangleAlert",
      title: "프로 빌더는 업데이트 중",
      text: "손볼 게 있어서 잠시 닫아 두었어요. 그동안 기본 빌더에서 다 하실 수 있어요.",
      link: "/builder",
      link_label: "기본 빌더 열기",
    },
    {
      icon: "trendingUp",
      title: "추천이 깐깐해졌어요",
      text: "그냥 들고 있는 것보다 확실히 나은 조합만 보여 줘요. 없으면 없다고 말해요.",
      link: "/builder",
      link_label: "물어볼까 열기",
    },
    {
      icon: "bookOpen",
      title: "업비트·빗썸 키 발급 안내",
      text: "국내 거래소 API 키를 어디서 어떤 권한으로 만드는지 사용 설명에 정리했어요.",
      // 사용 설명은 ?section=<id> 로 바로 그 항목을 연다(Guide.jsx 가 searchParams 를 읽는다).
      link: "/guide?section=domestic-api",
      link_label: "설명 보기",
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
