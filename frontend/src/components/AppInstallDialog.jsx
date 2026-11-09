// 앱(PWA) 설치 안내 — 지금 보던 화면 위에 넓은 화면은 가운데 대화상자, 휴대폰은 아래 시트(시안 B안, 11/06 확정).
// 휴대폰만 — PC 는 데스크톱 실행기가 앱 · 알림을 맡는다. 안드로이드는 브라우저가 설치 창을 띄울 수 있으면 '바로 설치' 버튼 하나.
// 안내 내용은 lib/pwaGuide.js 가 정하고 여기서는 그리기만 한다.
import { useId, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { BellIcon } from "@phosphor-icons/react/dist/csr/Bell";
import { CheckCircleIcon } from "@phosphor-icons/react/dist/csr/CheckCircle";
import { DotsThreeIcon } from "@phosphor-icons/react/dist/csr/DotsThree";
import { DotsThreeVerticalIcon } from "@phosphor-icons/react/dist/csr/DotsThreeVertical";
import { DownloadSimpleIcon } from "@phosphor-icons/react/dist/csr/DownloadSimple";
import { ExportIcon } from "@phosphor-icons/react/dist/csr/Export";
import { ListIcon } from "@phosphor-icons/react/dist/csr/List";
import { PlusSquareIcon } from "@phosphor-icons/react/dist/csr/PlusSquare";
import { XIcon } from "@phosphor-icons/react/dist/csr/X";
import { useModalLayer } from "../hooks/useModalLayer.js";
import { closeAppInstall, useAppInstallOpen } from "../lib/installDialog.js";
import { currentDevice, installState, promptInstall, subscribeInstall } from "../lib/pwa.js";
import { DEVICES, installGuide, iosVersion } from "../lib/pwaGuide.js";
import "./AppInstallDialog.css";

const GLYPHS = { "dots-v": DotsThreeVerticalIcon, "dots-h": DotsThreeIcon, share: ExportIcon, add: PlusSquareIcon, menu: ListIcon };
const GLYPH_LABELS = { "dots-v": "⋮", "dots-h": "⋯", share: "공유", add: "홈 화면에 추가", menu: "메뉴" };

function Parts({ parts }) {
  return parts.map((part, index) => {
    if (typeof part === "string") return part;
    if (part.b) return <b key={index} className="app-install-ui">{part.b}</b>;
    const Glyph = GLYPHS[part.icon];
    return (
      <span key={index} className="app-install-key" role="img" aria-label={GLYPH_LABELS[part.icon] || ""}>
        {Glyph ? <Glyph size={18} weight="bold" aria-hidden="true" /> : null}
      </span>
    );
  });
}

export default function AppInstallDialog() {
  const open = useAppInstallOpen();
  return open ? <InstallSheet onClose={closeAppInstall} /> : null;
}

function InstallSheet({ onClose }) {
  const device = useMemo(() => currentDevice(), []);
  const version = useMemo(() => iosVersion(globalThis.navigator?.userAgent || ""), []);
  const install = useSyncExternalStore(subscribeInstall, installState, installState);
  const [tab, setTab] = useState(device.platform === "ios" ? "ios" : "android");
  const [others, setOthers] = useState(device.platform === "desktop");
  const [busy, setBusy] = useState(false);
  const ref = useRef(null);
  const titleId = useId();
  useModalLayer({ open: true, ref, onEscape: onClose, busy, trap: true });
  const guide = installGuide({
    tab, platform: device.platform, browser: device.browser, version,
    canPrompt: install.canPrompt, host: globalThis.location?.host || "gg-parrot.vercel.app",
  });

  async function installNow() {
    setBusy(true);
    try {
      await promptInstall(); // 설치하면 appinstalled 가 '설치했어요'로, 닫으면 버튼 대신 3단계가 남는다
    } finally {
      setBusy(false);
    }
  }

  return createPortal(
    <div
      className="scrim fixed inset-0 z-90 grid place-items-center p-4 app-install-scrim"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onClose();
      }}
    >
      <div ref={ref} role="dialog" aria-modal="true" aria-labelledby={titleId} className="dialog app-install-dialog">
        <span className="app-install-handle" aria-hidden="true" />
        <div className="app-install-head">
          <img src="/favicon/android-icon-192x192.png" alt="" width="44" height="44" draggable="false" />
          <h2 id={titleId}>껄무새 앱 설치</h2>
          <button type="button" className="app-install-close" aria-label="닫기" onClick={onClose}>
            <XIcon size={20} weight="bold" aria-hidden="true" />
          </button>
        </div>
        {install.installed ? (
          <div className="app-install-done" role="status">
            <CheckCircleIcon size={40} weight="fill" aria-hidden="true" />
            <b>설치했어요</b>
            <span>홈 화면의 껄무새로 열어 주세요.</span>
          </div>
        ) : (
          <>
            {others ? (
              <div role="group" aria-label="기기" className="app-install-devices">
                {DEVICES.map((item) => (
                  <button key={item.key} type="button" aria-pressed={tab === item.key} onClick={() => setTab(item.key)}>{item.label}</button>
                ))}
              </div>
            ) : null}
            {guide.button === "only" ? (
              <button type="button" className="btn btn-xl btn-primary app-install-now" disabled={busy} onClick={installNow}>
                <DownloadSimpleIcon size={22} weight="bold" aria-hidden="true" />바로 설치
              </button>
            ) : null}
            {guide.steps.length ? (
              <ol className="app-install-steps">
                {guide.steps.map((item, index) => (
                  <li key={item.title}>
                    <span className="app-install-n" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
                    <h3>{item.title}</h3>
                    <p><Parts parts={item.text} /></p>
                    {item.hint ? <p className="app-install-hint"><Parts parts={item.hint} /></p> : null}
                  </li>
                ))}
              </ol>
            ) : null}
            {guide.note === "push" ? (
              <p className="app-install-foot"><BellIcon size={16} weight="bold" aria-hidden="true" />알림은 홈 화면의 껄무새에서 켜요</p>
            ) : null}
            {!others ? <button type="button" className="app-install-others" onClick={() => setOthers(true)}>다른 기기 설치 방법</button> : null}
          </>
        )}
      </div>
    </div>,
    document.body,
  );
}
