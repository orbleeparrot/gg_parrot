// 설정 › 앱 알림 — 이 기기에서 내 에이전트 알림(껄무새 중계 전부: 매매 · 손익 · 시세 · 뉴스)을 켜고 끈다.
// 줄 모양은 '계정 및 보안' 탭과 같다(괘선으로 나눈 줄, 오른쪽에 조작). 상태마다 오른쪽 조작과 아래 한 줄만 바뀐다.
import { useMemo, useState } from "react";
import AppToast from "./AppToast.jsx";
import { enableToast, unblockHint, usePushState } from "../hooks/usePushState.js";
import { openAppInstall } from "../lib/installDialog.js";
import { currentDevice } from "../lib/pwa.js";

const PLATFORM_LABELS = { android: "Android", ios: "iPhone", desktop: "PC" };
const BROWSER_LABELS = {
  chrome: "크롬", edge: "엣지", safari: "사파리", samsung: "삼성 인터넷", firefox: "파이어폭스",
  "ios-other": "브라우저", "in-app": "앱 안 브라우저", other: "브라우저",
};

function Path({ items }) {
  return items.map((item, index) => (
    <span key={item}>{index ? <span aria-hidden="true"> › </span> : null}<b>{item}</b></span>
  ));
}

export default function AppAlertsSettings() {
  const push = usePushState();
  const device = useMemo(() => currentDevice(), []);
  const [toast, setToast] = useState(null);
  const on = push.state === "on";
  const deviceLabel = device.standalone
    ? `${PLATFORM_LABELS[device.platform]} · 앱`
    : `${PLATFORM_LABELS[device.platform]} · ${BROWSER_LABELS[device.browser] || "브라우저"}`;

  function toggle() {
    if (on) {
      push.disable().then(() => setToast({ tone: "done", label: "알림", title: "앱 알림을 껐어요" }));
    } else {
      push.enable().then((result) => setToast(enableToast(result)));
    }
  }

  function sendTest() {
    push.test()
      .then((result) => setToast({ tone: "done", label: "알림", title: "시험 알림을 보냈어요", body: `기기 ${result?.devices ?? 1}대` }))
      .catch((error) => setToast({ tone: "error", label: "실패", title: String(error?.message || "시험 알림을 보내지 못했어요.") }));
  }

  if (push.state === "desktop") {
    return (
      <div className="profile-settings-security-row">
        <div className="app-alerts-label">
          <h3>PC 알림</h3>
          <p className="app-alerts-hint">PC 는 <Path items={["실행기", "설정", "알림"]} />에서 받아요</p>
        </div>
      </div>
    );
  }

  let control = null;
  let hint = null;
  if (push.state === "loading") {
    hint = "불러오는 중…";
  } else if (push.state === "on" || push.state === "off") {
    control = (
      <button type="button" role="switch" aria-checked={on} aria-label="내 에이전트 알림" className="app-alerts-switch"
        disabled={push.busy} onClick={toggle}>
        <span aria-hidden="true" />
      </button>
    );
  } else if (push.state === "denied") {
    control = <span className="badge app-alerts-blocked">차단됨</span>;
    hint = <Path items={unblockHint(device.platform)} />;
  } else if (push.state === "needs-install" || push.state === "in-app") {
    control = <button type="button" className="btn btn-m btn-secondary" onClick={openAppInstall}>앱 설치</button>;
    hint = push.state === "in-app" ? "앱 안 브라우저에서는 켤 수 없어요" : "홈 화면에 추가한 껄무새에서 켤 수 있어요";
  } else if (push.state === "unconfigured") {
    hint = "지금은 앱 알림을 켤 수 없어요";
  } else {
    hint = "이 브라우저는 앱 알림을 지원하지 않아요";
  }

  return (
    <div>
      <div className="profile-settings-security-row">
        <div className="app-alerts-label">
          <h3>내 에이전트 알림</h3>
          <span className="app-alerts-chips">
            <span className="badge badge-flat">이 기기</span>
            <span className="badge badge-flat">{deviceLabel}</span>
            <span className="badge badge-flat">매매 · 손익 · 시세 · 뉴스</span>
          </span>
          {hint ? <p className="app-alerts-hint">{hint}</p> : null}
        </div>
        {control}
      </div>
      {on ? (
        <div className="profile-settings-security-row">
          <h3>시험 알림</h3>
          <button type="button" className="btn btn-m btn-secondary" disabled={push.busy} onClick={sendTest}>보내기</button>
        </div>
      ) : null}
      {toast ? <AppToast key={toast.title} {...toast} onClose={() => setToast(null)} /> : null}
    </div>
  );
}
