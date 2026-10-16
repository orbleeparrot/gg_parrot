// 거래소 화면 재현 — 업비트 · 빗썸 Open API 관리 화면을 단순화해 그리고 커서가 실제 위치를 누른다.
// 실행기 연결 도우미(runner/exchange_screens.py)와 같은 장면 · 같은 순서다. 웹 가이드는 이것으로 실행기 화면을 따라 보여 준다.
// 근거: docs.upbit.com/kr/docs/api-key · 업비트 실제 화면 · 빗썸 API 2.0 실제 화면(2026-10-12 캡처) ·
// 바이낸스 Spot Test Network · Demo Trading · binance.com API Management 실제 화면(2026-10-15 캡처).
// 그림일 뿐이라 눌러도 아무 일도 없다. 움직임을 줄인 설정이면 마지막 장면만 보인다.
import { useEffect, useRef } from "react";
import "./ExchangeScreen.css";
import { getRunnerDevice } from "../lib/runnerDevice.js";

// 키 복사 단축키 — Mac 실행기에서는 ⌘C.
const COPY_KEYCAP = getRunnerDevice().platform === "macos" ? "⌘C" : "Ctrl+C";

const UP_KEYS = ["a8Kq3Zt0" + "*".repeat(20), "Wm2xP7Lb" + "*".repeat(20)];
const BT_KEYS = ["6b2c78fd" + "*".repeat(20), "ZDkyNGRk" + "*".repeat(20)];
const BN_KEYS = ["0lOHb8TL" + "•".repeat(18), "•".repeat(30)];
const TN_KEYS = ["FdiXVmAJ" + "*".repeat(22), "iRvB4t51" + "*".repeat(22)];
// 바이낸스 키 이름 — 실행기 그림과 같은 글자를 친다.
const KEY_LABEL = "gg_parrot";

function Check() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M5 12.5l4.5 4.5L19 7.5" fill="none" stroke="currentColor" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function Cursor() {
  return (
    <span className="xs-cur" aria-hidden="true">
      <svg viewBox="0 0 24 24" focusable="false"><path d="M5 3l14 8-6 2-3 6z" strokeWidth="1.5" strokeLinejoin="round" /></svg>
    </span>
  );
}

// ── 업비트 — 남색 머리줄 · 네모 체크 · 파란 버튼 ──
function UpChrome() {
  return <div className="xs-upc"><span className="xs-upw">UPbit</span><span>Open API 관리</span></div>;
}
function UpBox({ label, t = "" }) {
  return <span className="xs-upck" data-t={t || undefined}><span className="xs-ubox"><Check /></span>{label}</span>;
}
function UpInput({ placeholder }) {
  return <span className="xs-upin" data-field=""><span className="xs-ph">{placeholder}</span><span className="xs-typed" /></span>;
}

function UpbitScene({ kind }) {
  if (kind === "ip") {
    return (
      <div className="xs-upb"><div className="xs-uprow"><span className="xs-upl">IP 주소 등록</span><UpInput placeholder='IP 주소 입력(","로 구분하여 최대 10개)' /></div></div>
    );
  }
  if (kind === "perm") {
    const items = [["포켓관리", ""], ["자산조회", "t1"], ["주문조회", "t2"], ["주문하기", "t3"], ["출금조회", ""], ["출금하기", ""], ["입금조회", ""], ["입금하기", ""]];
    return (
      <div className="xs-upb">
        <div className="xs-uprow is-top"><span className="xs-upl">권한 선택</span><div className="xs-upgrid">{items.map(([label, t]) => <UpBox key={label} label={label} t={t} />)}</div></div>
        <div className="xs-upagree"><UpBox label="개인(신용)정보 수집 및 이용 동의(필수)" t="t4" /></div>
        <div className="xs-uprobot" data-t="t5"><span className="xs-ubox"><Check /></span>로봇이 아닙니다.</div>
        <span className="xs-upbtn" data-t="t6">Open API Key 발급받기</span>
      </div>
    );
  }
  if (kind === "keys") {
    return (
      <div className="xs-upmodal"><b>Open API Key 발급 완료</b>
        <div className="xs-upkeys">
          <div><span className="xs-kl">Access Key</span><span className="xs-kv">{UP_KEYS[0]}</span><span className="xs-upcopy" data-t="c1">복사</span></div>
          <div><span className="xs-kl">Secret Key</span><span className="xs-kv">{UP_KEYS[1]}</span><span className="xs-upcopy" data-t="c2">복사</span></div>
        </div>
        <span className="xs-upbtn is-active is-small">확인</span>
      </div>
    );
  }
  if (kind === "fix") {
    return (
      <>
        <div className="xs-upb"><div className="xs-uptbl"><span className="xs-ubox" data-t="t1"><Check /></span><span className="xs-kv">a8Kq3Zt0************</span><span className="xs-dots">● ● ●</span><span className="xs-upnavy" data-t="t2">변경</span></div></div>
        <div className="xs-ovl" data-t="t3"><div className="xs-upmini"><b>허용 IP주소 변경</b><UpInput placeholder='","로 구분하여 여러개의 IP주소 등록 가능' />
          <div className="xs-mbtns"><span className="xs-upghost">취소</span><span className="xs-upbtn is-active is-small" data-t="t5">확인</span></div></div></div>
      </>
    );
  }
  return (
    <div className="xs-upb"><div className="xs-uptbl"><span className="xs-kv">a8Kq3Zt0************</span><span className="xs-k">2027.07.01 만료</span><span className="xs-upghost is-small">보기</span></div></div>
  );
}

