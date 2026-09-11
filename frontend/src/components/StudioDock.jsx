// 직접 만들기 결과 독 — 다섯 탭의 화면. 데이터는 공용 부품(ResultView·OptimizePanel·PaperPanel)의
// 것과 같고, 화면만 워크벤치 시안대로 그린다: 수치 띠 · 자산곡선 · 열 지도 · 상태 상자 · 세 갈래 결과.
// 스타일은 pages/Studio.css 의 .sd-* .
import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import EquityChart from "./EquityChart.jsx";
import InfoTooltip from "./InfoTooltip.jsx";
import { verdict } from "./OptimizePanel.jsx";
import { useMacroActions } from "./PaperPanel.jsx";
import StrategyDetails from "./StrategyDetails.jsx";
import { fmtMoney, fmtMoneyCompact, fmtKrw, fmtPrice, fmtQty, quoteOf, baseOf } from "../lib/format.js";
import { buildMacro, RULE_TYPES, CANDLE_INTERVALS } from "../lib/macro.js";
import { useUsdKrw } from "../lib/usdkrw.js";

const AI_MASCOT = "/brand/navigation/ggparrot-nav-agent.svg";
const SIDE_KO = { buy: "매수", sell: "매도", short: "숏 진입", cover: "숏 청산" };
const SIDE_CLS = { buy: "is-buy", short: "is-buy", sell: "is-sell", cover: "is-sell" };
const pct = (v, digits = 2) => `${v >= 0 ? "+" : ""}${Number(v).toFixed(digits)}%`;
// 금액 축약 — 1,000,000 → 1.00M · 764,842 → 764.8K (수치 띠의 보조 글은 한 줄이어야 한다).
const compactNum = (v) => {
  const a = Math.abs(Number(v) || 0);
  if (a >= 1e9) return (v / 1e9).toFixed(2) + "B";
  if (a >= 1e6) return (v / 1e6).toFixed(2) + "M";
  if (a >= 1e3) return (v / 1e3).toFixed(1) + "K";
  return Number(v).toFixed(0);
};
// 등락색 — 독 전용 클래스(.is-up/.is-down). Tailwind 의 text-*-600 은 .sd-box-v 같은 상자 규칙과 같은 우선순위라
// 실리는 순서에 따라 지는 일이 있었다(페이퍼 현재 수익률이 마이너스인데 흰색).
const tone = (v) => (v >= 0 ? "is-up" : "is-down");

// ── 탭 줄 — 밑줄 탭 + 상태 점(빈 · 초록=완료 · 호박=조건 바뀜 · 노랑 깜박임=진행 중) ──
export function StudioTabs({ tabs, active, onChange }) {
  return (
    <div className="sd-tabs" role="tablist" aria-label="결과 보기">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          id={`studio-tab-${tab.id}`}
          aria-selected={active === tab.id}
          aria-controls="studio-dock-panel"
          disabled={!tab.enabled}
          title={!tab.enabled ? "백테스트 결과가 있어야 볼 수 있어요" : undefined}
          onClick={() => onChange(tab.id)}
          className={"sd-tab" + (active === tab.id ? " is-on" : "")}
        >
          <i className={"studio-dot" + (tab.dot ? ` is-${tab.dot}` : "")} aria-hidden="true" />
          {tab.label}
        </button>
      ))}
    </div>
  );
}

