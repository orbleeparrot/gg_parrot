import { useEffect, useRef, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { ArrowUpRightIcon } from "@phosphor-icons/react/dist/csr/ArrowUpRight";
import { ArrowLeftIcon } from "@phosphor-icons/react/dist/csr/ArrowLeft";
import { TimerIcon } from "@phosphor-icons/react/dist/csr/Timer";
import { api } from "../api.js";
import { useAuth } from "../lib/auth.js";
import useAdaptivePolling from "../hooks/useAdaptivePolling.js";
import { MODEL, VERDICTS, elapsedSeconds, liveStartedAt, phaseLabel } from "../lib/newsSentimentTest.js";
import "./NewsSentimentTest.css";

const timeLabel = (value) => {
  const date = new Date(value);
  return value && Number.isFinite(date.getTime()) ? date.toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
};
function Clock({ startedAt, running }) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    setNow(Date.now());
    if (!running) return;
    const timer = setInterval(() => setNow(Date.now()), 50);
    return () => clearInterval(timer);
  }, [startedAt, running]);
  return <span className="num" aria-label="판단 경과 시간">{elapsedSeconds(now - startedAt)}</span>;
}

function Workspace() {
  const [snapshot, setSnapshot] = useState(null);
  const [receivedAt, setReceivedAt] = useState(0);
  const [error, setError] = useState("");
  const [selectedId, setSelectedId] = useState(null);
  const version = useRef("");
  useAdaptivePolling(async (signal) => {
    const data = await api.newsTestResults(version.current, { signal });
    if (signal.aborted) return;
    setError("");
    if (data.unchanged) return;
    version.current = data.version || "";
    setSnapshot(data);
    setReceivedAt(Date.now());
  }, { intervalMs: 1500, maxIntervalMs: 15000, onError: (e) => setError(e.message), pollKey: "news-sentiment" });
  const history = snapshot?.history || [];
  const selected = history.find(entry => entry.article.id === selectedId);
  const current = selected || snapshot?.current;
  const result = current?.result;
  const article = current?.article;
  const isAnalyzing = !selected && current?.status === "processing";
  const failed = current?.status === "failed";
  const stats = snapshot?.stats || {};
  return <div className="sentiment-test">
    <header className="st-head">
      <div><Link className="st-back t-caption" to="/admin?tab=news"><ArrowLeftIcon size={16} />뉴스 수집 현황</Link><h1 className="t-h2">뉴스 판단 테스트</h1></div>
      <div className="st-controls">
        <span className="st-model t-caption"><i className={snapshot?.connected ? "is-ready" : ""} />{MODEL}</span>
        <span className="st-auto t-caption">자동 분석</span>
      </div>
    </header>
    <div className="st-summary t-caption">
      <span className="st-phase" role="status"><i className={snapshot?.enabled && !snapshot?.detail && !error ? "is-active" : ""} />{phaseLabel(snapshot, error)}</span>
      <span>완료 <b className="num">{stats.completed ?? "—"}</b></span><span>평균 <b className="num">{stats.average_ms != null ? `${elapsedSeconds(stats.average_ms)}초` : "—"}</b></span>
      <span>대기 <b className="num">{stats.pending ?? "—"}</b></span>
      {stats.failed > 0 ? <span>실패 <b className="num">{stats.failed}</b></span> : null}
    </div>
    {error || snapshot?.detail ? <div className="st-error t-small" role="alert">{error || snapshot.detail}</div> : null}
    <div className="st-workspace">
      <section className="st-focus" aria-label="현재 뉴스 판단">
        <div className="st-focus-label t-caption"><span>{selected ? "이전 결과" : isAnalyzing ? "현재 뉴스" : "최근 결과"}</span>{selected ? <button type="button" onClick={() => setSelectedId(null)}>현재 뉴스로 돌아가기 <ArrowUpRightIcon size={16} /></button> : <span className="num">{stats.completed > 0 ? String(stats.completed).padStart(2, "0") : "—"}</span>}</div>
        {article ? <article key={article.id} className="st-article">
          <div className="st-article-meta t-caption"><span>{article.scope === "MARKET" ? "시장·규제" : article.scope}</span><span>{article.source || "수집 뉴스"}</span>{timeLabel(article.published) ? <time>{timeLabel(article.published)}</time> : null}</div>
          <h2>{article.title}</h2>
          {article.excerpt ? <p className="st-excerpt t-body">{article.excerpt}</p> : null}
          {article.url ? <a className="st-source t-small" href={article.url} target="_blank" rel="noreferrer">원문 보기 <ArrowUpRightIcon size={17} /></a> : null}
          <div className={`st-result ${result ? `is-${result.verdict}` : ""}`}>
            <div className="st-verdict"><span className="t-caption">모델 판단</span><strong aria-live="polite">{result ? VERDICTS[result.verdict] : failed ? "판단 실패" : isAnalyzing ? "판단 중" : "판단 대기"}</strong></div>
            <div className="st-timing"><span className="t-caption"><TimerIcon size={17} />{result ? "판단 소요 시간" : "경과 시간"}</span><strong>{result ? <span className="num">{elapsedSeconds(result.elapsed_ms)}</span> : isAnalyzing ? <Clock startedAt={liveStartedAt(current, snapshot, receivedAt)} running={!error} /> : <span className="num">—</span>}{result || isAnalyzing ? <small>초</small> : null}</strong></div>
          </div>
          {result ? <div className="st-reason">{result.probabilities ? <><p className="t-caption">선택지 상대 점수 · 보정 전</p><div className="st-scores">{Object.entries(VERDICTS).map(([key, label]) => <div key={key}><span className="t-small">{label}</span><b className="num">{(result.probabilities[key] * 100).toFixed(1)}%</b><div className="st-score-track"><i style={{ width: `${result.probabilities[key] * 100}%` }} /></div></div>)}</div></> : result.reason ? <p className="t-body">{result.reason}</p> : null}<div className="st-result-meta t-caption"><span>서버 요청 → 응답 완료</span>{result.model_ms != null ? <span>모델 처리 <b className="num">{elapsedSeconds(result.model_ms)}초</b></span> : null}{result.load_ms != null ? <span>모델 로드 <b className="num">{elapsedSeconds(result.load_ms)}초</b></span> : null}</div></div> : failed ? <p className="st-error t-small">{current.error}</p> : <div className={`st-progress ${isAnalyzing ? "is-running" : ""}`} aria-hidden="true"><span /></div>}
        </article> : <div className="st-empty"><TimerIcon size={42} weight="light" /><h2 className="t-h4">새 뉴스를 기다리고 있어요</h2><p className="t-small">뉴스가 수집되면 서버에서 한 건씩 자동으로 판단합니다.</p><div className="st-empty-scale num">00<span>.</span>00<small>초</small></div></div>}
        <footer className="st-footnote t-caption">페이지를 닫아도 분석은 계속됩니다 · 제목·수집된 발췌 기준</footer>
      </section>
      <aside className="st-history" aria-label="이전 판단 기록">
        <header><h2 className="t-title">판단 기록 <span className="num">{history.length}</span></h2><span className="t-caption">최근 60건</span></header>
        {history.length ? <ol>{history.map((entry) => <li key={entry.article.id}><button type="button" aria-pressed={selected?.article.id === entry.article.id} onClick={() => setSelectedId(entry.article.id)}>
          <div className="st-history-meta t-caption"><span className={`st-history-verdict ${entry.result ? `is-${entry.result.verdict}` : ""}`}>{entry.result ? VERDICTS[entry.result.verdict] : "판단 실패"}</span><span className="num">{entry.result ? `${elapsedSeconds(entry.result.elapsed_ms)}초` : "—"}</span></div>
          <p>{entry.article.title}</p><span className="st-history-source t-caption">{entry.article.source || entry.article.scope}</span>
        </button></li>)}</ol> : <div className="st-history-empty t-small">다음 뉴스로 넘어가면<br />이전 결과가 여기에 남아요.</div>}
      </aside>
    </div>
  </div>;
}
export default function NewsSentimentTest() {
  const { token, user } = useAuth();
  if (!user) return <Navigate to="/login?next=%2Fadmin%2Fnews-test" replace />;
  if (!user.is_admin) return <Navigate to="/mypage" replace />;
  return <Workspace key={token} />;
}
