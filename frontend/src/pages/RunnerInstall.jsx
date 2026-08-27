// 매크로 실행기 설치 안내 — 상단바 '실행기 설치' 로 들어오는 단독 화면.
//
// 실행 마법사(RunnerDownload)의 '실행기 준비' 단계는 매크로를 먼저 고른 사람만
// 볼 수 있다. 하지만 "실행기가 뭔지, 왜 내 PC 에 필요한지, 어떻게 까는지"는
// 매크로와 무관하게 아무 때나 궁금해진다 — 그래서 그 설명만 따로 떼어 둔다.
// 배포 주소·버전 판단은 마법사와 같은 lib/runnerDownload.js 를 쓴다.
import { useState } from "react";
import { Link } from "react-router-dom";
import { PageHeader, SectionTitle } from "../components/Page.jsx";
import { RunnerKeyPanel } from "../components/RunnerSessions.jsx";
import { useAuth } from "../lib/auth.js";
import {
  fmtSize,
  isRunnerOpened,
  markRunnerOpened,
  useRunnerDownload,
} from "../lib/runnerDownload.js";

// 실행기가 무엇인지 — 웹이 못 하는 일을 왜 내 PC 가 맡는지부터 설명한다.
const WHY = [
  {
    title: "주문은 내 PC 에서 나가요",
    body: "브라우저 탭은 닫히거나 잠들면 멈춰요. 실행기는 내 윈도우 PC 에서 계속 돌면서 매크로 조건을 확인하고 주문을 넣어요.",
  },
  {
    title: "거래소 키가 밖으로 안 나가요",
    body: "바이낸스 API 키와 시크릿은 실행기 창에만 입력해요. 껄무새 웹이나 서버로는 보내지 않고, 실행기를 닫으면 남지도 않아요.",
  },
  {
    title: "웹에서는 상태만 봐요",
    body: "실행기가 상태를 보고하면 '내 에이전트' 화면에서 실시간 차트와 손익을 보고 원격으로 종료할 수 있어요.",
  },
];

const INSTALL_STEPS = [
  {
    title: "실행기 내려받기",
    body: "아래 버튼을 누르면 GitHub 릴리스에서 ggparrot-runner.exe 를 받아요. 설치 과정 없이 파일 하나로 바로 실행돼요.",
  },
  {
    title: "브라우저 경고 허용하기",
    body: "'확인되지 않은 다운로드' 로 막히면 다운로드 목록에서 파일 이름이 ggparrot-runner.exe 인지 확인한 뒤 유지를 눌러요. Windows SmartScreen 창이 뜨면 추가 정보 → 실행을 선택해요.",
  },
  {
    title: "처음 한 번 열어 두기",
    body: "받은 파일을 한 번 실행하면 웹의 '실행기 열기' 주소가 등록돼요. 다음부터는 웹에서 누르면 이미 열려 있는 실행기가 앞으로 나와요.",
  },
  {
    title: "회원 키 붙여넣기",
    body: "실행기 창의 회원 키 칸에 껄무새 회원 키를 붙여넣으면 계정과 연결돼요. 이 키는 상태 확인과 원격 종료에만 쓰고, 거래소 키가 아니에요.",
  },
];

