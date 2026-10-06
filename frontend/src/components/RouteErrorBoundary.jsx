import { Component } from "react";
import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../lib/auth.js";

// Keep the shell outside this boundary so a page exception cannot remove navigation.
export class PageErrorBoundary extends Component {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidUpdate(previous) {
    if (this.state.failed && previous.resetKey !== this.props.resetKey) {
      this.setState({ failed: false });
    }
  }

  render() {
    if (!this.state.failed) return this.props.children;
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
