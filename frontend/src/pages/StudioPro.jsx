// 프로 빌더 — 기존 조건 판(Builder) + 검증 결과 + 근거. 코치 패널 자리는 별도 계획에서 채운다.
import { useRef, useState } from "react";
import Builder from "../components/Builder.jsx";
import { api } from "../api.js";
import { buildMacro, defaultForm, validateDetailed } from "../lib/macro.js";
import { analysisLabel, warningText, windowBars } from "../lib/validationView.js";
import { barScale, headlineNote, metricText } from "../lib/validationFormat.js";
import "./StudioPro.css";

const WINDOW_COUNT = 4;

const errorText = (err, fallback) => (err && typeof err.message === "string" && err.message ? err.message : fallback);
const safeLink = (url) => typeof url === "string" && /^https?:\/\//i.test(url);

function Metric({ label, value, note }) {
  return (
    <div className="pro-metric">
      <dt>{label}</dt>
      <dd>{value}</dd>
      {note ? <small>{note}</small> : null}
    </div>
  );
}

function WindowBars({ windows }) {
  const bars = windowBars(windows);
  if (!bars.length) return <p className="pro-note">구간별 결과가 없어요.</p>;
  const scale = barScale(bars);
  return (
    <ol className="pro-windows">
      {bars.map((bar, i) => {
        const row = Array.isArray(windows) ? windows[i] ?? {} : {};
        const span = row.start && row.end ? `${String(row.start).slice(0, 10)} ~ ${String(row.end).slice(0, 10)}` : "";
        // 실패한 구간은 pct 를 읽지 않는다 — 막대도 없고 숫자도 없이 '데이터 없음' 만 그린다.
        if (bar.failed) {
          return (
            <li key={bar.index} className="pro-window is-failed">
              <span className="pro-window-label">구간 {bar.index}{span ? <small>{span}</small> : null}</span>
              <span className="pro-window-track" aria-hidden="true" />
              <b>데이터 없음</b>
            </li>
          );
        }
        const width = scale > 0 ? (Math.abs(bar.pct) / scale) * 100 : 0;
        return (
          <li key={bar.index} className="pro-window">
            <span className="pro-window-label">구간 {bar.index}{span ? <small>{span}</small> : null}</span>
            <span className="pro-window-track" aria-hidden="true">
              <i className={bar.pct < 0 ? "is-down" : "is-up"} style={{ width: `${width}%` }} />
            </span>
            <b>{metricText(bar.pct, { suffix: "%" })}</b>
          </li>
        );
      })}
    </ol>
  );
}

