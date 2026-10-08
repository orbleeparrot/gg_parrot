// 토스트가 뜨는 자리 — body 에 하나만 두고 앱 토스트(AppToast)와 알림 토스트(NotificationToasts)가 함께 쓴다.
// 예전에는 둘이 같은 위치에 각자 상자를 만들어, 동시에 뜨면 서로 겹쳐 보였다.
// 처음부터 있는 status 영역이어야 화면 읽기 프로그램이 새로 들어온 토스트를 읽어 준다(main.jsx 에서 미리 만든다).
export function toastHost() {
  if (typeof document === "undefined") return null;
  let host = document.getElementById("ggp-toasts");
  if (!host) {
    host = document.createElement("div");
    host.id = "ggp-toasts";
    host.className = "ggp-toasts";
    host.setAttribute("role", "status");
    host.setAttribute("aria-live", "polite");
    host.setAttribute("aria-label", "알림");
    document.body.appendChild(host);
  }
  return host;
}
