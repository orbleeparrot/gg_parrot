// 프로 빌더 잠금 — 지금 버전에서 멈춰 두고 다음 업데이트까지 들어가지 못하게 한다.
//
// 다시 열 때는 PRO_BUILDER_OPEN 을 true 로 바꾸면 끝이다. StudioPro.jsx 와 코치 패널,
// 서버의 코치 끝점은 **하나도 건드리지 않았다** — 잠금은 길(라우트)과 메뉴에만 걸려 있다.
// 그래서 되돌리는 데 이 파일 한 줄 말고는 필요한 수정이 없다.
export const PRO_BUILDER_OPEN = false;

export const PRO_BUILDER_LOCK_HINT = "업데이트 중";

export const PRO_BUILDER_CLOSED = Object.freeze({
  title: "프로 빌더는 잠시 닫아 두었어요",
  lead: "지금 버전에서 멈춰 두고 손보는 중이에요. 다음 업데이트에서 다시 열어 드릴게요.",
  points: [
    "그동안 기본 빌더에서 매크로를 만들고, 백테스트·모의 실행·실거래까지 그대로 하실 수 있어요.",
    "프로 빌더로 만들어 저장해 둔 매크로는 그대로 남아 있어요. 사라지지 않아요.",
    "여러 종목에 비중을 따로 주는 묶음은 다시 열릴 때 함께 돌아와요.",
  ],
});
