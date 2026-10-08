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
// 사용 설명(Guide.jsx 의 domestic-api 쪽)도 같은 주소를 쓴다 — 두 벌로 두면 한쪽만 바뀐다.
export const DOMESTIC_KEY_PAGES = {
  upbit: {
    url: "https://upbit.com/mypage/open_api_management",
    domain: "upbit.com",
    linkLabel: "업비트 Open API 관리 열기",
    helpUrl: "https://docs.upbit.com/kr/docs/api-key",
    verifiedOn: "2026-10-09",
    maxIpv4: 10,
  },
  bithumb: {
    url: "https://www.bithumb.com/react/api-support/management-api",
    domain: "bithumb.com",
    linkLabel: "빗썸 API 관리 열기",
    helpUrl: "https://support.bithumb.com/hc/ko/articles/52815899880345",
    verifiedOn: "2026-10-09",
    maxIpv4: 5,
  },
};

export const RUNNER_KEY_STORAGE_NOTE = "거래소 키는 껄무새 웹·서버로 보내지 않아요. ‘이 PC에 키 기억하기’를 선택한 경우에만 Windows 계정으로 암호화해 로컬에 저장해요. 기본은 꺼져 있어요.";
export const DOMESTIC_IP_LINE = "실거래에는 실행기를 돌릴 PC의 공인 IPv4를 키의 허용 IP로 등록해야 해요.";