// ── 빗썸 API 2.0 — 흰 카드 · 주황 원형 체크 · 검정 버튼 ──
function BtChrome({ tabs = true, logo }) {
  return (
    <>
      <div className="xs-btc">{logo ? <img src={logo} alt="" /> : null}<span>API 관리</span></div>
      {tabs ? <div className="xs-bttabs"><span className="is-sel">API 2.0</span><span>API 1.0</span></div> : null}
    </>
  );
}
function BtCheck({ label, t = "", tone = "" }) {
  return <span className={tone === "is-strong" ? "xs-btck is-strong" : tone === "is-sub" ? "xs-btck is-sub" : "xs-btck"} data-t={t || undefined}><span className="xs-bcir"><Check /></span>{label}</span>;
}
function BtIpRow() {
  return <div className="xs-btiprow"><span className="xs-btin" data-field=""><span className="xs-ph">IP 주소를 입력해 주세요. 예) 123.123.123.123</span><span className="xs-typed" /></span><span className="xs-sqb">+</span></div>;
}

function BithumbScene({ kind, logo }) {
  if (kind === "ip") {
    return (
      <>
        <BtChrome logo={logo} />
        <div className="xs-btb"><b className="xs-bth">IP 주소 등록 (필수) <span className="xs-btsm">(최대 5개 입력 가능)</span></b><BtIpRow /></div>
      </>
    );
  }
  if (kind === "perm") {
    const items = [["자산조회", "t1"], ["주문조회", "t2"], ["주문하기", "t3"], ["출금조회", ""], ["출금하기", ""], ["입금조회", ""], ["입금하기", ""]];
    return (
      <>
        <BtChrome logo={logo} />
        <div className="xs-btb">
          <b className="xs-bth">API 활성 항목 선택 (필수)</b>
          <div className="xs-btgrid">{items.map(([label, t]) => <BtCheck key={label} label={label} t={t} />)}</div>
          <div className="xs-btagree">
            <BtCheck label="모두 동의" t="t4" tone="is-strong" />
            <BtCheck label="개인정보 수집 및 이용 동의 (필수)" t="t4" tone="is-sub" />
            <BtCheck label="API 이용 주의사항 확인 및 동의 (필수)" t="t4" tone="is-sub" />
            <span className="xs-btbtn" data-t="t6">API Key 생성</span>
          </div>
        </div>
      </>
    );
  }
  if (kind === "keys") {
    return (
      <>
        <BtChrome tabs={false} logo={logo} />
        <div className="xs-btmodal"><b>API Key가 발급되었습니다.</b>
          <div className="xs-btkeys">
            <div className="xs-kr"><span className="xs-kl">API Key</span><span className="xs-kv">{BT_KEYS[0]}</span><span className="xs-btcopy" data-t="c1">복사</span></div>
            <div className="xs-kr"><span className="xs-kl">Secret key</span><span className="xs-kv">{BT_KEYS[1]}</span><span className="xs-btcopy" data-t="c2">복사</span></div>
          </div>
          <span className="xs-btbtn is-active is-wide">확인</span>
        </div>
      </>
    );
  }
  if (kind === "fix") {
    return (
      <>
        <BtChrome tabs={false} logo={logo} />
        <div className="xs-btb"><b className="xs-bth">API Key 발급 내역</b><div className="xs-bttbl"><span className="xs-kv">xECsmJSx************</span><span className="xs-dots">● ● ●</span><span className="xs-btghost" data-t="t2">변경</span></div></div>
        <div className="xs-ovl" data-t="t3"><div className="xs-btmini"><b>허용 IP주소 변경</b><span className="xs-btsm">IP주소 입력 (최대 5개 입력 가능)</span>
          <BtIpRow />
          <span className="xs-btbtn is-active is-wide" data-t="t5">변경</span></div></div>
      </>
    );
  }
  return (
    <>
      <BtChrome tabs={false} logo={logo} />
      <div className="xs-btb"><b className="xs-bth">API Key 발급 내역</b><div className="xs-bttbl"><span className="xs-kv">xECsmJSx************</span><span className="xs-k">2027-07-09 만료</span></div></div>
    </>
  );
}


