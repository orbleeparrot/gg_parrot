// 프로 빌더 — 왼쪽은 기존 조건 판(Builder) + 검증 결과 + 근거, 오른쪽은 코치 패널(좁혀 가는 대화로 판을 채운다).
import { useCallback, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import Builder from "../components/Builder.jsx";
import BuilderModeMenu from "../components/BuilderModeMenu.jsx";
import CandleChart from "../components/CandleChart.jsx";
import CoachPanel from "../components/CoachPanel.jsx";
import { EmptyState } from "../components/Page.jsx";
import { api } from "../api.js";
import { computeStrategyOverlay } from "../lib/indicators.js";
import { isDomestic, normalizeExchange } from "../lib/exchanges.js";
import { CANDLE_INTERVALS, FIELDLESS_ERROR_FIELDS, buildMacro, validateDetailed } from "../lib/macro.js";
import { seedForm } from "../lib/studioProSeed.js";
import { analysisLabel, sameForm, warningText, windowBars } from "../lib/validationView.js";
import { barScale, headlineNote, metricText } from "../lib/validationFormat.js";
import "./StudioPro.css";

const WINDOW_COUNT = 4;
const COACH_DONE_TEXT = "코치가 판을 다 채웠어요 · 아래에서 검증해 보세요.";

// 코치가 방금 바꾼 칸을 한 줄로 알려 준다. 조건 판(Builder) 안의 칸을 직접 깜빡이게 하려면 Builder 에
// 새 prop 을 달고 내부 격자까지 손대야 하므로, 코치 패널 쪽에 "방금 바꾼 것" 한 줄을 두는 쪽을 골랐다.
// 패치 키는 여러 개가 한꺼번에 오므로(규칙 하나가 기본값 열 칸을 함께 바꾼다) 묶음 이름으로 줄인다.
const PATCH_GROUPS = [
  ["규칙", (key) => key === "rule_type"],
  ["봉 간격", (key) => key === "candle_interval"],
  ["종목", (key) => key === "symbol"],
  ["비중", (key) => key === "leg_weights"],
  ["기간", (key) => key === "preset" || key === "start" || key === "end"],
  ["시작 자금", (key) => ["initial_capital", "amount_per_buy", "base_order_size", "safety_order_size"].includes(key)],
  ["손절 · 투입 비율", (key) => ["use_stop_loss", "stop_loss_pct", "invest_ratio_pct"].includes(key)],
  ["진입 조건", (key) => key === "use_entry_filter" || key.startsWith("filter_")],
  ["묶음 한도", (key) => key === "use_bundle_risk" || key.startsWith("bundle_")],
];

// 바뀐 칸 묶음의 이름들. 어느 묶음에도 안 드는 키(규칙마다 딸려 오는 세부 값)는 '규칙' 으로 셈한다.
export function patchGroups(patch) {
  const keys = Object.keys(patch || {});
  if (!keys.length) return [];
  const names = PATCH_GROUPS.filter(([, match]) => keys.some(match)).map(([name]) => name);
  const matched = new Set(keys.filter((key) => PATCH_GROUPS.some(([, match]) => match(key))));
  if (keys.length > matched.size && !names.includes("규칙")) names.unshift("규칙");
  return names;
}

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
  const location = useLocation();
  const [form, setForm] = useState(() => seedForm(location.state));
  const [formError, setFormError] = useState(null); // { message, form } — 그 오류가 난 조건을 함께 담는다
  const [report, setReport] = useState(null);
  const [reportForm, setReportForm] = useState(null); // 화면의 결과를 만든 조건
  const [analysis, setAnalysis] = useState(null);
  const [runError, setRunError] = useState("");
  const [explainError, setExplainError] = useState("");
  const [busy, setBusy] = useState(false);
  const [fileBusy, setFileBusy] = useState(false);
  const [fileError, setFileError] = useState(""); // 매크로 파일 내려받기가 실패한 이유(서버 문구 그대로)
  const [explaining, setExplaining] = useState(false);
  const [coachChanged, setCoachChanged] = useState([]); // 코치가 방금 바꾼 칸 묶음 이름
  const [coachDone, setCoachDone] = useState(false);
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

  // 코치가 올린 패치 — 이번 턴의 몫만 오므로 지금 조건에 **병합**한다(되돌리기는 쌓인 전부를 보낸다).
  // 조건이 바뀌면 아래 '지난 결과' 표시는 form 과 reportForm 을 견주어 저절로 뜬다 — 따로 켤 것이 없다.
  const applyCoachPatch = (patch) => {
    setForm((previous) => ({ ...previous, ...patch }));
    setCoachChanged(patchGroups(patch));
    setCoachDone(false);
  };

  // 조건을 고치면 화면의 결과는 이전 설정의 것이다 — 지우지 않고 '지난 결과' 로 표시한다.
  // 고쳤는지는 setForm 이 불린 횟수가 아니라 값으로 가린다. 공용 조건 판은 값이 그대로인 갱신도
  // (때로는 await 뒤에 늦게) 보내므로, 호출마다 '바뀜' 으로 치면 방금 나온 결과가 지난 결과로 둔갑한다.
  // 그래서 따로 상태를 두지 않고 매 그림마다 '결과를 만든 조건' 과 '지금 조건' 을 견줘 낸다.
  const stale = report !== null && reportForm !== null && !sameForm(form, reportForm);
  const shownFormError = formError && sameForm(formError.form, form) ? formError : null;
  // 비중 · 묶음 한도 · 레그 규칙 오류는 칸에 띄울 자리가 없다(종목 행 안의 생 입력 · 체크박스 · 펼치는 판).
  // 검증을 누르기 전에도 바로 보여 준다 — 안 그러면 비중 합이 틀려 검증이 막히는 걸 누르고 나서야 안다.
  const fieldlessError = (() => {
    const problem = validateDetailed(form);
    return problem && FIELDLESS_ERROR_FIELDS.includes(problem.field) ? problem : null;
  })();
  const formProblem = shownFormError || fieldlessError;

  // 매크로 파일 내려받기 — 지금 조건을 매크로로 싸서 .ggm.json 으로 받는다(실행기에 넣어 돌린다).
  // 누르기 전에 조건 검증을 한 번 거친다: 틀린 조건으로 파일을 받아 가면 실행기에서야 막힌다.
  // 여러 종목 묶음 매크로는 서버가 422 로 거절한다(실행기가 한 종목만 돌린다) — 그 문구를 그대로 보인다.
  async function downloadFile() {
    const problem = validateDetailed(form);
    if (problem) {
      setFormError({ ...problem, form });
      setFileError("");
      return;
    }
    setFormError(null);
    setFileError("");
    setFileBusy(true);
    try {
      await api.downloadMacroFile(buildMacro(form));
    } catch (err) {
      setFileError(errorText(err, "매크로 파일을 내려받지 못했어요. 잠시 뒤 다시 시도해 주세요."));
    } finally {
      setFileBusy(false);
    }
  }

  async function runValidation() {
    // 조건 판이 이미 보여 주는 입력 검증을 요청 전에도 한 번 거친다 — 서버까지 보낼 필요 없는 오류를 여기서 막는다.
    const problem = validateDetailed(form);
    if (problem) {
      setFormError({ ...problem, form });
      return;
    }
    const macro = buildMacro(form);
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
      {/* 두 열 — 왼쪽은 차트 · 조건 판 · 결과, 오른쪽은 코치. 좁은 화면에서는 코치가 위로 올라간다(CSS). */}
      <div className="pro-cols">
      <div className="pro-main">
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
        {/* 프로 판 — 비중 입력 · 종목마다 규칙 바꾸기 · 묶음 한도가 여기서 켜진다(스펙 §10).
            "dense" 를 넘기면 좁은 판용 격자로 판 전체가 다시 조판되므로 변형을 따로 둔다. */}
        <Builder form={form} setForm={setForm} variant="pro" intervalOptions={intervalOptions} />
        {formProblem ? <p className="pro-error" role="alert">{formProblem.message}</p> : null}
        <div className="pro-acts">
          <button type="button" className="pro-run" onClick={runValidation} disabled={busy}>
            {busy ? "검증 중…" : "검증하기"}
          </button>
          {/* 매크로 실행기로 가는 길 — 기본 빌더와 같은 끝점을 쓴다(저장 없이 파일만 내준다). */}
          <button type="button" className="pro-file" onClick={downloadFile} disabled={fileBusy}>
            {fileBusy ? "파일 만드는 중…" : "매크로 파일 내려받기"}
          </button>
        </div>
        <p className="pro-note">.ggm.json 을 실행기에 넣어 실행해요</p>
        {/* 서버는 내려줄 때 실행기 버전을 모른다 — v9 이하에 넣으면 세션 시작에서 426 이 난다. 그래서 여기서 미리 말한다. */}
        {isDomestic(form.exchange) ? <p className="pro-note">업비트·빗썸은 실행기 v10 이상이 필요해요.</p> : null}
        {fileError ? <p className="pro-error" role="alert">{fileError}</p> : null}
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

      <aside className="pro-side" aria-label="코치">
        <CoachPanel
          exchange={form.exchange}
          onPatch={applyCoachPatch}
          onDone={() => setCoachDone(true)}
        />
        {coachChanged.length > 0 ? (
          <p className="pro-coach-changed" role="status">방금 {coachChanged.join(" · ")}을 바꿨어요</p>
        ) : null}
        {coachDone ? <p className="pro-note">{COACH_DONE_TEXT}</p> : null}
      </aside>
      </div>
    </div>
  );
}