// ── 백테스트 — 수치 여섯 칸 + 자산곡선 + 종목별 표 ──
export function StudioBacktest({ result: r, perSymbol, periodLabel, symbol, leverage = 1 }) {
  const { rate: krwRate } = useUsdKrw();
  const quote = quoteOf(symbol);
  const bh = r.buy_hold_return_pct != null ? r.buy_hold_return_pct : null;
  const vsHold = bh !== null ? r.final_return_pct - bh : null;
  const liq = r.liquidation_count || 0;
  // 원래 ResultView 의 정보 세트 그대로(수익률·홀딩 비교 · 최종 평가금액+원화 · MDD · 승률 · 총 매매 횟수 · 샤프 · 손익비 · 최대 연속손절),
  // 관련된 것끼리 한 칸에: 수익률↔홀딩 비교, 평가금액↔원화, MDD↔연속손절, 승률↔매매 횟수. 없던 지표는 만들지 않는다.
  const streak = r.max_consecutive_losses || 0;
  const krw = fmtKrw(r.final_equity, krwRate); // "≈ 10.3억원"
  const krwMatch = /^(≈?\s*[\d.,]+)(.*)$/.exec(krw || "");
  // 숫자에만 .num(고정폭) — 한글까지 고정폭 클래스에 넣으면 한글이 대체 글꼴로 빠져 글자가 깨져 보인다.
  const kpis = [
    { k: `백테스트 수익률 · ${periodLabel || "테스트 기간"}`, term: "backtest", v: pct(r.final_return_pct), cls: tone(r.final_return_pct),
      d: bh === null ? "비교 기준 없음" : (
        <>홀딩했다면 <span className="num">{pct(bh)}</span> (<span className={"num " + tone(vsHold)}>{vsHold >= 0 ? "+" : ""}{vsHold.toFixed(2)}%p</span>)</>
      ) },
    { k: "최종 평가금액", v: <>{fmtMoneyCompact(r.final_equity, symbol).replace(new RegExp(`\\s*${quote}$`), "")}<small className="sd-kpi-unit">{quote}</small></>, title: fmtMoney(r.final_equity, symbol), cls: "",
      d: krw ? (krwMatch ? <><span className="num">{krwMatch[1]}</span>{krwMatch[2]}</> : krw) : null },
    { k: "MDD (최대낙폭)", term: "mdd", v: `-${r.mdd_pct.toFixed(1)}%`, cls: "is-down",
      d: <>최대 연속손절 <span className={"num" + (streak >= 5 ? " is-down" : "")}>{streak}</span>회</> },
    { k: "승률", term: "win_rate", v: `${r.win_rate_pct.toFixed(1)}%`, cls: "", d: <>총 매매 횟수 <span className="num">{r.total_trades}</span></> },
    { k: "샤프지수", term: "sharpe", v: r.sharpe != null ? r.sharpe.toFixed(2) : "—", cls: r.sharpe != null && r.sharpe >= 1 ? "is-up" : "", d: null },
    { k: "손익비 (PF)", term: "profit_factor", v: r.profit_factor != null ? r.profit_factor.toFixed(2) : "—", cls: r.profit_factor != null && r.profit_factor >= 1 ? "is-up" : "", d: null },
  ];
  return (
    <div className="sd-bt">
      <div className="sd-kpis">
        {kpis.map((kpi) => (
          <div key={kpi.k} className="sd-kpi">
            <div className="sd-kpi-k"><span className="sd-kpi-cap">{kpi.k}</span>{kpi.term && <InfoTooltip term={kpi.term} />}</div>
            <div className={"sd-kpi-v num " + kpi.cls} title={kpi.title}>{kpi.v}</div>
            {kpi.d && <div className="sd-kpi-d">{kpi.d}</div>}
          </div>
        ))}
      </div>

      {liq > 0 && (
        <div className="alert alert-risk sd-liq">
          <div className="t-title">기간 중 <span className="num">{liq}</span>번 청산됐어요 (전액 손실)</div>
          <div className="mt-1 t-small">
            레버리지 <span className="num">{leverage}</span>배라 청산으로 잃은 금액 <b className="num">{fmtMoney(r.liquidated_loss || 0, symbol)}</b>
            {fmtKrw(r.liquidated_loss || 0, krwRate) && <span className="num"> ({fmtKrw(r.liquidated_loss || 0, krwRate)})</span>}.
            <InfoTooltip term="liquidation" />
          </div>
        </div>
      )}

      <div className="sd-two">
        <div className="sd-eq">
          <div className="sd-cap">자산곡선{periodLabel ? ` · ${periodLabel}` : ""} · 파선이 본전</div>
          <EquityChart curve={r.equity_curve} height={140} stretch />
        </div>
        <div className="sd-side">
          <table className="sd-table">
            <thead>
              <tr><th>종목</th><th className="r">수익률</th><th className="r">MDD</th><th className="r">거래</th></tr>
            </thead>
            <tbody>
              {perSymbol && perSymbol.length > 0 ? (
                perSymbol.map((row) => (
                  <tr key={row.symbol}>
                    <td className="num">{row.symbol}</td>
                    <td className={"r num " + tone(row.final_return_pct)}>{pct(row.final_return_pct)}</td>
                    <td className="r num">-{Number(row.mdd_pct).toFixed(1)}%</td>
                    <td className="r num">{row.total_trades}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td className="num">{symbol}</td>
                  <td className={"r num " + tone(r.final_return_pct)}>{pct(r.final_return_pct)}</td>
                  <td className="r num">-{r.mdd_pct.toFixed(1)}%</td>
                  <td className="r num">{r.total_trades}</td>
                </tr>
              )}
            </tbody>
          </table>
          {leverage > 1 && (
            <p className="sd-note"><span className="badge badge-risk">고위험 레버리지 <span className="num">{leverage}</span>배 <InfoTooltip term="leverage" /></span></p>
          )}
        </div>
      </div>
    </div>
  );
}

// ── 껄무새 AI 해설 — 얼굴 + 머리말 + 요점 + 이 매크로를 쓴다면. 규칙 기반 문장은 보이지 않는다(분석하기를 누르면 AI). ──
export function StudioAiExplain({ explanation, onAiExplain, aiBusy, aiError }) {
  const isAi = explanation && explanation.source === "ai";
  return (
    <div className="sd-ai">
      <img src={AI_MASCOT} alt="" width="256" height="256" className="sd-ai-face" aria-hidden="true" draggable="false" />
      <div className="sd-ai-body">
        <div className="sd-ai-who">
          껄무새 AI 해설
          <small>{isAi ? "이 결과가 왜 이렇게 나왔는지 · 5줄 안으로" : "이 결과가 왜 이렇게 나왔는지 쉽게 정리해 드려요"}</small>
        </div>
        {isAi ? (
          <>
            <p className="sd-ai-head">{explanation.headline}</p>
            {explanation.points?.length > 0 && (
              <ul className="sd-ai-points">
                {explanation.points.map((point, index) => <li key={index}>{point}</li>)}
              </ul>
            )}
            {explanation.lesson && (
              <p className="sd-ai-lesson"><b>이 매크로를 쓴다면 · </b>{explanation.lesson}</p>
            )}
            {explanation.disclaimer && <p className="sd-note">{explanation.disclaimer}</p>}
          </>
        ) : (
          <p className="sd-ai-empty">
            버튼을 누르면 서버의 AI 가 백테스트 결과의 원인을 짚어요 — 어디서 벌고 어디서 잃었는지, 무엇을 조심할지, 어떤 값을 바꿔 볼지.
          </p>
        )}
        {aiError && <p className="sd-note text-amber-700" role="alert">{aiError}</p>}
        <div className="sd-ai-actions">
          <button type="button" onClick={onAiExplain} disabled={aiBusy} className="btn btn-m btn-secondary">
            {aiBusy ? "분석 중…" : isAi ? "다시 분석" : "AI 로 분석하기"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── 익·손절 최적화 — 열 지도(왼쪽) + 최적·검증·주의·적용(오른쪽). 시안대로 셀은 한 줄, 색은 은은한 초록/빨강. ──
// 셀 색 — 본전(0)이 회색, |수익률|/최대 에 비례해 초록·빨강을 섞는다(시안의 color-mix).
function heatBg(value, extent) {
  if (!(extent > 0)) return {};
  const mag = Math.min(1, Math.abs(value) / extent);
  const p = Math.round(8 + 56 * mag);
  const color = value >= 0 ? "rgb(var(--c-green-600))" : "rgb(var(--c-red-600))";
  return { background: `color-mix(in srgb, ${color} ${p}%, rgb(var(--c-slate-100)))` };
}

export function StudioOptimize({ form, setForm, valErr, onResult }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [data, setData] = useState(null);

  async function run() {
    setError("");
    if (valErr) return setError(valErr);
    setBusy(true);
    try {
      const res = await api.optimize(buildMacro(form));
      setData(res);
      onResult?.(res);
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }
  const apply = (tp, sl) => setForm((f) => ({ ...f, take_profit_pct: tp, stop_loss_pct: sl, use_stop_loss: true }));

  const returns = data ? data.cells.map((c) => c.final_return_pct) : [];
  const extent = returns.length ? Math.max(...returns.map((v) => Math.abs(v))) : 0;
  const cellAt = (tp, sl) => data?.cells.find((c) => c.tp === tp && c.sl === sl) || null;
  const best = data?.best || null;
  const current = data?.current || null;
  const currentCell = current ? cellAt(current.tp, current.sl) : null;
  const isCurrent = (tp, sl) => current && current.tp === tp && current.sl === sl;
  const isBest = (tp, sl) => best && best.tp === tp && best.sl === sl;
  const bestApplied = best && Number(form.take_profit_pct) === best.tp && Number(form.stop_loss_pct) === best.sl && form.use_stop_loss;
  const info = best ? verdict(best, data.validation) : null;

  if (!data) {
    return (
      <div className="sd-empty">
        <b>익절 × 손절 조합을 모두 돌려 봐요</b>
        <span>기간을 학습과 검증으로 나눠, 학습에서 고른 값이 고를 때 쓰지 않은 검증 구간에서도 통했는지까지 확인해요. 칸을 누르면 조건에 바로 적용돼요.</span>
        {error && <span className="is-error">오류: {error}</span>}
        <button type="button" onClick={run} disabled={busy || !!valErr} className="btn btn-m btn-secondary">
          {busy ? "최적화 중…" : "최적화 돌려보기"}
        </button>
      </div>
    );
  }

  return (
    <div className="sd-opt">
      <div className="sd-opt-grid">
        <div className="sd-opt-head">
          <b>손절 ＼ 익절 · 학습 구간 수익률 <InfoTooltip term="optimize" /></b>
          <span className="sd-opt-hint">회색이 본전 · 칸을 누르면 적용</span>
          <button type="button" onClick={run} disabled={busy || !!valErr} className="btn btn-s btn-secondary">{busy ? "최적화 중…" : "다시 최적화"}</button>
        </div>
        <div className="sd-heat" style={{ gridTemplateColumns: `52px repeat(${data.tp_values.length}, minmax(0, 1fr))` }}>
          <div className="sd-heat-h" />
          {data.tp_values.map((tp) => <div key={`h-${tp}`} className="sd-heat-h num">{tp}%</div>)}
          {data.sl_values.map((sl) => (
            <Fragment key={`r-${sl}`}>
              <div className="sd-heat-h num">{sl}%</div>
              {data.tp_values.map((tp) => {
                const c = cellAt(tp, sl);
                if (!c) return <div key={`${tp}-${sl}`} />;
                return (
                  <button
                    key={`${tp}-${sl}`}
                    type="button"
                    onClick={() => apply(tp, sl)}
                    style={heatBg(c.final_return_pct, extent)}
                    className={"sd-heat-c num" + (isBest(tp, sl) ? " is-best" : "") + (isCurrent(tp, sl) ? " is-current" : "")}
                    aria-label={`익절 ${tp}% 손절 ${sl}% · 학습 ${pct(c.final_return_pct, 1)}${c.oos_return_pct != null ? ` · 검증 ${pct(c.oos_return_pct, 1)}` : ""}`}
                    title={
                      `익절 ${tp}% · 손절 ${sl}%\n학습 ${pct(c.final_return_pct)} · MDD -${c.mdd_pct.toFixed(1)}% · 샤프 ${c.sharpe ?? "—"} · 매매 ${c.total_trades}회\n` +
                      (c.oos_return_pct != null ? `검증 ${pct(c.oos_return_pct)} (매매 ${c.oos_trades}회)\n` : "검증 구간 없음 (기간이 짧아요)\n") +
                      "누르면 조건에 적용"
                    }
                  >
                    {pct(c.final_return_pct, 1).replace("%", "")}
                  </button>
                );
              })}
            </Fragment>
          ))}
        </div>
      </div>

      <div className="sd-opt-side">
        {best && (
          <div className="sd-box">
            <div className="sd-box-k">★ 최적 · 주변까지 고르게 좋은 구간</div>
            <div className="sd-box-v">익절 <span className="num">{best.tp}%</span> · 손절 <span className="num">{best.sl}%</span></div>
          </div>
        )}
        {best && data.validation?.split && best.oos_return_pct != null && (
          <div className="sd-box">
            <div className="sd-box-k">검증 구간 · 고를 때 쓰지 않은 기간 <InfoTooltip text={`검증 기간 ${data.validation.test_label} 에서 다시 돌린 성적이에요. 학습 ${data.validation.train_label}.`} /></div>
            <div className={"sd-box-v num " + tone(best.oos_return_pct)}>
              {pct(best.oos_return_pct, 1)}
              {currentCell?.oos_return_pct != null && <small className="text-slate-500"> · 지금 설정 {pct(currentCell.oos_return_pct, 1)}</small>}
            </div>
          </div>
        )}
        <div className="sd-box is-warn">
          {info ? <><b>{info.label}</b> — {info.text}</> : <><b>과최적화 주의</b> — 과거에 맞춘 값이에요. 주변까지 고르게 좋은 구간이 더 믿을 만해요.</>}
        </div>
        {best && (
          <button type="button" onClick={() => apply(best.tp, best.sl)} disabled={bestApplied || !!valErr} className="btn btn-m btn-secondary w-full">
            {bestApplied ? "최적값 적용됨 · 다시 계산 중" : `익절 ${best.tp}% · 손절 ${best.sl}% 적용`}
          </button>
        )}
        {error && <p className="sd-note is-error">오류: {error}</p>}
      </div>
    </div>
  );
}

// 시작 시점 설정 한 줄 — 페이퍼는 시작할 때의 설정으로 고정된다.
function macroLine(macro) {
  if (!macro) return "";
  const interval = CANDLE_INTERVALS.find((i) => i.value === macro.candle_interval)?.label || macro.candle_interval;
  const parts = [macro.symbol, RULE_TYPES[macro.rule_type]?.label, macro.position_side === "short" ? "숏" : "롱", interval ? `${interval}봉` : "", `${macro.leverage || 1}배`];
  return parts.filter(Boolean).join(" · ");
}

// ── 페이퍼 트레이딩 — 왼쪽 상태 상자, 오른쪽 매매 로그 ──
export function StudioPaper({ macro, valErr, controller }) {
  const { status, mode, setMode, busy, error, startedMacro, startedMode, running, start, stop, restart } = controller;
  const { rate: krwRate } = useUsdKrw();
  const quote = quoteOf(macro.symbol);
  const base = baseOf(macro.symbol);
  const ret = status?.current_return ?? 0;
  const macroChanged = running && startedMacro && JSON.stringify(startedMacro) !== JSON.stringify(macro);
  const modeLabel = (value) => (value === "replay" ? "데모 리플레이" : "실시간");

  return (
    <div className="sd-paper">
      <div className="sd-paper-left">
        {!status ? (
          <>
            <div className="sd-field">
              <label>모의매매 방식 <InfoTooltip term="paper_trading" /></label>
              <div className="seg sd-seg" role="group" aria-label="페이퍼 트레이딩 방식">
                <button type="button" onClick={() => setMode("live")} aria-pressed={mode === "live"} className={"seg-item " + (mode === "live" ? "seg-item-on" : "")} disabled={busy}>실시간 (live)</button>
                <button type="button" onClick={() => setMode("replay")} aria-pressed={mode === "replay"} className={"seg-item " + (mode === "replay" ? "seg-item-on" : "")} disabled={busy}>데모 리플레이 · 최근 시세 빠르게 재생</button>
              </div>
            </div>
            <div className="sd-lock">
              <b>시작 시점의 설정이 잠겨요.</b> 익절·손절·일일 최대손실·최대 보유시간·재진입 금지가 함께 적용되고, 도중에 조건을 바꾸면 재시작해야 반영돼요.
              실제 주문 없이 실시간 시세로 "샀다·팔았다 치고" 기록만 하니 거래소 계정·API 키가 필요 없어요.
            </div>
            {macro.leverage > 1 && (
              <div className="sd-lock is-risk">
                <b>레버리지 <span className="num">{macro.leverage}</span>배</b> — 가격이 약 <b className="num">{(100 / macro.leverage).toFixed(macro.leverage >= 100 ? 2 : 1)}%</b> 반대로 움직이면 청산(전액 손실)돼요. 모의(가짜 돈)로 위험을 체험하는 용도예요.
              </div>
            )}
            <div className="sd-paper-actions">
              <button type="button" onClick={start} disabled={busy || !!valErr} className="btn btn-m btn-secondary">{busy ? "시작 중…" : "페이퍼 트레이딩 시작"}</button>
              <span className="sd-note"><span className="num">{macro.symbol}</span> · {modeLabel(mode)}</span>
            </div>
          </>
        ) : (
          <>
            <div className="sd-stat">
              <div className="sd-box">
                <div className="sd-box-k">상태</div>
                <div className="sd-box-v">
                  <span className={running ? "is-up" : "text-slate-500"}>●</span> {running ? "진행 중" : "중지됨"}
                  <small className="text-slate-500"> · {modeLabel(startedMode || mode)}</small>
                </div>
              </div>
              <div className="sd-box">
                <div className="sd-box-k">현재 수익률</div>
                <div className={"sd-box-v num " + tone(ret)}>{pct(ret)}</div>
              </div>
              <div className="sd-box">
                <div className="sd-box-k">현재 평가금액 ({quote})</div>
                <div className="sd-box-v num" title={fmtMoney(status.current_equity, macro.symbol)}>{fmtMoneyCompact(status.current_equity, macro.symbol)}</div>
                {fmtKrw(status.current_equity, krwRate) && <div className="sd-box-d num">{fmtKrw(status.current_equity, krwRate)}</div>}
              </div>
              <div className="sd-box">
                <div className="sd-box-k">청산</div>
                <div className={"sd-box-v num " + ((status.liquidations || 0) > 0 ? "is-down" : "")}>{status.liquidations || 0}회</div>
                {(status.liquidations || 0) > 0 && <div className="sd-box-d num">잃은 금액 {fmtMoney(status.liquidated_loss || 0, macro.symbol)}</div>}
              </div>
            </div>
            <div className={"sd-lock" + (macroChanged ? " is-warn" : "")}>
              시작 시점 설정 · <b>{macroLine(startedMacro || macro)}</b>
              {macroChanged && <> · 조건을 바꿨지만 이 세션은 시작 시점 설정으로 계속 돌아요. 반영하려면 재시작하세요.</>}
            </div>
            <div className="sd-paper-actions">
              {running ? (
                <>
                  <button type="button" onClick={stop} disabled={busy} className="btn btn-s btn-danger">중지</button>
                  {macroChanged && <button type="button" onClick={restart} disabled={busy || !!valErr} className="btn btn-s btn-secondary">바뀐 설정으로 재시작</button>}
                </>
              ) : (
                <button type="button" onClick={start} disabled={busy || !!valErr} className="btn btn-s btn-secondary">{busy ? "시작 중…" : "다시 시작"}</button>
              )}
              <Link to="/mypage" className="btn btn-s btn-secondary">마이페이지에서 보기</Link>
            </div>
          </>
        )}
        {valErr && <p className="sd-note text-amber-700" role="alert">{valErr}</p>}
        {error && <p className="sd-note is-error" role="alert">오류: {error}</p>}
      </div>

      <div className="sd-log">
        <div className="sd-log-cap">
          <span>실시간 매매 로그 (최신이 위)</span>
          {status && status.last_price > 0 && <span>현재가 <b className="num">{fmtPrice(status.last_price)}</b> {quote}</span>}
        </div>
        {!status ? (
          <div className="sd-log-empty"><b>백테스트가 괜찮으면 여기서 실제 시세로 돌려 봐요</b>결과가 쌓이는 동안 페이지를 닫아도 마이페이지에서 이어 볼 수 있어요.</div>
        ) : (status.trades || []).length === 0 ? (
          <div className="sd-log-empty">아직 체결이 없어요. 조건을 낮추거나(익절·손절 0.3~1%) 변동성 큰 종목·리플레이를 써 봐요.</div>
        ) : (
          <div className="sd-log-rows">
            {status.trades.map((t) => (
              <div key={t.id} className="sd-log-row">
                <span className="sd-log-t num">{String(t.ts).slice(11, 19)}</span>
                <span className={"sd-log-side " + (SIDE_CLS[t.side] || "")}>{SIDE_KO[t.side] || t.side}</span>
                <span className="sd-log-px num">{fmtPrice(t.price)} <small>{quote}</small> · {fmtQty(t.qty)} <small>{base}</small></span>
                <span className={"sd-log-pnl num " + tone(t.return_at_trade)}>{pct(t.return_at_trade)}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── 매크로 등록 — 내가 만든 매크로 한 줄이 주인공, 그 아래 네 가지 동작(리더보드 등록이 노랑), 그 아래 실행기 안내 상자 ──
export function StudioOutcomes({ macro, valErr, strategyEntry, strategyExtra = [], canRegister, onRegister, onShare, shareBusy = false }) {
  const { quickRun, downloadMacro, launching, error } = useMacroActions(macro);
  const futures = macro.position_side === "short" || macro.leverage > 1;
  return (
    <div className="sd-outcomes">
      <div className="sd-macro">
        <StrategyDetails entry={strategyEntry} extra={strategyExtra} />
      </div>
      <div className="sd-actions">
        <button
          type="button"
          onClick={() => onRegister?.()}
          disabled={!onRegister || !canRegister || !!valErr}
          title={!canRegister ? "조건이 바뀌었어요 — 다시 테스트한 뒤 등록할 수 있어요" : undefined}
          className="btn btn-l btn-primary"
        >
          리더보드 등록
        </button>
        <button type="button" onClick={quickRun} disabled={!!valErr || launching} className="btn btn-l btn-secondary">
          {launching ? "실행 준비 중…" : "빠른 실행"}
        </button>
        <button type="button" onClick={downloadMacro} disabled={!!valErr} className="btn btn-l btn-secondary">매크로 파일 내려받기</button>
        <button type="button" onClick={onShare} disabled={!!valErr || shareBusy} className="btn btn-l btn-secondary">
          {shareBusy ? "저장 중…" : "공유 링크 보기"}
        </button>
      </div>
      {!canRegister && <p className="sd-note text-amber-700">조건이 바뀌었어요 — 다시 테스트한 뒤 등록할 수 있어요.</p>}
      {error && <p className="sd-note is-error" role="alert">오류: {error}</p>}

      {/* 실행기 실거래 안내 — 원래 페이퍼 다음 단계에 있던 호박색 상자 그대로. */}
      <div className="alert alert-warn sd-runner space-y-3">
        <div className="t-title">동작 검증 완료 → 매크로 실행기로 실거래</div>
        <p className="t-small">
          터미널·파이썬 설치 없이 <b>껄무새 매크로 실행기</b>(프로그램)에 이 매크로 파일을 넣고 돌려요.
          실행 현황과 원격 종료는 <b>마이페이지</b>에서 확인해요.
        </p>
        <div className="pt-3 border-t border-amber-700/30 space-y-2">
          <p className="t-small font-bold">진행 방법</p>
          <ol className="t-small list-decimal pl-4 space-y-1">
            <li>위 버튼으로 <b>매크로 파일(.ggm.json)</b>을 내려받아요.</li>
            <li>마이페이지에서 <b>껄무새 회원 키</b>를 복사해요(계정당 1개).</li>
            <li>매크로 실행기를 열어 ①파일 ②실거래 여부 ③API 키 ④회원 키를 넣고 시작해요.</li>
          </ol>
          <p className="t-small font-bold pt-1">
            주의: 실행기는 <u>실제로 주문을 실행해요</u> (기본값: 바이낸스 테스트넷 = 가짜 자금)
          </p>
          <ul className="t-small list-disc pl-4 space-y-1">
            <li>{futures ? "숏·레버리지 매크로라 USDT-M 선물로 실행돼요." : "롱·1배 매크로라 현물(spot)로 실행돼요."}</li>
            <li>익절·손절·일일 최대손실·최대 보유시간·재진입 금지가 함께 적용돼요.</li>
            <li>실제 자금은 실행기에서 <b>실거래(메인넷) 체크</b>를 켜야 움직여요(경고 확인 단계 있음).</li>
            <li>API 키는 실행기 로컬에서만 쓰고 서버로 전송·저장하지 않아요. 출금 기능은 없어요.</li>
          </ul>
        </div>
      </div>
    </div>
  );
}