// ── 바이낸스 Spot Test Network — 검은 머리줄 · 연회색 바탕 · 파란 링크 · 노란 버튼 ──
function Diamond() {
  return <svg className="xs-bnd" viewBox="0 0 12 12" aria-hidden="true" focusable="false"><path d="M6 0l6 6-6 6-6-6z" /></svg>;
}
function TnChrome() {
  return <div className="xs-tnc"><Diamond /><b>BINANCE</b><span>Spot Test Network</span><span className="xs-tnout">Log out</span></div>;
}
function TnTable() {
  return (
    <div className="xs-tntbl">
      <span className="is-h">Key Type</span><span className="is-h">Description</span><span className="is-h">Permissions</span><span className="is-h" />
      <span>HMAC-SHA-256</span><span>TEST</span><span className="is-sm">TRADE, USER_DATA, USER_STREAM</span><span className="xs-tnbtns"><i>Edit</i><i>Revoke</i></span>
    </div>
  );
}
function TnCheck({ label, on = false, disabled = false }) {
  return <span className={`xs-tnck${on ? " is-on" : ""}${disabled ? " is-off" : ""}`}><span className="xs-tnbox">{on ? <Check /> : null}</span>{label}</span>;
}

function TestnetScene({ kind }) {
  if (kind === "perm") {
    return (
      <>
        <TnChrome />
        <div className="xs-tnb">
          <b className="xs-tnh">API Keys</b>
          <TnTable />
          <span className="xs-tnlink" data-t="gen">Generate HMAC-SHA-256 Key</span>
          <span className="xs-tnlink">Register Public Key</span>
        </div>
        <div className="xs-tnform" data-t="form">
          <b className="xs-tnh">Generate HMAC-SHA-256 key</b>
          <b>Description:</b>
          <span className="xs-tnin" data-field=""><span className="xs-ph" /><span className="xs-typed" /></span>
          <small>Needs to be a unique name containing between 1 and 20 letters, numbers, dashes or underscores.</small>
          <b>Key permissions:</b>
          <div className="xs-tnperms">
            <TnCheck label="TRADE" on /><TnCheck label="USER_DATA" on /><TnCheck label="USER_STREAM" on />
            <TnCheck label="FIX_API" disabled /><TnCheck label="FIX_API_READ_ONLY" disabled />
          </div>
          <span className="xs-tngo" data-t="go">Generate</span>
          <span className="xs-tnlink">Back</span>
        </div>
      </>
    );
  }
  if (kind === "keys") {
    // 이 화면에는 [복사] 가 없다 — 값을 골라 Ctrl+C(Mac ⌘C). 실행기 클립보드 감시가 받는다.
    return (
      <>
        <TnChrome />
        <div className="xs-tnb">
          <b className="xs-tnh">HMAC-SHA-256 Key registered</b>
          <span><b>Save these values right now.</b> They won't be shown ever again!</span>
          {[["API Key:", TN_KEYS[0], "s1", "k1"], ["Secret Key:", TN_KEYS[1], "s2", "k2"]].map(([label, value, sel, cap]) => (
            <span key={sel} className="xs-tnrow">{label} <span className="xs-tnsel" data-t={sel}>{value}</span><span className="xs-keycap" data-t={cap}>{COPY_KEYCAP}</span></span>
          ))}
          <span className="xs-tnlink">Back</span>
        </div>
      </>
    );
  }
  return (
    <>
      <TnChrome />
      <div className="xs-tnb"><b className="xs-tnh">API Keys</b><TnTable /><span className="xs-tnlink">Generate HMAC-SHA-256 Key</span></div>
    </>
  );
}

