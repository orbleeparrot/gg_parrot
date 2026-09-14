// 매크로 트레이딩 카드 — 내가 만든 매크로를 산출물 한 장으로. 매크로 등록 탭과 공유 다이얼로그가 같은 카드를 쓴다.
// 왼쪽 면은 "무엇인지"(종목 · 포지션/시장 · 매매 방식 · 조건 문장 · 사양표), 오른쪽 면은 "테스트에서 낸 것"(수익률 · 자산곡선 · MDD/승률/매매).
// 폭이 620px 아래면 결과 면이 카드 아래 한 줄 띠로 내려온다(MacroCard.css 의 컨테이너 쿼리).
import { Fragment, useLayoutEffect, useRef } from "react";
import CoinIcon from "./CoinIcon.jsx";
import { quoteOf, baseOf } from "../lib/format.js";
import { RULE_TYPES, CANDLE_INTERVALS } from "../lib/macro.js";
import { leaderboardStrategy } from "../lib/leaderboardStrategy.js";
import { strategyPhrases } from "../lib/strategyText.js";
import { portfolioTitle, portfolioWeight } from "../lib/portfolio.js";
import "./MacroCard.css";

const pct = (v, digits = 2) => `${v >= 0 ? "+" : ""}${Number(v).toFixed(digits)}%`;
const tone = (v) => (v >= 0 ? "is-up" : "is-down");

