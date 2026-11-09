// 앱 설치 대화상자 열고 닫기 — 머리글 · 휴대폰 메뉴 · 설정 · 종 단추 어디서 열어도 화면에는 하나만 뜬다(AppInstallDialog).
import { useSyncExternalStore } from "react";
import { offersAppInstall } from "./pwa.js";

// PC 대화상자의 QR(public/app-install-qr.svg)이 여는 주소의 표시 — https://gg-parrot.vercel.app/?install=1
export const INSTALL_PARAM = "install";

let open = false;
const listeners = new Set();

function emit() {
  for (const listener of listeners) listener();
}

export function openAppInstall() {
  if (open) return;
  open = true;
  emit();
}

export function closeAppInstall() {
  if (!open) return;
  open = false;
  emit();
}

function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

// QR 로 들어오면(?install=1) 설치 안내를 바로 연다 — 표시는 주소에서 지워 새로고침 · 공유 때 다시 뜨지 않게.
// 이미 앱으로 열었으면 열지 않는다. 화면을 그리기 전에(main.jsx) 불러 라우터가 지운 주소로 시작하게 한다.
export function openAppInstallFromLink(win = globalThis.window, offers = offersAppInstall) {
  let url;
  try {
    url = new URL(win.location.href);
  } catch {
    return false;
  }
  if (url.searchParams.get(INSTALL_PARAM) !== "1") return false;
  url.searchParams.delete(INSTALL_PARAM);
  try {
    win.history.replaceState(win.history.state, "", `${url.pathname}${url.search}${url.hash}`);
  } catch {
    /* 주소를 못 바꿔도 안내는 연다 */
  }
  if (!offers()) return false;
  openAppInstall();
  return true;
}

export function useAppInstallOpen() {
  return useSyncExternalStore(subscribe, () => open, () => false);
}
