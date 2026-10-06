// 빠른 실행 마법사의 거래소별 안내 — 어느 거래소의 키를 준비하고, 연습은 무엇으로 하는지.
// 바이낸스에는 테스트넷이 있고 업비트·빗썸에는 없다(연습은 실행기의 모의 모드, 키 없이도 돈다).
// 그래서 국내 매크로에 '테스트넷 키를 준비하세요' 를 보이면 사용자에게 불가능한 일을 시키게 된다.
// 화면(RunnerDownload.jsx)은 이 모듈이 돌려주는 글만 그대로 보여 준다.
import { exchangeLabel, isDomestic, normalizeExchange } from "./exchanges.js";

const BINANCE_KEY_GUIDE_STORAGE_PREFIX = "ggparrot:binance-testnet-key-ready:v1";
const DOMESTIC_KEY_GUIDE_STORAGE_PREFIX = "ggparrot:domestic-key-ready:v1";

export const BINANCE_TESTNET_GUIDES = {
  spot: {
    market: "현물",
    environment: "Spot Testnet",
    url: "https://testnet.binance.vision/",
    domain: "testnet.binance.vision",
    linkLabel: "Spot Testnet 열기",
    steps: [
      ["GitHub 계정으로 로그인", "Log In with GitHub을 누르고 binance-exchange 접근을 허용해요."],
      ["HMAC 키 만들기", "Generate HMAC_SHA256 Key를 선택해 API Key와 Secret Key를 만들어요."],
      ["거래 권한 확인", "TRADE 권한이 켜져 있는지 확인해요. 출금 권한은 빠른 실행에 필요하지 않아요."],
      ["두 키를 바로 보관", "Secret Key는 다시 보이지 않으니 비밀번호 관리자에 임시 보관해요. 메신저나 스크린샷에는 남기지 않아요."],
    ],
  },
  futures: {
    market: "선물",
    environment: "Futures Demo",
    url: "https://demo.binance.com/en/my/settings/api-management",
    domain: "demo.binance.com",
    linkLabel: "Futures Demo API 만들기",
    steps: [
      ["데모 계정으로 로그인", "로그인 뒤 Futures Demo의 API Management 화면으로 돌아와요."],
      ["API Management에서 키 만들기", "API Management → Create API를 누르고 알아보기 쉬운 이름을 입력해요."],
      ["선물 거래 권한 확인", "Demo Futures 거래 권한이 켜져 있는지 확인해요. 출금 권한은 필요하지 않아요."],
      ["두 키를 바로 보관", "API Key와 Secret Key는 비밀번호 관리자에 임시 보관해요. 메신저나 스크린샷에는 남기지 않아요."],
    ],
  },
};

// 업비트·빗썸 공식 키 발급 화면. 두 곳 모두 호출하는 IP 를 키에 등록해야 주문이 통과한다.
const DOMESTIC_KEY_PAGES = {
  upbit: { url: "https://upbit.com/mypage/open_api_management", domain: "upbit.com", linkLabel: "업비트 Open API 관리 열기" },
  bithumb: { url: "https://www.bithumb.com/react/api-support/management-api", domain: "bithumb.com", linkLabel: "빗썸 API 관리 열기" },
};

// 집 인터넷의 공인 IP 는 바뀐다 — 처음 한 번 등록해 두면 끝나는 일이 아니라는 점을 같이 말한다.
// 업비트는 키 하나에 허용 IP 를 10개까지 둘 수 있다(빗썸은 한도를 적지 않는다).
const IP_NOTE_UPBIT = "이 PC의 공인 IP를 키의 허용 IP에 등록해요. 키 하나에 10개까지 넣을 수 있어요. 집 인터넷은 IP가 바뀔 수 있으니, 주문이 거절되면 새 IP를 다시 등록해요.";
const IP_NOTE_BITHUMB = "이 PC의 공인 IP를 키의 허용 IP에 등록해요. 집 인터넷은 IP가 바뀔 수 있으니, 주문이 거절되면 새 IP를 다시 등록해요.";
export const DOMESTIC_IP_LINE = "실거래에는 키에 이 PC의 IP를 허용 IP로 등록해야 해요.";

function safeExchange(macro) {
  try { return normalizeExchange(macro?.exchange); } catch (_) { return "binance"; }
}

// 실행기 쪽 시장: 숏이거나 레버리지가 있으면 선물, 아니면 현물.
export function runnerExecutionMarket(macro = {}) {
  const leverage = Number(macro?.leverage || 1);
  return macro?.position_side === "short" || leverage > 1 ? "futures" : "spot";
}