// ── 바이낸스 Demo Trading · binance.com API Management — 어두운 화면 · 노란 버튼 ──
const LIVE_RESTRICTIONS = ["Enable Reading", "Enable Spot & Margin & Stock Trading", "Enable Margin Loan, Repay & Transfer",
  "Permits Universal Transfer", "Enable Withdrawals", "Enable Alpha Withdrawals", "Enable Prediction Trading", "Enable Symbol Whitelist"];
const DEMO_RESTRICTIONS = ["Enable Reading", "Enable Spot & Margin & Stock Trading", "Enable Futures"];

function BnChrome({ env }) {
  return (
    <div className="xs-bnc"><Diamond /><b>BINANCE</b>
      {env === "demo" ? <><span className="xs-bndemo">DEMO TRADING</span><span>Spot</span><span>Futures</span></>
        : <><span>Buy Crypto</span><span>Markets</span><span>Trade</span><span>Futures</span></>}
    </div>
  );
}
function BnHead({ env, edit = false }) {
  return (
    <div className="xs-bnhead">
      <span className="xs-hmac">HMAC</span><b>{KEY_LABEL}</b>
      <span className="xs-bnlinks">{env === "live" ? <span data-t={edit ? "edit" : undefined}>Edit restrictions</span> : null}<span>Delete</span></span>
      {edit ? <span className="xs-bnedit"><span className="xs-bnsave" data-t="save">Save</span><span className="xs-bncancel">Cancel</span></span> : null}
    </div>
  );
}
function BnRestrictions({ env }) {
  const labels = env === "demo" ? DEMO_RESTRICTIONS : LIVE_RESTRICTIONS;
  return (
    <>
      <span className="xs-bnlab">API restrictions</span>
      <div className="xs-bngrid">
        {labels.map((label, index) => (
          <span key={label} className={`xs-bnck${env === "demo" || index === 0 ? " is-on" : ""}`}
            data-t={env === "live" && label === "Enable Spot & Margin & Stock Trading" ? "trade" : undefined}>
            <span className="xs-bnbox"><Check /></span>{label}
          </span>
        ))}
      </div>
    </>
  );
}
function BnIpAccess({ withInput = false }) {
  return (
    <div className="xs-bnip">
      <b>IP access restrictions</b>
      <span className="xs-bnradio is-first"><i /><span>Unrestricted (Less Secure)</span><em>This API Key allows access from any IP address. This is not recommended.</em></span>
      <em className="xs-bnwarn">To protect the safety of your funds, if the IP is unrestricted and any permission other than Reading is enabled, this API key will be deleted.</em>
      <span className="xs-bnradio" data-t="restrict"><i /><span>Restrict access to trusted IPs only (Recommended)</span></span>
      {/* 'Restrict access…' 를 고르면 나오는 IP 입력 칸(사용자 캡처 2026-10-15) — 노란 테두리 · 안내 글 · 오른쪽 Confirm. */}
      {withInput ? (
        <span className="xs-bnipin" data-t="ipbox">
          <span className="xs-bnipfield" data-field=""><span className="xs-ph">When entering more than one IP address, please separate them with spaces.</span><span className="xs-typed" /></span>
          <span className="xs-bnconfirm" data-t="confirm">Confirm</span>
        </span>
      ) : null}
    </div>
  );
}

