// 프로 빌더 — 기존 조건 판(Builder) + 검증 결과 + 근거. 코치 패널 자리는 별도 계획에서 채운다.
import { useCallback, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import Builder from "../components/Builder.jsx";
import BuilderModeMenu from "../components/BuilderModeMenu.jsx";
import CandleChart from "../components/CandleChart.jsx";
import { EmptyState } from "../components/Page.jsx";
import { api } from "../api.js";
import { computeStrategyOverlay } from "../lib/indicators.js";
import { isDomestic, normalizeExchange } from "../lib/exchanges.js";
import { CANDLE_INTERVALS, buildMacro, validateDetailed } from "../lib/macro.js";
import { seedForm } from "../lib/studioProSeed.js";
import { analysisLabel, sameForm, warningText, windowBars } from "../lib/validationView.js";
import { barScale, headlineNote, metricText } from "../lib/validationFormat.js";
import "./StudioPro.css";

const WINDOW_COUNT = 4;
// 서버(/api/validate)가 여러 종목 매크로에 내는 문구와 같다 — 요청 한 번과 분당 한도를 아낀다.
const PORTFOLIO_MESSAGE = "여러 종목 포트폴리오 매크로는 아직 검증할 수 없어요. 종목 하나로 나눠 검증해 주세요.";

const errorText = (err, fallback) => (err && typeof err.message === "string" && err.message ? err.message : fallback);
const dayOf = (stamp) => (typeof stamp === "string" && stamp ? stamp.slice(0, 10) : "");
// 가장 깊었던 낙폭의 고점 ~ 바닥 날짜. 낙폭이 없거나 날짜를 읽을 수 없으면 줄표.
const drawdownSpan = (drawdown) => (dayOf(drawdown?.start) && dayOf(drawdown?.trough) ? `${dayOf(drawdown.start)} ~ ${dayOf(drawdown.trough)}` : "—");
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
              <i className={bar.pct < 0 ? "is-down" : "is-up"} style={{ "--fill": `${width}%` }} />
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
  const location = useLocation();
  const [form, setForm] = useState(() => seedForm(location.state));
  const [formError, setFormError] = useState(null); // { message, form } — 그 오류가 난 조건을 함께 담는다
  const [report, setReport] = useState(null);
  const [reportForm, setReportForm] = useState(null); // 화면의 결과를 만든 조건
  const [analysis, setAnalysis] = useState(null);
  const [runError, setRunError] = useState("");
  const [explainError, setExplainError] = useState("");
  const [busy, setBusy] = useState(false);
  const [explaining, setExplaining] = useState(false);
  const runId = useRef(0);
  const navigate = useNavigate();

  // 차트 — 기본 빌더와 같은 조각을 같은 방식으로 쓴다. 종목·봉 간격·보조지표가 조건을 그대로 따라오고,
  // 봉 간격은 차트 도구줄에서도 바뀐다(조건 판과 같은 form 한 곳을 고친다).
  const chartSymbols = useMemo(() => {
    const seen = new Set();
    const out = [];
    for (const part of (form.symbol || "").split(",")) {
      const symbol = part.trim().toUpperCase();
      if (!symbol || seen.has(symbol)) continue;
      seen.add(symbol);
      out.push(symbol);
      if (out.length === 5) break;
    }
    return out;
  }, [form.symbol]);
  // 국내 현물은 선물 봉이 없다. 그 밖에는 매크로가 실제로 쓸 시장을 따라간다.
  const chartMarket = isDomestic(form.exchange)
    ? "spot"
    : form.market === "futures"
      || (form.market === "auto" && (form.rule_type === "K" || form.position_side === "short" || Number(form.leverage) > 1))
      ? "futures"
      : "spot";
  // 봉 간격 — 국내 적립식은 날짜 간격을 세므로 일봉만. (테스트 기간당 봉 수 한도는 서버가 본다.)
  const intervalOptions = useMemo(() => CANDLE_INTERVALS.map((option) => (
    isDomestic(form.exchange) && form.rule_type === "C" && option.value !== "1d"
      ? { ...option, disabled: true, title: "국내 적립식 매수는 일봉으로 계산해요" }
      : option
  )), [form.exchange, form.rule_type]);
  const disabledIntervals = useMemo(
    () => intervalOptions.filter((option) => option.disabled).map((option) => ({ value: option.value, title: option.title })),
    [intervalOptions],
  );
  const overlay = useCallback((candles) => computeStrategyOverlay(form, candles), [form]);

  // 빌더 종류 전환 — 지금 조건을 매크로로 싸서 들고 간다. 조건 판이 그릴 수 없는 값이면
  // 매크로를 만들 수 없으니 들고 가지 않는다(받는 화면이 저장해 둔 조건으로 연다).
  const switchMode = (target) => {
    let macro = null;
    try { macro = buildMacro(form); } catch (_) { macro = null; }
    navigate(target.path, macro ? { state: { macro, source: "builder-mode" } } : undefined);
  };

  // 조건을 고치면 화면의 결과는 이전 설정의 것이다 — 지우지 않고 '지난 결과' 로 표시한다.
  // 고쳤는지는 setForm 이 불린 횟수가 아니라 값으로 가린다. 공용 조건 판은 값이 그대로인 갱신도
  // (때로는 await 뒤에 늦게) 보내므로, 호출마다 '바뀜' 으로 치면 방금 나온 결과가 지난 결과로 둔갑한다.
  // 그래서 따로 상태를 두지 않고 매 그림마다 '결과를 만든 조건' 과 '지금 조건' 을 견줘 낸다.
  const stale = report !== null && reportForm !== null && !sameForm(form, reportForm);
  const shownFormError = formError && sameForm(formError.form, form) ? formError : null;

  async function runValidation() {
    // 조건 판이 이미 보여 주는 입력 검증을 요청 전에도 한 번 거친다 — 서버까지 보낼 필요 없는 오류를 여기서 막는다.
    const problem = validateDetailed(form);
    if (problem) {
      setFormError({ ...problem, form });
      return;
    }
    const macro = buildMacro(form);
    if (macro.symbols) {
      setFormError({ message: PORTFOLIO_MESSAGE, form });
      return;
    }
    const mine = ++runId.current;
    const startedWith = form; // 이 요청이 보낸 조건 — 요청 중에 고치면 도착한 결과는 이 값과 달라진다
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
    // 요청이 도는 동안 조건을 고쳤다면 받은 결과는 이전 조건의 것이다 — 지난 결과 표시가 저절로 뜬다.
    setReport(next ?? {});
    setReportForm(startedWith);
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
  const drawdown = report?.drawdown ?? null;
  // 모르는 코드가 여럿이어도 같은 일반 문장은 한 줄만 보인다.
  const warnings = [...new Set((Array.isArray(report?.warnings) ? report.warnings : []).map(warningText))];
  const monthly = Array.isArray(report?.monthly) ? report.monthly : [];

  return (
    <div className="pro">
      {/* 제목이 곧 바이더 전환 메뉴다 — 기본 바이더와 같은 조각, 같은 자리. */}
      <h1 className="pro-title">
        <BuilderModeMenu mode="pro" onSwitch={switchMode} />
      </h1>
      {/* 차트 — 조건을 고치면 종목·봉 간격·보조지표가 바로 따라온다. 봉 간격은 차트 도구줄에서도 바뀐다.
          판 머리는 따로 두지 않는다: CandleChart(studio) 의 도구줄이 곧 머리다(기본 빌더와 같다). */}
      <section className="pro-chart" aria-label="차트 · 보조지표">
        <div className={"pro-chart-body" + (chartSymbols.length > 1 ? " is-multi" : "")}>
          {chartSymbols.length > 0 ? (
            // 거래소마다 고유한 봉을 쓴다 — 같은 이름의 KRW 짝과 USDT 짝은 다른 시장이다.
            chartSymbols.map((symbol) => (
              <CandleChart
                key={`${normalizeExchange(form.exchange)}:${symbol}`}
                symbol={symbol}
                exchange={form.exchange || "binance"}
                market={chartMarket}
                interval={form.candle_interval || "1d"}
                onIntervalChange={(value) => setForm((previous) => ({ ...previous, candle_interval: value }))}
                overlay={overlay}
                variant="studio"
                disabledIntervals={disabledIntervals}
              />
            ))
          ) : (
            <EmptyState title="종목을 고르면 차트가 나와요">아래 조건에서 거래소와 종목을 골라 주세요.</EmptyState>
          )}
        </div>
      </section>

      <section className="pro-build" aria-label="조건">
        <Builder form={form} setForm={setForm} intervalOptions={intervalOptions} />
        {shownFormError ? <p className="pro-error" role="alert">{shownFormError.message}</p> : null}
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
            <Metric label="샤프" value={metricText(result.sharpe)} />
            <Metric label="소르티노" value={metricText(report.sortino)} note={report.sortino == null ? "측정하지 못했어요" : ""} />
            <Metric label="칼마" value={metricText(report.calmar)} note={report.calmar == null ? "측정하지 못했어요" : ""} />
            <Metric label="손익비" value={metricText(result.profit_factor)} />
            <Metric label="최대 연속 손실" value={metricText(result.max_consecutive_losses, { digits: 0, suffix: "회" })} />
            <Metric label="가장 많이 번 달의 몫" value={metricText(report.concentration?.top_month_share_pct, { suffix: "%" })} />
            <Metric label="상위 거래 몫" value={metricText(result.top_trade_share_pct, { suffix: "%" })} />
            <Metric label="가장 깊은 낙폭" value={metricText(drawdown?.depth_pct, { suffix: "%" })} />
            <Metric label="낙폭 시기" value={drawdownSpan(drawdown)} />
            <Metric
              label="회복까지"
              value={metricText(drawdown?.recovery_days, { digits: 0, suffix: "일" })}
              note={drawdown && drawdown.recovery_days == null ? "아직 회복하지 못했어요" : ""}
            />
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
