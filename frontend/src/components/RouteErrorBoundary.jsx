import { Component } from "react";
import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../lib/auth.js";
import { isChunkLoadError, reloadOnceForNewBuild } from "../lib/chunkReload.js";
import { reportClientError } from "../lib/errorReport.js";

// Keep the shell outside this boundary so a page exception cannot remove navigation.
export class PageErrorBoundary extends Component {
  state = { failed: false, chunk: false };

  static getDerivedStateFromError(error) {
    return { failed: true, chunk: isChunkLoadError(error) };
  }

  componentDidCatch(error, info) {
    // 화면 코드 파일을 못 받은 경우(배포 직후)는 한 번 자동으로 새로고침한다.
    const chunk = isChunkLoadError(error);
    if (chunk) reloadOnceForNewBuild();
    // 잡힌 오류는 콘솔과 서버(화면 오류 모으기)에 남긴다 — 사용자가 알려 주기 전에 알 수 있게.
    console.error("[page-error]", error, info?.componentStack);
    reportClientError(chunk ? "chunk" : "render", error);
  }

  componentDidUpdate(previous) {
    if (this.state.failed && previous.resetKey !== this.props.resetKey) {
      this.setState({ failed: false });
    }
  }

  render() {
    if (!this.state.failed) return this.props.children;
    if (this.state.chunk) {
      return (
        <section className="py-14 text-center" role="alert">
          <h1 className="t-h2 text-slate-900">새 버전이 배포됐어요</h1>
          <p className="mt-3 t-small text-slate-700">새로고침하면 최신 화면을 불러와요.</p>
          <div className="mt-6 flex justify-center gap-3">
            <button type="button" className="btn btn-m btn-primary" onClick={() => window.location.reload()}>새로고침</button>
            <Link to="/" className="btn btn-m btn-secondary">홈으로</Link>
          </div>
        </section>
      );
    }
    return (
      <section className="py-14 text-center" role="alert">
        <h1 className="t-h2 text-slate-900">화면을 불러오지 못했어요</h1>
        <p className="mt-3 t-small text-slate-700">다시 시도하거나 다른 메뉴로 이동해 주세요.</p>
        <div className="mt-6 flex justify-center gap-3">
          <button type="button" className="btn btn-m btn-primary" onClick={() => this.setState({ failed: false })}>다시 시도</button>
          <Link to="/" className="btn btn-m btn-secondary">홈으로</Link>
        </div>
      </section>
    );
  }
}

export default function RouteErrorBoundary({ children }) {
  const { pathname, search } = useLocation();
  const { accountVersion } = useAuth();
  return <PageErrorBoundary resetKey={`${pathname}${search}:${accountVersion}`}>{children}</PageErrorBoundary>;
}

// 상단바·사이드바·하단 띠 같은 틀 부품 — 하나가 깨져도 그 부품만 사라지고 앱과 이동은 남는다.
// (예전엔 경계가 본문에만 있어 틀에서 난 오류가 앱 전체를 빈 화면으로 만들었다.)
export class ShellBoundary extends Component {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error, info) {
    console.error(`[shell-error:${this.props.name || "part"}]`, error, info?.componentStack);
    reportClientError(isChunkLoadError(error) ? "chunk" : "render", error);
  }

  render() {
    return this.state.failed ? (this.props.fallback ?? null) : this.props.children;
  }
}

// 마지막 그물 — 라우터 밖. 무엇이 깨져도 흰 화면 대신 새로고침 안내를 둔다.
export class RootErrorBoundary extends Component {
  state = { failed: false, chunk: false };

  static getDerivedStateFromError(error) {
    return { failed: true, chunk: isChunkLoadError(error) };
  }

  componentDidCatch(error, info) {
    const chunk = isChunkLoadError(error);
    if (chunk) reloadOnceForNewBuild();
    console.error("[app-error]", error, info?.componentStack);
    reportClientError(chunk ? "chunk" : "render", error);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <main className="site-main py-14 text-center" role="alert">
        <h1 className="t-h2 text-slate-900">{this.state.chunk ? "새 버전이 배포됐어요" : "화면을 그리지 못했어요"}</h1>
        <p className="mt-3 t-small text-slate-700">새로고침하면 다시 불러와요.</p>
        <div className="mt-6 flex justify-center">
          <button type="button" className="btn btn-m btn-primary" onClick={() => window.location.reload()}>새로고침</button>
        </div>
      </main>
    );
  }
}