function DarkScene({ env, kind }) {
  if (kind === "perm") {
    const buttons = env === "demo" ? ["Create API", "Delete all API"] : ["Create API", "Create Tax Report API", "Delete all API"];
    return (
      <>
        <BnChrome env={env} />
        <div className="xs-bnpage">
          <div className="xs-bntop"><b className="xs-bnh">API Management</b>
            <span className="xs-bnbtns">{buttons.map((label) => <span key={label} className={label === "Create API" ? "is-y" : ""} data-t={label === "Create API" ? "create" : undefined}>{label}</span>)}</span></div>
          <small>1. Each account can create up to 30 API Keys.</small>
          <small>2. Do not disclose your API Key, Secret Key (HMAC) or Private Key (Ed25519, RSA) to anyone.</small>
          <div className="xs-bnnote">{env === "demo"
            ? <><span>Spot Demo API Documentation: https://developers.binance.com/docs/binance-spot-api-docs</span><span>Futures API Documentation: https://developers.binance.com/docs/derivatives</span></>
            : <span className="xs-bndsc"><span className="xs-bnbox is-white"><Check /></span>By checking this box, all existing API Key(s) on your master account and sub-accounts will be subject to Default Security Controls.</span>}</div>
        </div>
        <div className="xs-ovl xs-bnovl" data-t="type">
          <div className="xs-bnmodal is-type"><b>Choose API Key type</b><span className="xs-x">✕</span>
            <span className="xs-bnopt is-sel"><span className="xs-key" /><span><b>System generated</b><small>Works using HMAC symmetric encryption. An API Key and Secret Key will be provided to you.</small></span></span>
            <span className="xs-bnopt"><span className="xs-key" /><span><b>Self-generated</b><small>Works using Ed25519 or RSA asymmetric encryption. You create your own key pair.</small></span></span>
            <span className="xs-bnnext" data-t="next1">Next</span>
          </div>
        </div>
        <div className="xs-ovl xs-bnovl" data-t="label">
          <div className="xs-bnmodal"><b>Create API</b><span className="xs-x">✕</span>
            <small>Label API Key to proceed</small>
            <span className="xs-bnin" data-field=""><span className="xs-ph" /><span className="xs-typed" /></span>
            <span className="xs-bnpair"><span className="xs-bncancel">Cancel</span><span className="xs-bnnext" data-t="next2">Next</span></span>
          </div>
        </div>
      </>
    );
  }
  if (kind === "keys" || kind === "existing") {
    const full = kind === "keys";
    return (
      <>
        <BnChrome env={env} />
        <div className="xs-bncard">
          <BnHead env={env} />
          {full ? <span className="xs-bnwarnbar">ⓘ Make sure to copy your API Key and Secret Key now. They will not be shown in full again after you leave this page.</span> : null}
          {[["API Key", BN_KEYS[0], "c1"], ["Secret Key", BN_KEYS[1], "c2"]].map(([label, value, t]) => (
            <span key={t} className="xs-bnkey"><small>{label}</small><span><b>{value}</b>{full ? <span className="xs-bncopy" data-t={t}>Copy</span> : null}</span></span>
          ))}
          {full ? <BnRestrictions env={env} /> : null}
          {full && env === "live" ? <BnIpAccess /> : null}
        </div>
      </>
    );
  }
  // 실거래 3단계 — 키 카드의 Edit restrictions → Restrict access to trusted IPs only → 주문 권한 → Save.
  return (
    <>
      <BnChrome env={env} />
      <div className="xs-bncard" data-t="card">
        <BnHead env={env} edit />
        <BnRestrictions env={env} />
        <BnIpAccess withInput />
      </div>
    </>
  );
}