// 카드 안의 자산곡선 미리보기 — 백테스트 탭의 큰 차트와 같은 곡선을 선 하나로. 파선이 본전. 색은 최종 수익률의 등락색.
function CardSpark({ curve, up }) {
  if (!Array.isArray(curve) || curve.length < 2) return null;
  const W = 300, H = 64, PAD = 3;
  const step = Math.max(1, Math.ceil(curve.length / 160));
  const pts = curve.filter((_, i) => i % step === 0 || i === curve.length - 1).map((p) => Number(p.equity));
  const start = Number(curve[0].equity);
  let lo = Math.min(start, ...pts);
  let hi = Math.max(start, ...pts);
  if (!(hi > lo)) hi = lo + 1;
  const x = (i) => ((i / (pts.length - 1)) * W).toFixed(1);
  const y = (v) => (PAD + (1 - (v - lo) / (hi - lo)) * (H - PAD * 2)).toFixed(1);
  const line = pts.map((v, i) => `${i ? "L" : "M"}${x(i)} ${y(v)}`).join(" ");
  return (
    <svg className={"sd-card-spark " + (up ? "is-up" : "is-down")} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
      {/* 색·선 두께는 속성으로 직접 준다 — 카드를 이미지로 뜰 때(html-to-image) SVG 자식에는 CSS 가 실리지 않아 검은 상자가 됐다. currentColor 는 svg 의 color(등락색)를 따른다. */}
      <path className="sd-card-spark-area" d={`${line} L${W} ${H} L0 ${H} Z`} fill="currentColor" opacity="0.12" />
      <path className="sd-card-spark-base" d={`M0 ${y(start)} H${W}`} fill="none" strokeWidth="1" strokeDasharray="3 3" opacity="0.7" vectorEffect="non-scaling-stroke" style={{ stroke: "rgb(var(--c-slate-400))" }} />
      <path className="sd-card-spark-line" d={line} fill="none" stroke="currentColor" strokeWidth="2" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

const fmtN = (v, digits = 8) => Number(v).toLocaleString("en-US", { maximumFractionDigits: digits });
// 데이터 출처 — 서버의 source 값(cache · binance · binance-futures · synthetic)을 사람 말로.
const SOURCE_KO = { cache: "바이낸스 (캐시)", binance: "바이낸스 현물", "binance-futures": "바이낸스 선물(USDT-M)", synthetic: "합성 데이터 (오프라인)" };

// 카드의 사양표 — 매크로 파일에 실제로 들어가는 조건만(종목 · 봉 간격 · 자금 · 위험 관리 · 비용). 없는 값은 만들지 않는다.
function macroFacts({ macro, symbol, symbols, result, futures }) {
  const p = macro.params || {};
  const risk = macro.risk || {};
  const fees = macro.fees || {};
  const quote = quoteOf(symbol);
  const interval = CANDLE_INTERVALS.find((i) => i.value === macro.candle_interval)?.label || "";
  const facts = [];
  if (symbols.length > 1) facts.push({ k: `종목 ${symbols.length}개 · 자금 균등`, v: symbols.join(" · "), num: true, wide: true });
  facts.push({ k: "봉 간격", v: interval ? `${interval}봉` : "—" });
  if (macro.rule_type === "C") {
    facts.push({ k: "자금", v: p.amount_per_buy != null ? `회당 ${fmtN(p.amount_per_buy)} ${quote} · ${fmtN(p.interval_days || 0)}일마다` : "—" });
  } else {
    const capital = p.initial_capital ?? result?.initial_capital;
    const ratio = risk.invest_ratio != null ? `${fmtN(Number(risk.invest_ratio) * 100)}% 투입` : "";
    const per = symbols.length > 1 ? `종목당 ${portfolioWeight(symbols.length).fraction}` : "";
    facts.push({ k: "자금", v: [capital != null ? `${fmtN(capital, 2)} ${quote}` : "", ratio, per].filter(Boolean).join(" · ") || "—" });
  }
  const riskParts = [
    risk.stop_loss_pct != null ? `손절 -${fmtN(risk.stop_loss_pct)}%` : "",
    risk.daily_max_loss_pct != null ? `일일 최대손실 -${fmtN(risk.daily_max_loss_pct)}%` : "",
    risk.max_holding_hours != null ? `최대 보유 ${fmtN(risk.max_holding_hours)}시간` : "",
    risk.cooldown_minutes ? `재진입 금지 ${fmtN(risk.cooldown_minutes)}분` : "",
  ].filter(Boolean);
  facts.push({ k: "위험 관리", v: riskParts.length ? riskParts.join(" · ") : "손절 없음" });
  const feeParts = [
    fees.commission_pct != null ? `수수료 ${fmtN(fees.commission_pct)}%` : "",
    fees.slippage_pct != null ? `슬리피지 ${fmtN(fees.slippage_pct)}%` : "",
    futures && fees.funding_pct ? `펀딩 ${fmtN(fees.funding_pct)}%/일` : "",
  ].filter(Boolean);
  if (feeParts.length) facts.push({ k: "비용", v: feeParts.join(" · ") });
  return facts;
}

// 조건 문장 조판 — 구는 세로 괘선으로 나누고, 사용자가 정한 숫자(익절·손절·가격·기간·σ·k·배수)는 고정폭 굵게 노랑.
// 한 줄로만 놓는다: 폭이 모자라면 들어갈 때까지 글자 크기를 줄인다(줄바꿈도 말줄임도 없다). title 에 전문이 있다.
function useFitLine(ref, text) {
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    let raf = 0;
    const fit = () => {
      const max = parseFloat(getComputedStyle(el).getPropertyValue("--sd-strategy-max")) || 19;
      let size = max;
      el.style.fontSize = `${size}px`;
      while (size > 8 && el.scrollWidth > el.clientWidth) {
        size -= 0.5;
        el.style.fontSize = `${size}px`;
      }
    };
    const schedule = () => {
      if (typeof requestAnimationFrame === "undefined") { fit(); return; }
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(fit);
    };
    fit();
    // 폭이 바뀔 때(분할 바 · 창 크기)와 웹폰트가 늦게 들어와 글이 넓어질 때 다시 맞춘다 — 폰트 로드는 상자 크기를 바꾸지 않아 ResizeObserver 만으로는 못 잡는다.
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(schedule);
    observer?.observe(el);
    const fonts = typeof document !== "undefined" ? document.fonts : null;
    fonts?.ready?.then(schedule, () => {});
    fonts?.addEventListener?.("loadingdone", schedule);
    return () => {
      if (typeof cancelAnimationFrame !== "undefined") cancelAnimationFrame(raf);
      observer?.disconnect();
      fonts?.removeEventListener?.("loadingdone", schedule);
    };
  }, [ref, text]);
}

function StrategyText({ text }) {
  const ref = useRef(null);
  const phrases = strategyPhrases(text, { dropLeverage: true }); // 레버리지는 머리의 시장 태그가 보여 준다
  useFitLine(ref, text);
  return (
    <p ref={ref} className="sd-card-strategy" title={text || ""}>
      {phrases.length ? phrases.map((tokens, i) => (
        <span key={i} className="sd-card-phrase">
          {tokens.map((token, j) => token.t === "num" ? <b key={j} className="num">{token.v}</b> : <Fragment key={j}>{token.v}</Fragment>)}
        </span>
      )) : <span className="sd-card-phrase">상세 정보 없음</span>}
    </p>
  );
}