export default function RunnerInstall() {
  const { token } = useAuth();
  const download = useRunnerDownload();
  const [opened, setOpened] = useState(isRunnerOpened);

  const statusText = opened
    ? "이 PC 에서 한 번 실행함"
    : download.state === "loading"
      ? "배포 확인 중"
      : download.state === "available"
        ? "다운로드 가능"
        : download.state === "error"
          ? "배포 상태 확인 실패"
          : "배포 정보 없음";

  return (
    <div className="max-w-3xl">
      <PageHeader
        eyebrow="Windows 전용 · 설치 없이 실행"
        title="껄무새 매크로 실행기"
        description="웹에서 만든 매크로를 실제로 돌리는 프로그램이에요. 내 윈도우 PC 에서 실행되고, 거래소 API 키는 이 프로그램 안에서만 쓰여요."
        actions={<Link to="/?run=1&step=1" className="btn btn-m btn-secondary">실행 가이드 전체 보기</Link>}
      />

      {/* 1. 왜 필요한가 */}
      <section aria-labelledby="runner-why">
        <SectionTitle className="mb-3"><span id="runner-why">실행기가 하는 일</span></SectionTitle>
        <dl className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          {WHY.map((item) => (
            <div key={item.title} className="card p-4">
              <dt className="t-label font-bold text-slate-900">{item.title}</dt>
              <dd className="mt-2 t-small text-slate-700">{item.body}</dd>
            </div>
          ))}
        </dl>
      </section>

      {/* 2. 내려받기 */}
      <section className="mt-9" aria-labelledby="runner-get">
        <SectionTitle className="mb-3"><span id="runner-get">내려받기</span></SectionTitle>
        <div className="card p-5">
          <div className="flex items-start justify-between gap-4 flex-wrap">
            <div className="min-w-0">
              <p className="t-label font-bold text-slate-900">
                {download.state === "loading"
                  ? "배포 상태를 확인하고 있어요."
                  : download.available
                    ? "Windows 실행기를 받을 수 있어요."
                    : download.state === "error"
                      ? "배포 상태를 확인하지 못했어요."
                      : "이 서버에 실행기 다운로드 주소가 아직 설정되지 않았어요."}
              </p>
              <p className="mt-1 t-small text-slate-700">Windows 10 이상 · 설치 과정 없음 · 파일 하나 실행</p>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              {download.available ? (
                <a
                  href={download.url}
                  download={download.isExternal ? undefined : true}
                  target={download.isExternal ? "_blank" : undefined}
                  rel={download.isExternal ? "noopener noreferrer" : undefined}
                  className="btn btn-l btn-primary"
                >
                  실행기 내려받기
                </a>
              ) : null}
              {["error", "unavailable"].includes(download.state) ? (
                <button type="button" onClick={() => void download.refresh()} className="btn btn-m btn-ghost">
                  다시 확인
                </button>
              ) : null}
            </div>
          </div>

          <dl className="mt-4 pt-4 border-t border-slate-200 grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div><dt className="stat-label">배포 상태</dt><dd className="t-label font-bold text-slate-900">{statusText}</dd></div>
            {download.available && download.version ? (
              <div><dt className="stat-label">다운로드 버전</dt><dd className="t-label font-bold num text-slate-900">v{download.version}</dd></div>
            ) : null}
            {download.supportsLaunch ? (
              <div><dt className="stat-label">자동 연결 최소 버전</dt><dd className="t-label font-bold num text-slate-900">v{download.minVersion}</dd></div>
            ) : null}
            {download.available && download.size ? (
              <div><dt className="stat-label">파일 크기</dt><dd className="t-label font-bold num text-slate-900">{fmtSize(download.size)}</dd></div>
            ) : null}
          </dl>

          {/* 서버 응답이 실패해도 공식 릴리스로 받을 수 있으면 굳이 겁주지 않는다. */}
          {download.error && !download.available ? (
            <p className="mt-3 t-small text-red-600" role="alert">배포 확인 응답: {download.error}</p>
          ) : null}
        </div>
      </section>

      {/* 3. 설치 순서 */}
      <section className="mt-9" aria-labelledby="runner-steps">
        <SectionTitle className="mb-3"><span id="runner-steps">설치하고 처음 여는 순서</span></SectionTitle>
        <ol className="card divide-y divide-slate-200">
          {INSTALL_STEPS.map((step, index) => (
            <li key={step.title} className="p-4 flex gap-4">
              <span className="num t-caption text-slate-400 shrink-0 pt-1">{String(index + 1).padStart(2, "0")}</span>
              <div className="min-w-0">
                <strong className="t-label text-slate-900">{step.title}</strong>
                <p className="mt-1 t-small text-slate-700">{step.body}</p>
              </div>
            </li>
          ))}
        </ol>
        <div className="mt-4">
          {opened ? (
            <p className="t-small text-slate-600">이 브라우저는 실행기를 한 번 연 것으로 기록돼 있어요. 웹에서 바로 연결할 수 있어요.</p>
          ) : (
            <button
              type="button"
              onClick={() => { markRunnerOpened(); setOpened(true); }}
              className="btn btn-m btn-secondary"
            >
              이 PC 에서 실행기를 이미 열었어요
            </button>
          )}
        </div>
      </section>

      {/* 4. 회원 키 — 실행기 연결에 바로 필요한 값이라 여기서 복사할 수 있게 둔다. */}
      <section className="mt-9" aria-labelledby="runner-key">
        <SectionTitle className="mb-3"><span id="runner-key">실행기에 넣을 회원 키</span></SectionTitle>
        {token ? (
          <RunnerKeyPanel />
        ) : (
          <div className="card p-5">
            <p className="t-small text-slate-700">로그인하면 실행기에 붙여넣을 회원 키를 여기서 바로 복사할 수 있어요.</p>
            <Link to="/login?next=%2Frunner%2Finstall" className="mt-3 inline-flex btn btn-m btn-secondary">로그인하기</Link>
          </div>
        )}
      </section>

      <div className="notice-warn mt-9 t-caption text-slate-700">
        <b className="text-slate-900">주의 · </b>
        거래소 API 키와 시크릿은 껄무새 웹 화면에 절대 입력하지 마세요. 실행기 창에만 넣고, 처음에는 실제 돈이 들지 않는 테스트넷 키로 시작하는 걸 권해요.
      </div>

      <div className="mt-6 flex items-center gap-2 flex-wrap">
        <Link to="/?run=1&step=1" className="btn btn-m btn-primary">매크로 골라서 실행하기</Link>
        <Link to="/agents" className="btn btn-m btn-secondary">내 에이전트 보기</Link>
      </div>
    </div>
  );
}
