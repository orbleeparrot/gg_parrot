import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import InfoTooltip from "./InfoTooltip.jsx";

const KIMCHI_POLL_MS = Number(import.meta.env?.VITE_KIMCHI_POLL_MS) || 15000;
const FEAR_GREED_POLL_MS = Number(import.meta.env?.VITE_FEARGREED_POLL_MS) || 600000;
const HANGANG_POLL_MS = Number(import.meta.env?.VITE_HANGANG_POLL_MS) || 300000;
const COINS = ["BTC", "ETH", "XRP", "SOL"];
const MARKET_HELP = {
  kimchi: "국내외 거래소의 같은 코인 가격 차이예요. +는 김프, -는 역프를 뜻해요.",
  fear: "시장 심리를 0~100으로 나타낸 지수예요. 낮을수록 공포, 높을수록 탐욕에 가까워요.",
  water: "서울 한강의 최근 관측 수온이에요.",
};

const EMPTY_STATE = { data: null, loading: true, error: "" };
const fixed2 = (value) => value == null ? "-" : Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const krw = (value) => value == null ? "-" : `${fixed2(value)} 원`;

function waterComment(value) {
  if (value == null) return "";
  if (value >= 20) return "물이 미지근하네요";
  if (value >= 10) return "슬슬 차가워집니다";
  return "오늘은 집이 최고";
}

function fearTone(value) {
  if (value == null) return { text: "text-slate-700", bar: "bg-slate-400" };
  if (value < 25) return { text: "text-red-600", bar: "bg-red-500" };
  if (value < 45) return { text: "text-amber-700", bar: "bg-amber-500" };
  if (value < 55) return { text: "text-slate-700", bar: "bg-slate-400" };
  return { text: "text-green-600", bar: "bg-green-600" };
}

