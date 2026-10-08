// 서버·브라우저 오류를 사용자에게 보일 문장으로 — DESIGN.md §8 "에러는 멈춰 세우지 않고 다음 화면으로 안내한다".
// 서버가 한국어로 쓴 detail 은 사용자를 위해 쓴 문장이라 그대로 두고, 영어 원문(Internal Server Error,
// macro not found, 422 의 JSON 배열, Failed to fetch)은 상태 코드에 맞는 안내로 바꾼다. 원문은 error.detail 에 남긴다.
const HANGUL = /[가-힣]/;

export const NETWORK_MESSAGE = "인터넷 연결을 확인하고 다시 시도해 주세요.";

const BY_STATUS = {
  400: "요청을 처리하지 못했어요. 입력한 내용을 확인해 주세요.",
  401: "로그인이 필요하거나 만료됐어요. 다시 로그인해 주세요.",
  403: "이 작업을 할 수 있는 권한이 없어요.",
  404: "찾을 수 없어요. 지워졌거나 주소가 바뀌었을 수 있어요.",
  408: "응답이 지연되고 있어요. 잠시 후 다시 시도해 주세요.",
  409: "다른 곳에서 먼저 바뀌었어요. 새로 불러온 뒤 다시 시도해 주세요.",
  413: "보내려는 내용이 너무 커요. 크기를 줄여 다시 시도해 주세요.",
  422: "입력한 값을 확인해 주세요.",
  429: "요청이 몰리고 있어요. 잠시 후 다시 시도해 주세요.",
};

export function friendlyMessage(status, detail) {
  const text = typeof detail === "string" ? detail.trim() : "";
  if (text && HANGUL.test(text)) return text;
  if (BY_STATUS[status]) return BY_STATUS[status];
  if (status >= 500) return "서버에 문제가 생겼어요. 잠시 후 다시 시도해 주세요.";
  return "요청을 처리하지 못했어요. 잠시 후 다시 시도해 주세요.";
}

// 응답 본문이 JSON 이 아니다 — 500 대(프레임워크 평문 오류)와 2xx HTML(프록시·배포 중 페이지)을 가른다.
export function nonJsonMessage(status) {
  if (status >= 500) return friendlyMessage(status, "");
  return "서버가 예상과 다른 응답을 보냈어요. 잠시 후 다시 시도해 주세요.";
}

// fetch 가 던진 오류 — 취소·시간 초과는 그대로, 연결 실패(TypeError)는 안내 문장으로.
export function networkError(reason) {
  if (reason?.name === "AbortError" || reason?.name === "TimeoutError" || reason?.status) return reason;
  if (reason instanceof TypeError) {
    const error = new Error(NETWORK_MESSAGE);
    error.code = "NETWORK";
    error.detail = reason.message;
    return error;
  }
  return reason;
}
