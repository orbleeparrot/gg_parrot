// 기기별 앱 설치 안내(3단계) — 화면(AppInstallDialog)은 이 데이터만 그린다.
// 설치는 휴대폰(Android · iPhone · 아이패드)에 — PC 에서는 01 단계가 QR 찍기(11/09 시안 A), PC 자체 설치 안내는 없다(11/06).
// 글 조각: 문자열 | { b: "굵게(메뉴 이름)" } | { br: true } | { icon: "dots-v" | "dots-h" | "share" | "add" | "install" | "menu" }
// 단계: { title, text, hint, qr } — qr 이면 화면이 그 단계에 QR 그림(public/app-install-qr.svg)을 함께 그린다.
// 메뉴 이름은 각 브라우저 한국어 화면 그대로 쓴다(줄이지 않는다).

export const DEVICES = [
  { key: "android", label: "Android" },
  { key: "ios", label: "iPhone" },
];

// 사파리 버전(아이폰 26부터 OS 버전은 UA 에서 18_6 으로 고정되므로 Version/ 을 먼저 본다).
export function iosVersion(userAgent = "") {
  const safari = /Version\/(\d+)/.exec(userAgent);
  if (safari) return Number(safari[1]);
  const os = /OS (\d+)_\d+/.exec(userAgent);
  return os ? Number(os[1]) : 0;
}

const step = (title, text, hint = null, qr = false) => ({ title, text, hint, qr });

// PC 에서 휴대폰 설치 — QR 을 찍어 휴대폰에서 연 뒤(찍은 휴대폰에는 그 기기에 맞는 시트가 바로 뜬다) 남은 두 단계.
const scanStep = () => step("휴대폰으로 QR 찍기", ["휴대폰 카메라로 찍으면", { br: true }, "껄무새가 열려요."], null, true);

function fromPcSteps(tab) {
  if (tab === "ios") {
    return [
      scanStep(),
      step("⋯ 누르고 공유", ["아래 ", { icon: "dots-h" }, " 를 누르고 ", { b: "공유" }], ["iOS 18 이하: 아래 ", { icon: "share" }, " 공유 버튼"]),
      step("홈 화면에 추가", ["목록에서 ", { icon: "add" }, " ", { b: "홈 화면에 추가" }, " › ", { b: "추가" }],
        [{ b: "웹 앱으로 열기" }, "가 켜진 채로"]),
    ];
  }
  return [
    scanStep(),
    step("⋮ 누르기", ["크롬 오른쪽 위 ", { icon: "dots-v" }, " 를 눌러요."]),
    step("앱 설치", [{ b: "앱 설치" }, " 또는 ", { b: "홈 화면에 추가" }, " › ", { b: "설치" }]),
  ];
}

function inAppSteps(platform) {
  return [
    step("⋮ 누르기", ["화면 구석의 ", { icon: platform === "ios" ? "dots-h" : "dots-v" }, " 를 눌러요."]),
    step("다른 브라우저로 열기", [{ b: "다른 브라우저로 열기" }, "를 눌러요."], ["아이폰: ", { b: "Safari로 열기" }]),
    step("앱 설치", ["열린 브라우저에 이 안내가 다시 나와요."]),
  ];
}

function androidSteps({ browser, onPhone, host }) {
  if (onPhone && browser === "samsung") {
    return [
      step("메뉴", ["아래 ", { icon: "menu" }, " 를 눌러요."]),
      step("현재 페이지 추가", [{ b: "현재 페이지 추가" }, "를 눌러요."]),
      step("홈 화면", [{ b: "홈 화면" }, "을 눌러요."]),
    ];
  }
  return [
    step("⋮ 누르기", ["크롬 오른쪽 위 ", { icon: "dots-v" }, " 를 눌러요."], onPhone ? null : ["휴대폰 크롬에서 ", { b: host }, " 을 먼저 열어요."]),
    step("앱 설치", [{ b: "앱 설치" }, " 또는 ", { b: "홈 화면에 추가" }, "를 눌러요."]),
    step("설치", ["뜨는 창에서 ", { b: "설치" }, "를 눌러요."]),
  ];
}

function iosSteps({ browser, version, onPhone, host }) {
  const opening = onPhone ? null : ["아이폰 사파리에서 ", { b: host }, " 을 먼저 열어요."];
  if (onPhone && browser === "ios-other") {
    return [
      step("공유 버튼", ["주소창의 ", { icon: "share" }, " 를 눌러요."]),
      step("홈 화면에 추가", ["목록에서 ", { icon: "add" }, " ", { b: "홈 화면에 추가" }]),
      step("추가", ["오른쪽 위 ", { b: "추가" }, "를 눌러요."]),
    ];
  }
  if (onPhone && version > 0 && version < 26) {
    return [
      step("공유 버튼", ["아래 ", { icon: "share" }, " 를 눌러요."]),
      step("홈 화면에 추가", ["목록에서 ", { icon: "add" }, " ", { b: "홈 화면에 추가" }]),
      step("추가", ["오른쪽 위 ", { b: "추가" }, "를 눌러요."]),
    ];
  }
  return [
    step("⋯ 누르고 공유", ["아래 ", { icon: "dots-h" }, " 를 누르고 ", { b: "공유" }],
      opening || ["iOS 18 이하: 아래 ", { icon: "share" }, " 공유 버튼"]),
    step("홈 화면에 추가", ["목록에서 ", { icon: "add" }, " ", { b: "홈 화면에 추가" }]),
    step("추가", [{ b: "웹 앱으로 열기" }, "가 켜진 채로 ", { b: "추가" }]),
  ];
}

// 고른 기기(tab)의 안내. 안드로이드에서 브라우저가 설치 창을 띄울 수 있으면(canPrompt) 큰 버튼 하나만(button: "only").
export function installGuide({ tab, platform, browser, version = 0, canPrompt = false, host = "gg-parrot.vercel.app" }) {
  const own = tab === platform;
  if (platform === "desktop") return { kind: tab, button: null, steps: fromPcSteps(tab), note: tab === "ios" ? "push" : "" };
  if (own && browser === "in-app") return { kind: "in-app", button: null, steps: inAppSteps(platform), note: "" };
  if (tab === "android") {
    if (own && canPrompt) return { kind: "android", button: "only", steps: [], note: "" };
    return { kind: "android", button: null, steps: androidSteps({ browser, onPhone: own, host }), note: "" };
  }
  return { kind: "ios", button: null, steps: iosSteps({ browser, version, onPhone: own, host }), note: "push" };
}
