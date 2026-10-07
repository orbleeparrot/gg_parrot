// Anonymous, client-side identity for the leaderboard (no login, no server PII).
// A stable random id lives in localStorage; the nickname is user-editable.

const ID_KEY = "ggp_uid";
const NICK_KEY = "ggp_nick";

// 저장소가 막힌 브라우저에서는 이 탭 동안만 쓰는 아이디로 — 예전엔 예외가 나 리더보드 화면이 깨졌다.
let memoryId = "";
const newId = () => "u_" + Math.random().toString(36).slice(2) + Date.now().toString(36);

export function getUserId() {
  try {
    let id = localStorage.getItem(ID_KEY);
    if (!id) {
      id = newId();
      localStorage.setItem(ID_KEY, id);
    }
    return id;
  } catch {
    memoryId ||= newId();
    return memoryId;
  }
}

export function getNickname() {
  try {
    return localStorage.getItem(NICK_KEY) || "";
  } catch {
    return "";
  }
}

export function setNickname(name) {
  try {
    localStorage.setItem(NICK_KEY, (name || "").trim().slice(0, 24));
  } catch {
    /* 막힌 저장소 */
  }
}
