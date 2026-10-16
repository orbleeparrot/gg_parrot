// 실행기(데스크톱 앱) 설치 안내 — 왼쪽은 설치 순서 애니메이션(실제 화면 캡처), 오른쪽은 단계마다 제목과 할 일 한두 줄.
// 앱 안의 메뉴 이름은 굵게, 보조 정보는 흐리게. 방문자 OS(Windows · Mac)에 맞는 설치 파일을 먼저 준다.
import { Link } from "react-router-dom";
import { RunnerKeyPanel } from "../components/RunnerKeyPanel.jsx";
import RunnerDeviceHandoff from "../components/RunnerDeviceHandoff.jsx";
import { PageHeader } from "../components/Page.jsx";
import { useAuth } from "../lib/auth.js";
import { markRunnerOpened, useRunnerDownload } from "../lib/runnerDownload.js";
import { getRunnerDevice } from "../lib/runnerDevice.js";
import RunnerInstallGuide from "../components/RunnerInstallGuide.jsx";
import "./RunnerInstall.css";

function Heading() {
  return (
    <div className="runner-install-heading">
      <PageHeader title="껄무새 매크로 실행기" />
      <img className="runner-install-mascot" src="/brand/navigation/ggparrot-nav-agent.svg" alt="" width="96" height="96" draggable="false" />
    </div>
  );
}

export default function RunnerInstall() {
  const device = getRunnerDevice();
  if (device.canRunRunner) return <DesktopRunnerInstall />;
  return (
    <div className="runner-install">
      <Heading />
      <div className="runner-install-layout is-device-limited">
        <div className="runner-install-side">
          <RunnerDeviceHandoff installation isMobile={device.isMobile} />
        </div>
        <RunnerInstallGuide platform="windows" />
      </div>
    </div>
  );
}

const B = ({ children }) => <b className="runner-install-ui">{children}</b>;
const Path = ({ items }) => items.map((item, i) => (
  <span key={item}>{i ? <span className="runner-install-sep" aria-hidden="true"> › </span> : null}<B>{item}</B></span>
));

function DesktopRunnerInstall() {
  const { token } = useAuth();
  const download = useRunnerDownload();
  const mac = download.platform === "macos";

  return (
    <div className="runner-install">
      <Heading />
      <div className="runner-install-layout">
        <RunnerInstallGuide platform={download.platform} />
        <div className="runner-install-side">
          <ol className="runner-install-steps">
            <li>
              <span className="runner-install-n" aria-hidden="true">01</span>
              <h2>내려받기</h2>
              <div className="runner-install-act">
                <a href={download.url} target="_blank" rel="noopener noreferrer" className="btn btn-l btn-primary">
                  {download.platformLabel}용 내려받기
                </a>
                {download.otherUrl ? (
                  <a href={download.otherUrl} target="_blank" rel="noopener noreferrer" className="runner-install-alt">{download.otherPlatformLabel}용</a>
                ) : null}
              </div>
              <p className="runner-install-hint">{download.requirement}{download.appVersion ? ` · v${download.appVersion}` : ""}</p>
            </li>
            <li>
              <span className="runner-install-n" aria-hidden="true">02</span>
              <h2>설치</h2>
              {mac ? (
                <>
                  <p>dmg를 열고 <B>껄무새</B>를 Applications로 옮겨요.</p>
                  <p className="runner-install-hint">경고가 뜨면 <Path items={["시스템 설정", "개인정보 보호 및 보안", "그래도 열기"]} /></p>
                </>
              ) : (
                <>
                  <p>설치 파일을 열어 설치해요.</p>
                  <p className="runner-install-hint">‘Windows의 PC 보호’ 창이 뜨면 <B>추가 정보</B> → <B>실행</B></p>
                </>
              )}
            </li>
            <li>
              <span className="runner-install-n" aria-hidden="true">03</span>
              <h2>웹과 연결</h2>
              <p>매크로를 고르고 <B>실행기 열기</B>를 눌러요.</p>
              <p>앱의 <Path items={["설정", "껄무새 계정"]} />에서 <B>이 키 쓰기</B></p>
              <div className="runner-install-act">
                <Link to="/?run=1&step=1" onClick={markRunnerOpened} className="btn btn-l btn-secondary">매크로 고르기</Link>
                {token ? (
                  <div className="runner-install-key"><RunnerKeyPanel key={token} compact /></div>
                ) : (
                  <Link to="/login?next=%2Frunner%2Finstall" className="runner-install-alt">로그인하고 회원 키 복사</Link>
                )}
              </div>
            </li>
            <li>
              <span className="runner-install-n" aria-hidden="true">04</span>
              <h2>거래소 연결</h2>
              <p>앱의 <Path items={["설정", "거래소 연결", "연결 도우미"]} /></p>
              <p className="runner-install-hint">모의 실행은 키 없이 돼요.</p>
            </li>
          </ol>
          <p className="runner-install-foot">
            거래소 키는 이 PC에만 저장되고 서버로 보내지 않아요.<br />
            예전 실행기(ggparrot-runner.exe)는 지워 주세요.
          </p>
        </div>
      </div>
    </div>
  );
}