function useMarketIndicators(symbol) {
  const [kimchi, setKimchi] = useState(EMPTY_STATE);
  const [fearGreed, setFearGreed] = useState(EMPTY_STATE);
  const [hangang, setHangang] = useState(EMPTY_STATE);

  useEffect(() => {
    let alive = true;
    let inFlight = false;
    async function tick() {
      if (inFlight) return;
      inFlight = true;
      try {
        const data = await api.kimchiPremium(symbol);
        if (!alive) return;
        setKimchi({ data: data?.ok ? data : null, loading: false, error: data?.ok ? "" : data?.error || "정보 없음" });
      } catch (reason) {
        if (alive) setKimchi((current) => ({ ...current, loading: false, error: String(reason.message || reason) }));
      } finally {
        inFlight = false;
      }
    }
    setKimchi({ data: null, loading: true, error: "" });
    void tick();
    const timer = window.setInterval(tick, KIMCHI_POLL_MS);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [symbol]);

  useEffect(() => {
    let alive = true;
    let inFlight = false;
    async function tick() {
      if (inFlight) return;
      inFlight = true;
      try {
        const data = await api.fearGreed();
        if (!alive) return;
        setFearGreed({ data: data?.ok ? data : null, loading: false, error: data?.ok ? "" : "정보 없음" });
      } catch (reason) {
        if (alive) setFearGreed((current) => ({ ...current, loading: false, error: String(reason.message || reason) }));
      } finally {
        inFlight = false;
      }
    }
    void tick();
    const timer = window.setInterval(tick, FEAR_GREED_POLL_MS);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    let alive = true;
    let inFlight = false;
    async function tick() {
      if (inFlight) return;
      inFlight = true;
      try {
        const data = await api.hangangTemp();
        if (!alive) return;
        setHangang({ data: data?.ok ? data : null, loading: false, error: data?.ok ? "" : "정보 없음" });
      } catch (reason) {
        if (alive) setHangang((current) => ({ ...current, loading: false, error: String(reason.message || reason) }));
      } finally {
        inFlight = false;
      }
    }
    void tick();
    const timer = window.setInterval(tick, HANGANG_POLL_MS);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);

  return { kimchi, fearGreed, hangang };
}

function MetricGlyph({ type }) {
  const common = { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round", strokeLinejoin: "round", focusable: "false" };
  if (type === "pulse") return <svg {...common}><path d="M3 12h4l2-5 4 10 2-5h6" /></svg>;
  if (type === "kimchi") return <svg {...common}><path d="M4 8h12" /><path d="m13 5 3 3-3 3" /><path d="M20 16H8" /><path d="m11 13-3 3 3 3" /></svg>;
  if (type === "fear") return <svg {...common}><path d="M5 16a7 7 0 0 1 14 0" /><path d="m12 16 4-5" /><path d="M7 19h10" /></svg>;
  if (type === "temperature") return <svg {...common}><path d="M9 14.7V5a3 3 0 0 1 6 0v9.7a5 5 0 1 1-6 0Z" /><path d="M12 8v8" /></svg>;
  return <svg {...common}><path d="M3 15c2-2 4-2 6 0s4 2 6 0 4-2 6 0" /><path d="M5 10c2-2 4-2 6 0s4 2 6 0" /></svg>;
}

function MetricChip({ type, label, value, tone = "text-slate-900", stale }) {
  return (
    <span className="market-indicator-chip">
      <span className={`market-indicator-icon is-${type}`} aria-hidden="true"><MetricGlyph type={type} /></span>
      <span className="market-indicator-label">{label}</span>
      <strong className={`num ${tone}`}>{value}</strong>
      {stale ? <span className="market-stale-dot" title="이전 갱신값" aria-label="이전 갱신값" /> : null}
    </span>
  );
}

export default function MarketContext() {
  const [symbol, setSymbol] = useState("BTC");
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const triggerRef = useRef(null);
  const { kimchi, fearGreed, hangang } = useMarketIndicators(symbol);

  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event) => {
      if (!rootRef.current?.contains(event.target)) setOpen(false);
    };
    const onKeyDown = (event) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const premium = kimchi.data?.premium_pct;
  const premiumTone = premium == null ? "text-slate-500" : premium >= 0 ? "text-red-600" : "text-blue-800";
  const premiumValue = premium == null ? (kimchi.loading ? "…" : "—") : `${premium >= 0 ? "+" : ""}${premium.toFixed(2)}%`;
  const premiumLabel = kimchi.data?.label || (premium == null ? "" : premium >= 0 ? "김프" : "역프");
  const fearValue = fearGreed.data?.value;
  const fear = fearTone(fearValue);
  const fearLabel = fearGreed.data?.classification_ko || "";
  const water = hangang.data?.temperature;
  const waterValue = water == null ? (hangang.loading ? "…" : "—") : `${water.toFixed(1)}°C`;

  return (
    <div ref={rootRef} className="market-context-topbar" data-tour="market-briefing">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="market-pulse-trigger"
        aria-expanded={open}
        aria-controls="market-context-panel"
        aria-label={`시장 참고 지표 상세 ${open ? "닫기" : "열기"}`}
      >
        <span className="market-pulse-title">
          <strong>{open ? "시장 브리핑 닫기" : "시장 브리핑 보기"}</strong>
          <span className="market-pulse-chevron" aria-hidden="true">
            <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" focusable="false"><path d={open ? "m4 6 4 4 4-4" : "m4 10 4-4 4 4"} /></svg>
          </span>
        </span>
        <span className="market-indicator-scroll">
          <MetricChip type="kimchi" label="김프" value={`${premiumValue}${premiumLabel ? ` (${premiumLabel})` : ""}`} tone={premiumTone} stale={kimchi.data?.fx_is_fallback} />
          <MetricChip type="fear" label="공포·탐욕" value={fearValue == null ? (fearGreed.loading ? "…" : "—") : `${fearValue} ${fearLabel}`} tone={fear.text} stale={fearGreed.data?.stale} />
          <MetricChip type="water" label="한강" value={waterValue} tone="text-sky-800" stale={hangang.data?.stale} />
          <span className="market-updated-at">
            {kimchi.data?.updated_at ? <>갱신 <span className="num">{kimchi.data.updated_at.slice(11, 19)}</span> UTC</> : "갱신 확인 중"}
          </span>
        </span>
      </button>

      {open ? (
        <section id="market-context-panel" className="market-context-panel" aria-label="시장 참고 지표 상세">
          <header>
            <div><span className="num">MARKET PULSE</span><strong>오늘의 시장 참고 지표</strong></div>
            <p className="market-disclaimer"><span aria-hidden="true">⚠️</span> 투자 조언이나 매매 신호가 아닌 참고 정보</p>
          </header>

          <div className="market-detail-row is-kimchi">
            <div className="market-detail-label">
              <span className="market-indicator-icon is-kimchi" aria-hidden="true"><MetricGlyph type="kimchi" /></span>
              <strong>김치 프리미엄</strong><InfoTooltip text={MARKET_HELP.kimchi} placement="top" />
            </div>
            <div className="market-detail-main">
              <select value={symbol} onChange={(event) => setSymbol(event.target.value)} className="field field-sm h-8 w-auto py-0 t-caption" aria-label="김치 프리미엄 기준 종목">
                {COINS.map((coin) => <option key={coin} value={coin}>{coin}</option>)}
              </select>
              <strong className={`t-title num ${premiumTone}`}>{premiumValue}</strong>
              {kimchi.data ? <span className="text-slate-500">({premiumLabel})</span> : <span className="text-slate-500">{kimchi.error || "불러오는 중…"}</span>}
            </div>
            <p className="market-detail-note">
              {kimchi.data ? (
                <>
                  <span><b>업비트</b> <span className="num">{krw(kimchi.data.upbit_price_krw)}</span></span>
                  <span><b>바이낸스 환산</b> <span className="num">{krw(kimchi.data.binance_price_krw)}</span></span>
                  <span className="num market-detail-formula">${fixed2(kimchi.data.binance_price_usdt)} × {fixed2(kimchi.data.usdkrw)}</span>
                  {kimchi.data.fx_is_fallback ? <span className="text-amber-700">환율 조회 실패로 근사값을 사용하고 있어요.</span> : null}
                </>
              ) : "국내외 가격 차이를 확인해요."}
            </p>
          </div>

          <div className="market-detail-row is-fear">
            <div className="market-detail-label">
              <span className="market-indicator-icon is-fear" aria-hidden="true"><MetricGlyph type="fear" /></span>
              <strong>공포·탐욕 지수</strong><InfoTooltip text={MARKET_HELP.fear} placement="top" />
            </div>
            <div className="market-detail-main">
              <strong className={`t-title num ${fear.text}`}>{fearValue ?? "—"}</strong>
              <span className={`font-bold ${fear.text}`}>{fearLabel || fearGreed.error || "불러오는 중…"}</span>
              <span className="market-fear-track"><span className={fear.bar} style={{ width: `${fearValue == null ? 0 : Math.max(2, Math.min(fearValue, 100))}%` }} /></span>
            </div>
            <p className="market-detail-note"><span>시장 전체 기준 · 종목별 지표 아님</span><span><span className="num">0</span> 공포 ↔ <span className="num">100</span> 탐욕</span></p>
          </div>

          <div className="market-detail-row is-water">
            <div className="market-detail-label">
              <span className="market-indicator-icon is-water" aria-hidden="true"><MetricGlyph type="water" /></span>
              <strong>한강 수온</strong><InfoTooltip text={MARKET_HELP.water} placement="top" />
            </div>
            <div className="market-detail-main">
              <strong className="t-title num text-sky-800">{waterValue}</strong>
              <span className="text-slate-700">{water == null ? hangang.error || "불러오는 중…" : waterComment(water)}</span>
              {water != null ? <span className="market-temperature-mark text-sky-800" aria-hidden="true"><MetricGlyph type="temperature" /></span> : null}
            </div>
            <p className="market-detail-note">{hangang.data ? `${hangang.data.location}${hangang.data.observed_label ? ` · ${hangang.data.observed_label} 기준` : ""}` : "실시간 관측값을 불러오고 있어요."}</p>
          </div>
        </section>
      ) : null}
    </div>
  );
}
