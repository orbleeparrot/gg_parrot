// 실행기 설치 안내 애니메이션 — 실제 화면 캡처 위로 커서가 움직이며 순서를 보여 준다.
// Windows: 다운로드 폴더의 설치 파일 열기 → ‘Windows의 PC 보호’ 추가 정보 → 실행 → 설치 마침 → 실행기 창.
// Mac: dmg 창에서 껄무새를 Applications 로 끌어다 놓기 → 실행기 창.
// 움직임 줄이기 설정이면 마지막 장면(실행기 창)만 멈춰 보여 준다.
import winDownloads from "../assets/runner-install/win-1-downloads.png";
import winMoreInfo from "../assets/runner-install/win-2-more-info.png";
import winRun from "../assets/runner-install/win-3-run.png";
import winFinish from "../assets/runner-install/win-4-finish.png";
import macDmg from "../assets/runner-install/mac-1-dmg.jpg";
import macIcon from "../assets/runner-install/mac-app-icon.png";
import appRunning from "../assets/runner-install/app-running.jpg";
import "./RunnerInstallGuide.css";

const WINDOWS = [
  ["rig-w1", winDownloads, 660, 591],
  ["rig-w2", winMoreInfo, 538, 498],
  ["rig-w3", winRun, 528, 497],
  ["rig-w4", winFinish, 622, 486],
  ["rig-w5", appRunning, 880, 609],
];
const MAC = [
  ["rig-m1", macDmg, 810, 570],
  ["rig-m2", appRunning, 880, 609],
];

function Cursor({ drag = false }) {
  return (
    <span className="rig-track" aria-hidden="true">
      {drag ? <img className="rig-ghost" src={macIcon} alt="" width="156" height="156" draggable="false" /> : null}
      <span className="rig-ring" />
      <svg className="rig-cursor" viewBox="0 0 24 24" width="24" height="24">
        <path d="M4 2.5v17.2l4.6-4.4 2.9 6.6 2.7-1.2-2.9-6.5h6.4z" />
      </svg>
    </span>
  );
}

export default function RunnerInstallGuide({ platform = "windows" }) {
  const mac = platform === "macos";
  const scenes = mac ? MAC : WINDOWS;
  const label = mac
    ? "내려받은 dmg 창에서 껄무새를 Applications 폴더로 끌어다 놓고, 실행기 창이 열리는 안내 애니메이션"
    : "다운로드 폴더에서 설치 파일을 열고, ‘Windows의 PC 보호’ 창에서 추가 정보와 실행을 누른 뒤, 설치를 마치고 실행기 창이 열리는 안내 애니메이션";
  return (
    <figure className={`rig ${mac ? "is-mac" : "is-windows"}`} role="img" aria-label={label}>
      <div className="rig-stage">
        {scenes.map(([cls, src, w, h]) => (
          <img key={cls} className={`rig-scene ${cls}`} src={src} alt="" width={w} height={h} draggable="false" decoding="async" />
        ))}
        <Cursor drag={mac} />
      </div>
    </figure>
  );
}
