// 글자 복사 — navigator.clipboard 가 권한·보안 문맥 때문에 막히면 숨긴 입력칸을 골라 복사로 한 번 더 시도한다.
// 둘 다 안 되면 false — 부르는 쪽이 "직접 복사해 주세요" 를 보여 준다(브라우저 기본 prompt 창은 쓰지 않는다).
export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    if (typeof document === "undefined") return false;
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.className = "sr-only";
    const active = document.activeElement;
    document.body.appendChild(area);
    area.select();
    let copied = false;
    try {
      copied = document.execCommand("copy");
    } catch {
      copied = false;
    }
    area.remove();
    if (active && typeof active.focus === "function") active.focus({ preventScroll: true });
    return copied;
  }
}
