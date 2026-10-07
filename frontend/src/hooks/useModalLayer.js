import { useEffect, useRef } from "react";
import { lockBodyScroll } from "../lib/bodyScrollLock.js";

// 겹쳐 뜨는 창(대화상자·메뉴·말풍선)의 공용 규칙.
//
// ① Esc 는 맨 위 한 겹만 받는다. 예전에는 창마다 document 에 keydown 을 따로 달아서, 껄무새 후보
//    불러오기 확인창에서 Esc 를 누르면 물어볼까 창까지 닫히며 하루 횟수를 쓴 결과가 사라졌다.
//    처리 중(busy)인 창은 Esc 를 삼키기만 한다.
// ② 모달(trap)은 열릴 때 포커스를 안으로 옮기고 Tab 을 안에서만 돌리며, 닫히면 연 요소로 돌려준다.
// ③ 설정은 열릴 때 한 번만 한다 — 부모가 다시 그려질 때마다 효과가 다시 돌아 포커스를 확인(삭제)
//    버튼으로 끌어가던 ConfirmDialog 의 사고를 막는다. 최신 콜백·busy 는 ref 로 읽는다.
const layers = [];
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function topLayer() {
  return layers[layers.length - 1] || null;
}

function onKeyDown(event) {
  const layer = topLayer();
  if (!layer) return;
  if (event.key === "Escape") {
    event.preventDefault();
    event.stopImmediatePropagation();
    if (!layer.busy()) layer.escape();
    return;
  }
  if (event.key !== "Tab") return;
  // Tab 은 가장 위의 모달이 가둔다 — 모달 안 ⓘ 말풍선처럼 가두지 않는 겹이 위에 떠 있어도 새지 않게.
  const trapLayer = [...layers].reverse().find((item) => item.trap);
  if (!trapLayer) return;
  const root = trapLayer.ref.current;
  if (!root) return;
  const focusable = Array.from(root.querySelectorAll(FOCUSABLE)).filter((el) => el.getClientRects().length > 0);
  if (!focusable.length) {
    event.preventDefault();
    root.focus();
    return;
  }
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  const active = document.activeElement;
  if (!root.contains(active) || active === root) {
    event.preventDefault();
    (event.shiftKey ? last : first).focus();
  } else if (event.shiftKey && active === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && active === last) {
    event.preventDefault();
    first.focus();
  }
}

function push(layer) {
  if (!layers.length) document.addEventListener("keydown", onKeyDown, true);
  layers.push(layer);
}

function remove(layer) {
  const index = layers.indexOf(layer);
  if (index >= 0) layers.splice(index, 1);
  if (!layers.length) document.removeEventListener("keydown", onKeyDown, true);
}

/**
 * @param {object} options
 * @param {boolean} options.open
 * @param {React.RefObject<HTMLElement>} options.ref  창의 바깥 요소(role=dialog/menu 등)
 * @param {() => void} options.onEscape
 * @param {boolean} [options.busy]  true 면 Esc 를 삼키고 닫지 않는다
 * @param {boolean} [options.trap]  모달 — 포커스 진입·가두기·스크롤 잠금
 * @param {React.RefObject<HTMLElement>} [options.initialFocusRef]  처음 포커스할 요소(없으면 첫 컨트롤)
 * @param {boolean} [options.restoreFocus]  닫힐 때 연 요소로 포커스를 돌려줄지(기본 true)
 * @param {boolean} [options.inertRoot]  열려 있는 동안 뒤의 앱(#root)을 누르지도 포커스하지도 못하게 — body 로 포털된 창만
 * @param {boolean} [options.lockScroll]  뒤 페이지 스크롤 잠금(기본: trap 과 같음)
 */
export function useModalLayer({ open, ref, onEscape, busy = false, trap = false, initialFocusRef = null, restoreFocus = true, inertRoot = false, lockScroll = trap }) {
  const escapeRef = useRef(onEscape);
  const busyRef = useRef(busy);
  escapeRef.current = onEscape;
  busyRef.current = busy;

  useEffect(() => {
    if (!open || typeof document === "undefined") return undefined;
    const opener = document.activeElement;
    const layer = { ref, trap, escape: () => escapeRef.current?.(), busy: () => Boolean(busyRef.current) };
    push(layer);
    const unlock = lockScroll ? lockBodyScroll() : () => {};
    const app = inertRoot ? document.getElementById("root") : null;
    const wasInert = app?.inert ?? false;
    if (app) app.inert = true;
    let timer = 0;
    if (trap) {
      // 포털이 붙은 뒤에 옮긴다. 이미 안쪽 칸(자동 포커스 입력 등)에 있으면 건드리지 않는다.
      timer = window.setTimeout(() => {
        const root = ref.current;
        if (!root || root.contains(document.activeElement)) return;
        const target = initialFocusRef?.current || root.querySelector(FOCUSABLE) || root;
        if (target === root && !root.hasAttribute("tabindex")) root.setAttribute("tabindex", "-1");
        target.focus({ preventScroll: true });
      }, 0);
    }
    return () => {
      window.clearTimeout(timer);
      remove(layer);
      unlock();
      if (app) app.inert = wasInert;
      if (!restoreFocus) return;
      // 닫힌 창 안에 포커스가 있었을 때만 돌려준다 — 사용자가 이미 다른 곳으로 옮겼으면 그대로 둔다.
      const active = document.activeElement;
      const lost = !active || active === document.body || !document.contains(active) || ref.current?.contains(active);
      if (!lost) return;
      // 아래에 아직 모달이 열려 있으면 그 안으로 — 첫 방문 노트를 닫자 포커스가 뒤에 열린 물어볼까 창 밖(페이지)으로
      // 빠지던 문제. 연 요소가 그 모달 안에 있을 때만 연 요소로 돌려준다.
      const below = [...layers].reverse().find((item) => item.trap)?.ref.current;
      const target = below && !(opener && below.contains(opener)) ? below.querySelector(FOCUSABLE) || below : opener;
      if (target && target !== document.body && document.contains(target) && typeof target.focus === "function") {
        window.requestAnimationFrame(() => target.focus({ preventScroll: true }));
      }
    };
    // 열림 상태가 바뀔 때만 다시 설정한다(콜백·busy 는 ref 로).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
}

// 테스트용 — 지금 쌓인 겹 수.
export const openLayerCount = () => layers.length;
