import { useEffect, useMemo, useRef, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { PlayIcon } from "@phosphor-icons/react/dist/csr/Play";
import { StopIcon } from "@phosphor-icons/react/dist/csr/Stop";
import { ArrowUpRightIcon } from "@phosphor-icons/react/dist/csr/ArrowUpRight";
import { ArrowLeftIcon } from "@phosphor-icons/react/dist/csr/ArrowLeft";
import { TimerIcon } from "@phosphor-icons/react/dist/csr/Timer";
import { api } from "../api.js";
import { useAuth } from "../lib/auth.js";
import { MODEL, VERDICTS, createNewsTestRunner, elapsedSeconds, emptyTestState } from "../lib/newsSentimentTest.js";
import "./NewsSentimentTest.css";

const phases = { idle: "시작 대기", loading: "뉴스 확인 중", analyzing: "판단 중", complete: "판단 완료", waiting: "새 뉴스 대기", paused: "일시 중지", error: "확인 필요" };
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
  const [state, setState] = useState(emptyTestState);
  const [model, setModel] = useState(null);
  const [checking, setChecking] = useState(false);
  const [selected, setSelected] = useState(null);
  const startRequest = useRef(null);
  const runner = useMemo(() => createNewsTestRunner({ fetchFeed: api.newsTestArticles, analyze: api.newsTestAnalyze, onChange: setState }), []);
  useEffect(() => () => { startRequest.current?.abort(); runner.stop(); }, [runner]);
  useEffect(() => {
    const controller = new AbortController();
    setChecking(true);
    api.newsTestStatus({ signal: controller.signal }).then(setModel).catch((error) => {
      if (!controller.signal.aborted) setModel({ ready: false, detail: error.message });
    }).finally(() => { if (!controller.signal.aborted) setChecking(false); });
    return () => controller.abort();
  }, []);
  const start = async () => {
    startRequest.current?.abort();
    const controller = new AbortController();
    startRequest.current = controller;
    setSelected(null);
    // Starting always rechecks the server, including after configuration changes.
    setChecking(true);
    try {
      const next = await api.newsTestStatus({ signal: controller.signal });
      if (controller.signal.aborted) return;
      setModel(next);
      if (next.ready) void runner.start();
    } catch (error) { if (!controller.signal.aborted) setModel({ ready: false, detail: error.message }); }
    finally { if (!controller.signal.aborted) setChecking(false); }
  };
  const current = selected || state.current;
  const result = current?.result;
  const article = current?.article;
  const isAnalyzing = !selected && state.phase === "analyzing";
  const history = state.history;
  return <div className="sentiment-test">
    <header className="st-head">
      <div><Link className="st-back t-caption" to="/admin?tab=news"><ArrowLeftIcon size={16} />뉴스 수집 현황</Link><h1 className="t-h2">뉴스 판단 테스트</h1></div>
      <div className="st-controls">
        <span className="st-model t-caption"><i className={model?.ready ? "is-ready" : ""} />{MODEL}</span>
        <button type="button" className={`btn btn-m ${state.running ? "btn-secondary" : "btn-primary"}`} disabled={checking}
          onClick={state.running ? runner.stop : start}>
          {state.running ? <StopIcon size={18} weight="fill" /> : <PlayIcon size={18} weight="fill" />}
          {checking ? "연결 확인 중" : state.running ? "중지" : state.count || state.error ? "다시 시작" : "테스트 시작"}
        </button>
      </div>
    </header>
    <div className="st-summary t-caption">
      <span className="st-phase" role="status"><i className={state.running ? "is-active" : ""} />{phases[state.phase]}</span>
      <span>완료 <b className="num">{state.count}</b></span><span>평균 <b className="num">{state.count ? `${elapsedSeconds(state.totalMs / state.count)}초` : "—"}</b></span>
      <span>대기 <b className="num">{state.queueCount}</b></span>
      {!state.running && state.count > 0 ? <button type="button" onClick={() => { runner.reset(); setSelected(null); }}>기록 초기화</button> : null}
    </div>
    {model?.ready === false || state.error ? <div className="st-error t-small" role="alert">{state.error || model.detail}</div> : null}
    <div className="st-workspace">
      <section className="st-focus" aria-label="현재 뉴스 판단">
        <div className="st-focus-label t-caption"><span>{selected ? "이전 결과" : "현재 뉴스"}</span>{selected ? <button type="button" onClick={() => setSelected(null)}>현재 뉴스로 돌아가기 <ArrowUpRightIcon size={16} /></button> : <span className="num">{state.count + (isAnalyzing ? 1 : 0) > 0 ? String(state.count + (isAnalyzing ? 1 : 0)).padStart(2, "0") : "—"}</span>}</div>
        {article ? <article key={article.id} className="st-article">
          <div className="st-article-meta t-caption"><span>{article.scope === "MARKET" ? "시장·규제" : article.scope}</span><span>{article.source || "수집 뉴스"}</span>{timeLabel(article.published) ? <time>{timeLabel(article.published)}</time> : null}</div>
          <h2>{article.title}</h2>
          {article.excerpt ? <p className="st-excerpt t-body">{article.excerpt}</p> : null}
          {article.url ? <a className="st-source t-small" href={article.url} target="_blank" rel="noreferrer">원문 보기 <ArrowUpRightIcon size={17} /></a> : null}
          <div className={`st-result ${result ? `is-${result.verdict}` : ""}`}>
            <div className="st-verdict"><span className="t-caption">모델 판단</span><strong aria-live="polite">{result ? VERDICTS[result.verdict] : isAnalyzing ? "판단 중" : "판단 대기"}</strong></div>
            <div className="st-timing"><span className="t-caption"><TimerIcon size={17} />{result ? "판단 소요 시간" : "경과 시간"}</span><strong>{result ? <span className="num">{elapsedSeconds(result.elapsed_ms)}</span> : <Clock startedAt={current.startedAt} running={isAnalyzing} />}<small>초</small></strong></div>
          </div>
          {result ? <div className="st-reason"><p className="t-body">{result.reason}</p><div className="st-result-meta t-caption"><span>서버 요청 → 응답 완료</span>{result.model_ms != null ? <span>모델 처리 <b className="num">{elapsedSeconds(result.model_ms)}초</b></span> : null}{result.load_ms != null ? <span>모델 로드 <b className="num">{elapsedSeconds(result.load_ms)}초</b></span> : null}</div></div> : <div className={`st-progress ${isAnalyzing ? "is-running" : ""}`} aria-hidden="true"><span /></div>}
        </article> : <div className="st-empty"><TimerIcon size={42} weight="light" /><h2 className="t-h4">한 건씩, 판단까지 걸린 시간</h2><p className="t-small">수집된 뉴스가 도착하면 여기서 바로 판단합니다.</p><div className="st-empty-scale num">00<span>.</span>00<small>초</small></div></div>}
        <footer className="st-footnote t-caption">제목·수집된 발췌 기준 · 영향이 불분명하면 판단 유보</footer>
      </section>
      <aside className="st-history" aria-label="이전 판단 기록">
        <header><h2 className="t-title">판단 기록 <span className="num">{history.length}</span></h2><span className="t-caption">최근 60건</span></header>
        {history.length ? <ol>{history.map((entry) => <li key={entry.article.id}><button type="button" aria-pressed={selected?.article.id === entry.article.id} onClick={() => setSelected(entry)}>
          <div className="st-history-meta t-caption"><span className={`st-history-verdict is-${entry.result.verdict}`}>{VERDICTS[entry.result.verdict]}</span><span className="num">{elapsedSeconds(entry.result.elapsed_ms)}초</span></div>
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