function binanceGuide(macro) {
  const market = runnerExecutionMarket(macro);
  const base = BINANCE_TESTNET_GUIDES[market];
  return {
    ...base,
    exchange: "binance",
    domestic: false,
    exchangeName: "바이낸스",
    storageKey: `${BINANCE_KEY_GUIDE_STORAGE_PREFIX}:${market}`,
    workspaceTitle: `Binance ${base.environment}`,
    workspaceStatus: "테스트넷 · 가짜 자금",
    introTitle: `${base.market} 테스트넷 키가 필요해요.`,
    introBody: "실거래 키가 아니라 연습용 키를 만들어요. 현물과 선물 키는 서로 바꿔 쓸 수 없어요.",
    stepsLabel: `${base.market} 테스트넷 API 키 발급 순서`,
    checkTitle: `${base.market} 테스트넷의 API Key와 Secret Key를 준비했어요.`,
    checkNote: "실제 키 유효성은 실행기에서 연결할 때 확인해요.",
    pickDescription: "내 계정의 매크로나 리더보드 전략을 고르면 테스트넷 준비부터 실행기 연결과 실행 확인까지 한 화면씩 이어져요.",
    keyDescription: "처음이라면 실제 돈이 들지 않는 테스트넷부터 시작해요. 선택한 매크로에 맞는 공식 발급 화면과 순서를 바로 안내해 드려요.",
    runnerDescription: "껄무새 실행기는 내 PC에서 주문을 처리해요. 바이낸스 키는 웹이나 껄무새 서버로 보내지 않아요.",
    modeShort: "테스트넷 기본",
    modeLong: "테스트넷 · 가짜 자금",
    launchKeyTitle: `앞에서 준비한 ${base.market} 테스트넷 키를 실행기에 입력해요.`,
    launchKeyNote: "실행기 로그에 ‘연결 성공’이 나타나야 거래소 인증까지 끝난 거예요.",
    accountStartText: "실행기에 바이낸스 테스트넷 API 키/시크릿을 입력하고 매크로 시작을 눌러요.",
  };
}

function domesticGuide(exchange) {
  const name = exchangeLabel(exchange);
  const page = DOMESTIC_KEY_PAGES[exchange];
  const ipNote = exchange === "upbit" ? IP_NOTE_UPBIT : IP_NOTE_BITHUMB;
  return {
    exchange,
    domestic: true,
    exchangeName: name,
    market: "원화 현물",
    environment: `${name} Open API`,
    url: page.url,
    domain: page.domain,
    linkLabel: page.linkLabel,
    steps: [
      ["API 키 만들기", `${name}에 로그인해 API 관리 화면에서 새 키를 만들어요.`],
      ["권한 확인", "자산 조회와 주문 권한만 켜요. 출금 권한은 빠른 실행에 필요하지 않아요."],
      ["허용 IP 등록", ipNote],
      ["두 키를 바로 보관", "Secret Key는 다시 확인하기 어려우니 비밀번호 관리자에 임시 보관해요. 메신저나 스크린샷에는 남기지 않아요."],
    ],
    storageKey: `${DOMESTIC_KEY_GUIDE_STORAGE_PREFIX}:${exchange}`,
    workspaceTitle: `${name} API 키`,
    workspaceStatus: "모의 모드는 키 없이 가능",
    introTitle: `${name}에는 테스트넷이 없어요. 연습은 모의 모드로 해요.`,
    introBody: `모의 모드는 주문을 보내지 않고 키 없이도 돌아요. 실거래로 바꿀 때만 ${name} API 키가 필요하고, 키마다 이 PC의 IP를 허용 IP로 등록해야 해요.`,
    stepsLabel: `${name} API 키 발급 순서`,
    checkTitle: `${name} 키를 준비했거나, 모의 모드부터 해 볼게요.`,
    checkNote: "모의 모드에는 키가 필요 없어요. 실거래 키는 실행기에서 연결할 때 확인해요.",
    pickDescription: "내 계정의 매크로나 리더보드 전략을 고르면 키 준비부터 실행기 연결과 실행 확인까지 한 화면씩 이어져요.",
    keyDescription: `${name}에는 테스트넷이 없어서 연습은 실행기의 모의 모드로 해요. 실거래 때 쓸 키와 허용 IP 등록 순서를 안내해 드려요.`,
    runnerDescription: `껄무새 실행기는 내 PC에서 주문을 처리해요. ${name} 키는 웹이나 껄무새 서버로 보내지 않아요.`,
    modeShort: "모의 기본",
    modeLong: "모의 · 주문을 보내지 않아요",
    launchKeyTitle: "실행기의 실행 모드가 ‘모의’인지 확인해요.",
    launchKeyNote: `모의는 키 없이 주문 없이 돌아요. 실거래로 바꿀 때만 ${name} 키를 입력하고, ${DOMESTIC_IP_LINE}`,
    accountStartText: `실행 모드가 모의인지 확인하고 매크로 시작을 눌러요. 실거래로 바꿀 때만 ${name} API 키/시크릿을 입력하고, ${DOMESTIC_IP_LINE}`,
  };
}

// '자동 연결 최소 버전' 칸에 보일 숫자. 국내 매크로는 국내 요구 버전이고, 모르면 칸을 비운다(바이낸스 숫자를 대신 보이지 않는다).
export function launchMinVersionFor(guide, { general = "", domestic = "" } = {}) {
  return guide?.domestic ? String(domestic || "") : String(general || "");
}

export function runnerKeyGuide(macro) {
  const exchange = safeExchange(macro);
  return isDomestic(exchange) ? domesticGuide(exchange) : binanceGuide(macro);
}
