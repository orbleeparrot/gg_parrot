// 설치 안내는 애니메이션을 먼저 보여 주고, 필요한 행동만 옆에 모은다.
import { useState } from "react";
import { Link } from "react-router-dom";
import { RunnerKeyPanel } from "../components/RunnerSessions.jsx";
import RunnerLaunchGuide from "../components/RunnerLaunchGuide.jsx";
import { PageHeader } from "../components/Page.jsx";
import { useAuth } from "../lib/auth.js";
import { fmtSize, isRunnerOpened, markRunnerOpened, useRunnerDownload } from "../lib/runnerDownload.js";
import "./RunnerInstall.css";

export default function RunnerInstall() {
  const { token } = useAuth();
  const download = useRunnerDownload();
  const [opened] = useState(isRunnerOpened);
  const downloadMeta = [
    "Windows 10 이상",
    download.available && download.version ? `v${download.version}` : "",
    download.available && download.size ? fmtSize(download.size) : "",
  ].filter(Boolean).join(" · ");

  return (
    <div className="runner-install">
      {/* 제목 조판은 다른 화면과 같은 규격 — 공용 PageHeader(§9) */}
      <PageHeader title="껄무새 매크로 실행기" />

      <div className="runner-install-layout">
        <div className="runner-install-media">
          <RunnerLaunchGuide />
        </div>

        <div className="runner-install-side">
          <div className="runner-install-actions">
            <section className="runner-install-step" aria-labelledby="runner-get">
              <h2 id="runner-get" className="runner-install-step-title t-title text-slate-900">
                <span className="num t-caption text-slate-400" aria-hidden="true">01</span>
                실행기 다운로드
              </h2>
              <p className="t-small text-slate-700">
                웹에서 만든 매크로를 실제로 돌리는 프로그램이에요. 내 Windows PC에서 실행되고, 매크로 실행 중에는 브라우저를 닫아도 조건 확인과 주문이 이어져요.
              </p>
              <p className="t-caption text-slate-700">{downloadMeta}</p>
              {download.available ? (
                <a
                  href={download.url}
                  download={download.isExternal ? undefined : true}
                  target={download.isExternal ? "_blank" : undefined}
                  rel={download.isExternal ? "noopener noreferrer" : undefined}
                  className="btn btn-l btn-primary runner-install-button"
                >
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5" />
                  </svg>
                  실행기 내려받기
                </a>
              ) : (
                <button
                  type="button"
                  disabled={download.state === "loading"}
                  onClick={() => void download.refresh()}
                  className="btn btn-l btn-secondary runner-install-button"
                >
                  {download.state === "loading" ? "확인 중…" : "다시 확인"}
                </button>
              )}
              {download.error && !download.available ? (
                <p className="t-small text-red-600" role="alert">다운로드를 확인하지 못했어요.</p>
              ) : null}
            </section>

            <section className="runner-install-step" aria-labelledby="runner-key">
              <h2 id="runner-key" className="runner-install-step-title t-title text-slate-900">
                <span className="num t-caption text-slate-400" aria-hidden="true">02</span>
                회원 키 연결
              </h2>
              <p className="t-small text-slate-700">실행기 창의 ④ 회원 키 칸에 붙여넣으세요.</p>
              {token ? (
                <RunnerKeyPanel key={token} compact />
              ) : (
                <Link to="/login?next=%2Frunner%2Finstall" className="btn btn-l btn-secondary runner-install-button">
                  로그인하고 회원 키 복사
                </Link>
              )}
              <p className="t-caption text-slate-700">회원 키는 계정 연결과 상태 확인·원격 종료에 쓰며, 거래소 키와 달라요.</p>
            </section>

            <section className="runner-install-step" aria-labelledby="runner-next">
              <h2 id="runner-next" className="runner-install-step-title t-title text-slate-900">
                <span className="num t-caption text-slate-400" aria-hidden="true">03</span>
                매크로 선택
              </h2>
              {/* 파일을 열었다는 명시적인 확인으로만 현재 버전을 기록한다. */}
              <Link
                to="/?run=1&step=1"
                onClick={() => { if (!opened) markRunnerOpened(); }}
                className="btn btn-l btn-secondary runner-install-button"
              >
                {opened ? "매크로 선택하기" : "실행했어요 · 매크로 선택"}
                <span aria-hidden="true">→</span>
              </Link>
              <p className="t-small text-slate-700">
                실행 후에는 <Link to="/agents" className="runner-install-inline-link">내 에이전트</Link>에서 실시간 차트와 손익을 확인하고 원격으로 종료할 수 있어요.
              </p>
            </section>
          </div>

          <aside className="runner-install-security" aria-labelledby="runner-security">
            <h2 id="runner-security" className="t-title text-slate-900">거래소 키는 실행기에서만</h2>
            <div className="runner-install-security-copy t-small text-slate-700">
              <p>
                바이낸스 API 키와 시크릿은 실행기 창에만 입력하세요. 껄무새 웹·서버로 전송하거나 파일에 저장하지 않으며, 실행기를 다시 켜면 새로 입력해요.
              </p>
              <p>처음에는 실제 자금이 들지 않는 <strong className="text-slate-900">테스트넷 키</strong>로 시작하는 걸 권해요.</p>
            </div>
          </aside>
        </div>
      </div>
    </div>
  );
}
