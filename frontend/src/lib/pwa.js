// 앱(PWA) 설치 — 어떤 기기인지, 이미 앱으로 열었는지, 안드로이드 · 크롬의 '바로 설치'(beforeinstallprompt).
// beforeinstallprompt 는 화면이 뜨기 전에 올 수 있어 main.jsx 가 제일 먼저 captureInstallPrompt() 를 부른다.

// 앱 안 브라우저(카카오톡 · 네이버 · 인스타그램 등)는 홈 화면 추가 · 알림이 안 된다 — 먼저 기본 브라우저로 열게 한다.
const IN_APP = /KAKAOTALK|NAVER\(inapp|DaumApps|Instagram|FBAN|FBAV|FB_IAB|Line\/|everytimeApp|BAND\//i;

export function detectPlatform({ userAgent = "", platform = "", maxTouchPoints = 0 } = {}) {
  // iPadOS 13+ 는 '맥' 으로 자신을 소개한다 — 터치가 되는 맥은 아이패드다.
  if (/iPhone|iPad|iPod/i.test(userAgent) || (platform === "MacIntel" && maxTouchPoints > 1)) return "ios";
  if (/Android/i.test(userAgent)) return "android";
  return "desktop";
}

export function detectBrowser({ userAgent = "" } = {}) {
  if (IN_APP.test(userAgent)) return "in-app";
  if (/SamsungBrowser/i.test(userAgent)) return "samsung";
  if (/CriOS|FxiOS|EdgiOS|OPiOS/i.test(userAgent)) return "ios-other"; // 아이폰의 크롬 · 파이어폭스 · 엣지
  if (/Edg\//i.test(userAgent)) return "edge";
  if (/Firefox\//i.test(userAgent)) return "firefox";
  if (/Chrome\/|Chromium\//i.test(userAgent)) return "chrome";
  if (/Safari\//i.test(userAgent)) return "safari";
  return "other";
}

export function isStandalone(win = globalThis.window) {
  if (!win) return false;
  try {
    if (win.matchMedia?.("(display-mode: standalone)").matches || win.matchMedia?.("(display-mode: window-controls-overlay)").matches) return true;
  } catch {
    /* 오래된 브라우저 */
  }
  return win.navigator?.standalone === true; // iOS 홈 화면 앱
}

export function currentDevice(win = globalThis.window) {
  const nav = win?.navigator || {};
  const env = { userAgent: String(nav.userAgent || ""), platform: String(nav.platform || ""), maxTouchPoints: Number(nav.maxTouchPoints || 0) };
  return { platform: detectPlatform(env), browser: detectBrowser(env), standalone: isStandalone(win) };
}

// 앱 설치 안내를 보일 화면 — 아직 앱으로 열지 않았으면 어디서나. 휴대폰 · 태블릿은 그 기기에 설치하고, PC 는 화면의
// QR 을 휴대폰으로 찍어 휴대폰에 설치한다(11/09 — 사용자는 PC 화면을 보며 휴대폰으로 찍는다). PC 자체에 설치하는
// 안내는 두지 않는다(PC 알림은 데스크톱 실행기가 맡는다, 11/06).
export function offersAppInstall(device = currentDevice()) {
  return !device.standalone;
}

// --- 바로 설치 -----------------------------------------------------------------
let deferredPrompt = null;
let installedNow = false;
let snapshot = { canPrompt: false, installed: false }; // useSyncExternalStore 는 같은 상태면 같은 객체를 받아야 한다
const listeners = new Set();

function emit() {
  snapshot = { canPrompt: Boolean(deferredPrompt), installed: installedNow };
  for (const listener of listeners) listener();
}

export function captureInstallPrompt(win = globalThis.window) {
  if (!win?.addEventListener) return;
  win.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault(); // 크롬이 스스로 띄우는 아래 띠 대신 우리 '바로 설치' 버튼으로 받는다
    deferredPrompt = event;
    emit();
  });
  win.addEventListener("appinstalled", () => {
    deferredPrompt = null;
    installedNow = true;
    emit();
  });
}

export function installState() {
  return snapshot;
}

export function subscribeInstall(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

// 브라우저 설치 창을 띄운다. accepted | dismissed | unavailable
export async function promptInstall() {
  const event = deferredPrompt;
  if (!event) return "unavailable";
  deferredPrompt = null; // 한 번 띄운 이벤트는 다시 못 쓴다
  emit();
  try {
    await event.prompt();
    const choice = await event.userChoice;
    return choice?.outcome === "accepted" ? "accepted" : "dismissed";
  } catch {
    return "unavailable";
  }
}

// 시험용 — 모듈 상태를 처음으로
export function resetInstallStateForTest(event = null) {
  deferredPrompt = event;
  installedNow = false;
  emit();
}