// 움직임 순서 — [동작, 대상]. 실행기의 exchange_screens.py 와 같은 순서다.
const UPBIT_SEQ = {
  ip: [["move", "[data-field]"], ["click"], ["focus", "[data-field]"], ["type", "[data-field]"], ["wait", 1600]],
  perm: [["move", '[data-t="t1"]'], ["click"], ["on", '[data-t="t1"]'], ["move", '[data-t="t2"]'], ["click"], ["on", '[data-t="t2"]'], ["move", '[data-t="t3"]'], ["click"], ["on", '[data-t="t3"]'],
    ["move", '[data-t="t4"]'], ["click"], ["on", '[data-t="t4"]'], ["move", '[data-t="t5"]'], ["click"], ["on", '[data-t="t5"]'], ["on", '[data-t="t6"]'], ["move", '[data-t="t6"]'], ["click"], ["wait", 1400]],
  keys: [["move", '[data-t="c1"]'], ["click"], ["on", '[data-t="c1"]'], ["wait", 900], ["move", '[data-t="c2"]'], ["click"], ["on", '[data-t="c2"]'], ["wait", 1800]],
  fix: [["move", '[data-t="t1"]'], ["click"], ["on", '[data-t="t1"]'], ["move", '[data-t="t2"]'], ["click"], ["on", '[data-t="t3"]'], ["move", ".xs-ovl [data-field]"], ["click"], ["focus", ".xs-ovl [data-field]"],
    ["type", ".xs-ovl [data-field]"], ["move", '[data-t="t5"]'], ["click"], ["off", '[data-t="t3"]'], ["wait", 1400]],
};
const BITHUMB_SEQ = {
  ip: UPBIT_SEQ.ip,
  perm: [["move", '[data-t="t1"]'], ["click"], ["on", '[data-t="t1"]'], ["move", '[data-t="t2"]'], ["click"], ["on", '[data-t="t2"]'], ["move", '[data-t="t3"]'], ["click"], ["on", '[data-t="t3"]'],
    ["move", '[data-t="t4"]'], ["click"], ["on", '[data-t="t4"]'], ["on", '[data-t="t6"]'], ["move", '[data-t="t6"]'], ["click"], ["wait", 1400]],
  keys: UPBIT_SEQ.keys,
  fix: [["move", '[data-t="t2"]'], ["click"], ["on", '[data-t="t3"]'], ["move", ".xs-ovl [data-field]"], ["click"], ["focus", ".xs-ovl [data-field]"], ["type", ".xs-ovl [data-field]"],
    ["move", '[data-t="t5"]'], ["click"], ["off", '[data-t="t3"]'], ["wait", 1400]],
};
const TN_SEQ = {
  perm: [["wait", 500], ["move", '[data-t="gen"]'], ["click"], ["on", '[data-t="form"]'], ["move", '[data-t="form"] [data-field]'], ["click"],
    ["focus", '[data-t="form"] [data-field]'], ["type", '[data-t="form"] [data-field]', KEY_LABEL], ["move", '[data-t="go"]'], ["click"],
    ["on", '[data-t="go"]'], ["wait", 1400]],
  keys: [["move", '[data-t="s1"]'], ["click"], ["click"], ["on", '[data-t="s1"]'], ["on", '[data-t="k1"]'], ["wait", 900], ["off", '[data-t="k1"]'],
    ["move", '[data-t="s2"]'], ["click"], ["click"], ["on", '[data-t="s2"]'], ["on", '[data-t="k2"]'], ["wait", 900], ["off", '[data-t="k2"]'], ["wait", 1400]],
};
const BN_PERM_SEQ = [["move", '[data-t="create"]'], ["click"], ["on", '[data-t="type"]'], ["wait", 700], ["move", '[data-t="next1"]'], ["click"],
  ["off", '[data-t="type"]'], ["on", '[data-t="label"]'], ["move", '[data-t="label"] [data-field]'], ["click"], ["focus", '[data-t="label"] [data-field]'],
  ["type", '[data-t="label"] [data-field]', KEY_LABEL], ["move", '[data-t="next2"]'], ["click"], ["on", '[data-t="next2"]'], ["wait", 1400]];
const BN_DEMO_SEQ = { perm: BN_PERM_SEQ, keys: UPBIT_SEQ.keys };
const BN_LIVE_SEQ = {
  perm: BN_PERM_SEQ,
  keys: UPBIT_SEQ.keys,
  restrict: [["move", '[data-t="edit"]'], ["click"], ["on", '[data-t="card"]'], ["wait", 500], ["move", '[data-t="restrict"]'], ["click"],
    ["on", '[data-t="restrict"]'], ["on", '[data-t="ipbox"]'], ["wait", 400], ["move", '[data-t="ipbox"] [data-field]'], ["click"],
    ["focus", '[data-t="ipbox"] [data-field]'], ["type", '[data-t="ipbox"] [data-field]'], ["move", '[data-t="confirm"]'], ["click"],
    ["wait", 500], ["move", '[data-t="trade"]'], ["click"], ["on", '[data-t="trade"]'], ["move", '[data-t="save"]'],
    ["click"], ["wait", 1600]],
};
export const SCREEN_SEQUENCES = { upbit: UPBIT_SEQ, bithumb: BITHUMB_SEQ, "binance:testnet": TN_SEQ, "binance:demo": BN_DEMO_SEQ, "binance:live": BN_LIVE_SEQ };

function reset(demo) {
  demo.querySelectorAll(".on, .focus").forEach((element) => element.classList.remove("on", "focus"));
  demo.querySelectorAll(".xs-typed").forEach((element) => { element.textContent = ""; element.parentElement?.classList.remove("filled"); });
  const cursor = demo.querySelector(".xs-cur");
  if (cursor) {
    cursor.style.transition = "none";
    cursor.style.opacity = "0";
    cursor.style.transform = "translate(70%, 80%)";
  }
}