function Evidence({ rows }) {
  const [openDate, setOpenDate] = useState("");
  if (!Array.isArray(rows) || !rows.length) return null;
  return (
    <div className="pro-evidence">
      <h4>근거로 본 날</h4>
      <ul>
        {rows.map((row, i) => {
          const key = row?.date || String(i);
          const open = openDate === key;
          const note = headlineNote(row?.headlines);
          const items = row?.headlines?.found === true && Array.isArray(row.headlines.items) ? row.headlines.items : [];
          return (
            <li key={key}>
              <button type="button" aria-expanded={open} onClick={() => setOpenDate(open ? "" : key)}>
                <span>{row?.date || "날짜 없음"}</span>
                <span>{metricText(row?.change_pct, { suffix: "%" })}</span>
                {row?.verdict ? <span className="pro-verdict">{row.verdict}</span> : null}
              </button>
              {open ? (
                <div className="pro-evidence-body">
                  <p className="pro-note">
                    거래량 {metricText(row?.volume_ratio, { suffix: "배" })} · 기준 종목 {metricText(row?.btc_change_pct, { suffix: "%" })}
                  </p>
                  {items.map((item, j) => (
                    safeLink(item?.url)
                      ? <a key={item.url + j} href={item.url} target="_blank" rel="noreferrer">{item.title} <small>{item.source}</small></a>
                      : <span key={j}>{item?.title} <small>{item?.source}</small></span>
                  ))}
                  {note ? <p className="pro-note">{note}</p> : null}
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export default function StudioPro() {
  const [form, setForm] = useState(defaultForm);
  const [formError, setFormError] = useState(null);
  const [report, setReport] = useState(null);
  const [analysis, setAnalysis] = useState(null);
  const [runError, setRunError] = useState("");
  const [explainError, setExplainError] = useState("");
  const [busy, setBusy] = useState(false);
  const [explaining, setExplaining] = useState(false);
  const [stale, setStale] = useState(false);
  const runId = useRef(0);

  // 조건을 고치면 화면의 결과는 이전 설정의 것이다 — 지우지 않고 '지난 결과' 로 표시한다.
  const editForm = (next) => {
    setForm(next);
    setFormError(null);
    setStale(true);
  };

  async function runValidation() {
    // 조건 판이 이미 보여 주는 입력 검증을 요청 전에도 한 번 거친다 — 서버까지 보낼 필요 없는 오류를 여기서 막는다.
    const problem = validateDetailed(form);
    if (problem) {
      setFormError(problem);
      return;
    }
    const mine = ++runId.current;
    const macro = buildMacro(form);
    setFormError(null);
    setBusy(true);
    setRunError("");
    setExplainError("");
    setAnalysis(null);
    setExplaining(false);
    let next;
    try {
      next = await api.validate(macro, WINDOW_COUNT);
    } catch (err) {
      if (runId.current === mine) {
        setRunError(errorText(err, "검증을 하지 못했어요. 잠시 뒤 다시 시도해 주세요."));
        setBusy(false);
      }
      return;
    }
    if (runId.current !== mine) return;
    setReport(next ?? {});
    setStale(false);
    setBusy(false);
    setExplaining(true);
    try {
      // 서버가 받는 summary 는 validate 응답 그대로다 — 숫자를 여기서 다시 만들지 않는다.
      const text = await api.validateExplain(macro, next ?? {});
      if (runId.current === mine) setAnalysis(text ?? null);
    } catch (err) {
      if (runId.current === mine) setExplainError(errorText(err, "해설을 불러오지 못했어요."));
    } finally {
      if (runId.current === mine) setExplaining(false);
    }
  }

  const result = report?.result ?? {};
  const warnings = (Array.isArray(report?.warnings) ? report.warnings : []).map(warningText).filter(Boolean);
  const monthly = Array.isArray(report?.monthly) ? report.monthly : [];

  return (
    <div className="pro">
      <h1 className="pro-title">프로 빌더</h1>
      <section className="pro-build" aria-label="조건">
        <Builder form={form} setForm={editForm} />
        {formError ? <p className="pro-error" role="alert">{formError.message}</p> : null}
        <button type="button" className="pro-run" onClick={runValidation} disabled={busy}>
          {busy ? "검증 중…" : "검증하기"}
        </button>
        {runError ? <p className="pro-error" role="alert">{runError}</p> : null}
      </section>

      {busy && !report ? <p className="pro-note" role="status">구간별로 다시 돌려 보는 중이에요. 잠시만 기다려 주세요.</p> : null}

      {report ? (
        <section className="pro-report" aria-label="검증 결과" aria-busy={busy}>
          {stale ? <p className="pro-stale" role="status">조건을 바꿨어요. 아래 결과는 이전 조건의 것이니 다시 검증해 주세요.</p> : null}

          {warnings.length > 0 ? (
            <ul className="pro-warnings">
              {warnings.map((text) => <li key={text}>{text}</li>)}
            </ul>
          ) : null}

          <dl className="pro-metrics">
            <Metric label="최종 수익률" value={metricText(result.final_return_pct, { suffix: "%" })} />
            <Metric label="단순 보유 수익률" value={metricText(result.buy_hold_return_pct, { suffix: "%" })} />
            <Metric label="최대 낙폭" value={metricText(result.mdd_pct, { suffix: "%" })} />
            <Metric label="거래 수" value={metricText(result.total_trades, { digits: 0 })} />
            <Metric label="소르티노" value={metricText(report.sortino)} note={report.sortino == null ? "측정하지 못했어요" : ""} />
            <Metric label="칼마" value={metricText(report.calmar)} note={report.calmar == null ? "측정하지 못했어요" : ""} />
            <Metric label="가장 많이 번 달의 몫" value={metricText(report.concentration?.top_month_share_pct, { suffix: "%" })} />
            <Metric label="가장 깊은 낙폭" value={metricText(report.drawdown?.depth_pct, { suffix: "%" })} />
          </dl>

          <h3>구간별 성과</h3>
          <WindowBars windows={report.windows} />

          <h3>월별 수익</h3>
          {monthly.length ? (
            <ol className="pro-months">
              {monthly.map((row) => (
                <li key={row.month}><span>{row.month}</span><b>{metricText(row.pct, { suffix: "%" })}</b></li>
              ))}
            </ol>
          ) : <p className="pro-note">월별 결과가 없어요.</p>}

          <div className="pro-analysis">
            {analysis ? (
              <>
                <h3>{analysisLabel(analysis.source)}</h3>
                <p>{analysis.text}</p>
                <Evidence rows={analysis.evidence} />
              </>
            ) : explaining ? (
              <p className="pro-note" role="status">해설을 만드는 중이에요…</p>
            ) : explainError ? (
              <p className="pro-error" role="alert">{explainError}</p>
            ) : null}
          </div>
        </section>
      ) : null}
    </div>
  );
}