// logoProxy — 공유 다이얼로그처럼 카드를 이미지로 뜰 때는 로고를 우리 서버(/api/coin-logo)로 받아 같은 출처가 되게 한다.
export default function MacroCard({ macro, result, perSymbol = [], strategyEntry, periodLabel = "", dataSource = "", symbols = [], logoProxy = false, className = "" }) {
  const futures = macro.position_side === "short" || macro.leverage > 1;
  const leverage = macro.leverage || 1;
  const symbol = strategyEntry?.symbol || macro.symbol || "";
  const details = leaderboardStrategy(strategyEntry || { symbol, macro, locked: false });
  const side = details?.side || macro.position_side || null;
  const sideLabel = { long: "롱", short: "숏", switch: "롱 → 숏" }[side] || "—";
  const marginLabel = macro.margin_mode === "cross" ? "교차" : "격리";
  const marketLabel = futures ? `선물 · ${marginLabel} ${leverage}배` : "현물 · 1배";
  const ruleLabel = RULE_TYPES[macro.rule_type]?.label || macro.rule_type || "";
  const facts = macroFacts({ macro, symbol, symbols, result, futures });
  const ret = Number(result?.final_return_pct ?? 0);
  // 여러 종목이면 포트폴리오 카드 — 머리에 로고를 겹치고 티커를 나란히, 결과 면에는 합산 아래 종목별 줄.
  const multi = symbols.length > 1;
  const stack = multi ? symbols.slice(0, 5) : [symbol];
  const perRows = multi && Array.isArray(perSymbol)
    ? symbols.map((sym) => perSymbol.find((row) => row && row.symbol === sym)).filter(Boolean)
    : [];
  return (
    <article className={"sd-card" + (className ? ` ${className}` : "")} aria-label="내 매크로 카드">
      <div className="sd-card-in">
      {/* 왼쪽 — 이 매크로가 무엇인지: 종목 · 포지션/시장 · 매매 방식 · 조건 문장 · 사양표 */}
      <div className="sd-card-spec">
        <div className="sd-card-head">
          {multi ? (
            <span className="sd-card-coins" aria-hidden="true">
              {stack.map((sym) => <CoinIcon key={sym} symbol={sym} size={40} className="sd-card-coin" alt="" proxy={logoProxy} />)}
            </span>
          ) : (
            <CoinIcon symbol={symbol} size={44} className="sd-card-coin" alt="" proxy={logoProxy} />
          )}
          <div className="sd-card-id">
            <div className="sd-card-ticker num"><strong>{multi ? portfolioTitle(symbols) : baseOf(symbol)}</strong><small>{quoteOf(symbol)}</small></div>
            <div className="sd-card-rule" title="매매 방식">{ruleLabel}{multi ? ` · ${symbols.length}종목 자금 균등` : ""}</div>
          </div>
          <div className="sd-card-tags">
            <span className={"sd-card-side is-" + (side || "unknown")}>{sideLabel}</span>
            <span className="sd-card-tag">{marketLabel}</span>
          </div>
        </div>
        <StrategyText text={details?.description || ""} />
        <dl className="sd-card-facts">
          {facts.map((f) => (
            <div key={f.k} className={f.wide ? "is-wide" : undefined}><dt>{f.k}</dt><dd className={f.num ? "num" : undefined}>{f.v}</dd></div>
          ))}
        </dl>
      </div>
      {/* 오른쪽 — 이 매크로가 테스트에서 낸 것: 수익률 · 자산곡선 · MDD/승률/매매 */}
      <div className="sd-card-proof">
        <span className="sd-card-eyebrow num" aria-hidden="true">GGPARROT MACRO</span>
        <span className="sd-card-k">{multi ? "합산 수익률" : "백테스트 수익률"}{periodLabel ? ` · ${periodLabel}` : ""}</span>
        <strong className={"sd-card-ret num " + tone(ret)}>{pct(ret)}</strong>
        <CardSpark curve={result?.equity_curve} up={ret >= 0} />
        {result && (
          <dl className="sd-card-stats">
            <div><dt>MDD</dt><dd className="num is-down">-{Number(result.mdd_pct).toFixed(1)}%</dd></div>
            <div><dt>승률</dt><dd className="num">{Number(result.win_rate_pct).toFixed(1)}%</dd></div>
            <div><dt>매매</dt><dd><span className="num">{result.total_trades}</span>회</dd></div>
          </dl>
        )}
        {perRows.length > 0 && (
          <ul className="sd-card-per" aria-label="종목별 결과">
            {perRows.map((row) => (
              <li key={row.symbol}>
                <CoinIcon symbol={row.symbol} size={16} alt="" proxy={logoProxy} />
                <span className="sd-card-per-sym num">{baseOf(row.symbol)}</span>
                <span className={"sd-card-per-ret num " + tone(Number(row.final_return_pct))}>{pct(Number(row.final_return_pct))}</span>
                <span className="sd-card-per-mdd num">MDD -{Number(row.mdd_pct).toFixed(1)}%</span>
              </li>
            ))}
          </ul>
        )}
        {dataSource && <span className="sd-card-src">데이터 · {SOURCE_KO[dataSource] || dataSource}</span>}
      </div>
      </div>
    </article>
  );
}