function finalState(demo, seq, text) {
  for (const [action, selector, fixed] of seq) {
    if (action === "on") demo.querySelectorAll(selector).forEach((element) => element.classList.add("on"));
    else if (action === "off") demo.querySelectorAll(selector).forEach((element) => element.classList.remove("on"));
    else if (action === "type") {
      const field = demo.querySelector(selector);
      const typed = field?.querySelector(".xs-typed");
      const value = fixed || text;
      if (typed && value) { typed.textContent = value; field.classList.add("filled"); }
    }
  }
}

export default function ExchangeScreen({ exchange, kind, typedText = "", logo = "", label = "" }) {
  const ref = useRef(null);
  useEffect(() => {
    const demo = ref.current;
    const seq = SCREEN_SEQUENCES[exchange]?.[kind];
    if (!demo || !seq) return undefined;
    const reduce = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduce) {
      reset(demo);
      finalState(demo, seq, typedText);
      return () => reset(demo);
    }
    let alive = true;
    const timers = new Set();
    const sleep = (ms) => new Promise((resolve) => {
      const id = window.setTimeout(() => { timers.delete(id); resolve(); }, ms);
      timers.add(id);
    });
    const cursor = demo.querySelector(".xs-cur");
    (async () => {
      while (alive) {
        reset(demo);
        await sleep(500);
        if (!alive) return;
        cursor.style.transition = "transform 520ms cubic-bezier(.3,.7,.2,1), opacity 200ms";
        cursor.style.opacity = "1";
        for (const [action, arg, fixed] of seq) {
          if (!alive) return;
          if (action === "move") {
            const target = demo.querySelector(arg);
            if (!target) continue;
            const box = demo.getBoundingClientRect();
            const rect = target.getBoundingClientRect();
            // 입력칸은 오른쪽을 누른다 — 왼쪽부터 찍히는 글자를 가리지 않게.
            const field = target.hasAttribute("data-field");
            const x = Math.min(rect.left - box.left + (field ? Math.max(rect.width * 0.6, 150) : Math.min(26, rect.width / 2)), box.width - 20);
            const y = rect.top - box.top + rect.height / 2;
            cursor.style.opacity = "1";
            cursor.style.transform = `translate(${x}px, ${y}px)`;
            await sleep(640);
          } else if (action === "click") {
            cursor.classList.add("press");
            await sleep(180);
            cursor.classList.remove("press");
            await sleep(140);
          } else if (action === "on") demo.querySelectorAll(arg).forEach((element) => element.classList.add("on"));
          else if (action === "off") demo.querySelectorAll(arg).forEach((element) => element.classList.remove("on"));
          else if (action === "focus") demo.querySelector(arg)?.classList.add("focus");
          else if (action === "type") {
            const field = demo.querySelector(arg);
            const typed = field?.querySelector(".xs-typed");
            // ["type", 칸] 은 가린 주소(typedText), ["type", 칸, 글자] 는 그 글자(바이낸스 키 이름).
            const value = fixed || typedText;
            if (typed && value) {
              field.classList.add("filled");
              for (let index = 1; index <= value.length && alive; index += 1) {
                typed.textContent = value.slice(0, index);
                await sleep(40);
              }
            }
            await sleep(300);
          } else if (action === "wait") {
            cursor.style.opacity = "0";
            await sleep(arg);
          }
        }
      }
    })();
    return () => {
      alive = false;
      timers.forEach((id) => window.clearTimeout(id));
      timers.clear();
    };
  }, [exchange, kind, typedText]);

  const upbit = exchange === "upbit";
  const binanceEnv = exchange.startsWith("binance:") ? exchange.slice("binance:".length) : "";
  const theme = upbit ? "xs-up" : binanceEnv === "testnet" ? "xs-tn" : binanceEnv ? "xs-bn" : "xs-bt";
  let scene;
  if (upbit) scene = <><UpChrome /><UpbitScene kind={kind} /></>;
  else if (binanceEnv === "testnet") scene = <TestnetScene kind={kind} />;
  else if (binanceEnv) scene = <DarkScene env={binanceEnv} kind={kind} />;
  else scene = <BithumbScene kind={kind} logo={logo} />;
  return (
    <div ref={ref} className={`xs ${theme} ${kind === "fix" ? "is-fix" : ""}`.trim()} role="img" aria-label={label || undefined}>
      {scene}
      <Cursor />
    </div>
  );
}