// 주소는 브라우저나 휴대폰이 아니라 주문을 보내는 실행기 PC의 외부 IPv4이다.
// 자동 확인은 주소를 고정하지 않는다. 미래의 유동 IP를 미리 알거나 등록할 수도 없다.
// 공개 연결 도우미 / 빠른 실행 마법사의 공통 단계. 비밀값을 받거나 저장하지 않는다.
// 호출마다 새 배열과 객체를 반환하여 화면의 상태 변경이 다른 사용자 안내에 섞이지 않게 한다.
export function domesticConnectionSteps(exchange) {
  let normalized;
  try { normalized = normalizeExchange(exchange); } catch (_) { return []; }
  if (!isDomestic(normalized)) return [];
  const name = exchangeLabel(normalized);
  const page = DOMESTIC_KEY_PAGES[normalized];
  const upbit = normalized === "upbit";
  return [
    {
      id: "prepare",
      title: "실행기에서 PC의 공인 IP 확인하기",
      description: "Windows PC에 실행기를 준비하고, 새 실행기에 ‘거래소 연결 도우미’가 보이면 열어요. ‘공인 IPv4 확인’ → ‘IPv4 복사’로 실행기 PC의 주소를 준비해요. 연습만 한다면 키 없이 모의 모드를 사용할 수 있어요.",
      checklist: [
        "IP 확인은 실행기 PC에서 외부 서비스 ipify(api4.ipify.org)에 접속해요. 거래소 키는 보내지 않아요. 이 웹·휴대폰·Render 서버의 주소가 아니에요.",
        "자동 조회는 고정 IP를 만들거나 거래소 허용 IP에 등록하지 않아요. 등록은 다음 단계에서 공식 관리 화면에 직접 해요. 사설 IP(192.168.… 또는 10.…)를 넣지 않아요.",
        "조회는 현재 주소가 고정 IP인지 판정하지 않아요. 업비트가 안내하는 고정 IP 환경은 별도로 준비해야 해요. 유동 주소는 바뀔 수 있고, VPN·프록시에서 거래소 연결 경로가 다르면 실제 주문 주소가 다를 수 있어 실행기 인증 검사로 확인해야 해요.",
        "현재 배포된 v10 등 도우미가 없는 기존 실행기는 실행할 Windows PC에서 공인 IPv4를 직접 확인해요. 새 버튼이 없다고 키를 웹에 입력하지 마세요.",
      ],
    },
    {
      id: "permissions",
      title: "공식 거래소에서 권한·IP를 등록하고 발급하기",
      description: `${name} PC 웹의 API 관리 화면에서 자산조회 · 주문조회 · 주문하기를 선택하고 출금하기는 꺼 둬요. 실행기에서 확인한 IPv4를 허용 IP에 직접 등록한 다음 동의와 발급 인증을 마쳐요. 최대 ${page.maxIpv4}개까지 등록할 수 있어요.`,
      checklist: [
        upbit
          ? "고객 확인(KYC)과 2채널 인증을 마친 계정으로 마이페이지 → Open API 관리에 들어가요. 업비트 공식 QR 로그인은 PC 로그인 수단이지 API 키 전송이나 껄무새 매매 승인 기능이 아니에요. 발급 인증은 별도로 완료해요."
          : "PC 웹의 메뉴 → 계정관리 → API 관리에서 API 2.0 안내에 따라 발급해요. 점유 인증을 마치면 발급된 키는 즉시 활성화돼요.",
        "자산조회는 잔고, 주문조회는 체결 상태, 주문하기는 실제 매매에 사용해요.",
        "출금 권한을 꺼도 유출된 키로 매매 손실이 날 수 있어요. 키를 공유하지 마세요.",
        ...(upbit ? [] : ["빗썸은 발급 후 권한을 바꾸려면 키를 새로 만들어야 해요."]),
      ],
      action: { href: page.url, label: page.linkLabel },
    },
    {
      id: "keys",
      title: "발급한 키로 실행기에서 검사하기",
      description: "발급한 두 키는 실행기에만 입력하고 새 실행기의 ‘연결 검사’로 확인해요. 웹 안내를 확인했다고 거래소 인증이 완료된 것은 아니에요. 실거래 시작은 검사와 별도로 결정해요.",
      checklist: [
        "Secret Key는 최초 발급 때만 보여요. 비밀번호 관리자에 보관하고 메신저·스크린샷에 남기지 않아요.",
        "새 실행기에 ‘연결 검사’ 버튼이 보이면, 매크로를 열고 API 키·시크릿을 입력한 뒤 눌러요. 이 검사는 실제 주문을 보내지 않아요.",
        upbit
          ? "업비트는 실제 주문을 생성하지 않는 주문 테스트로 확인해요. 실패 안내에 따라 권한·IP·잔고를 고쳐요."
          : "빗썸은 읽기 요청으로 키와 조회 연결을 확인해요. 검사 성공만으로 주문 권한까지 확인되지 않아요.",
        ...(upbit ? [] : ["빗썸 키는 발급 후 1년이 지나면 연장하지 못하므로 새로 발급해요."]),
        RUNNER_KEY_STORAGE_NOTE,
        "버튼이 없는 기존 실행기는 시작 시 표시되는 기존 연결 검사 안내를 따르세요. ‘매크로 시작’은 검사 전용 버튼이 아니므로 실거래에서는 실제 주문이 나갈 수 있어요. 먼저 모의 모드로 확인하세요.",
      ],
    },
  ];
}

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
  const connectionSteps = domesticConnectionSteps(exchange);
  return {
    exchange,
    domestic: true,
    exchangeName: name,
    market: "원화 현물",
    environment: `${name} Open API`,
    url: page.url,
    domain: page.domain,
    linkLabel: page.linkLabel,
    steps: connectionSteps.map((step) => [step.title, step.description]),
    storageKey: `${DOMESTIC_KEY_GUIDE_STORAGE_PREFIX}:${exchange}`,
    workspaceTitle: `${name} 연결 안내`,
    workspaceStatus: "모의 모드는 키 없이 가능",
    introTitle: `${name} API 연결은 실행기에서 마쳐요.`,
    introBody: `모의 모드는 주문을 보내지 않고 키 없이도 돌아요. 실거래로 바꿀 때만 ${name} API 키가 필요하고, 키마다 실행기 PC의 공인 IPv4를 허용 IP로 등록해야 해요.`,
    stepsLabel: `${name} 실행기 연결 순서`,
    checkTitle: "안내를 확인했어요. 실제 키 검사는 실행기에서 할게요.",
    checkNote: "이 확인은 거래소 인증 성공을 뜻하지 않아요. 모의 모드에는 키가 필요 없고 실거래 키는 실행기의 연결 검사로 확인해요.",
    pickDescription: "내 계정의 매크로나 리더보드 전략을 고르면 실행기를 준비하고 모의 모드로 시작할 수 있어요. 실거래 키 연결은 실행기에서 마쳐요.",
    keyDescription: `${name}에는 테스트넷이 없어요. 지금은 연결 방법만 보고 실행기부터 준비하세요. 실거래 때 PC의 IP를 확인하고 공식 발급 화면과 실행기를 이어서 사용해요.`,
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
