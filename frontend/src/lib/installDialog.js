// 앱 설치 대화상자 열고 닫기 — 머리글 · 휴대폰 메뉴 · 설정 · 종 단추 어디서 열어도 화면에는 하나만 뜬다(AppInstallDialog).
import { useSyncExternalStore } from "react";

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

export function useAppInstallOpen() {
  return useSyncExternalStore(subscribe, () => open, () => false);
}
