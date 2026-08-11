import { lazy, Suspense, useEffect, useState } from "react";
import { RULE_TYPES, withTypeDefaults } from "../lib/macro.js";

const ResultView = lazy(() => import("./ResultView.jsx"));
const PaperPanelView = lazy(() =>
  import("./PaperPanel.jsx").then((module) => ({ default: module.PaperPanelView }))
);
const GuidePage = lazy(() => import("../pages/Guide.jsx"));
const CandleChart = lazy(() => import("./CandleChart.jsx"));

function SceneLoading({ label }) {
  return <div className="py-10 t-small text-slate-500" role="status">{label}</div>;
}

const STRATEGY_COPY = {
  A: "정한 수익과 손실에서 정리하고 다시 진입해요.",
  B: "살 가격과 팔 가격을 미리 정해 범위 안에서 거래해요.",
  C: "정해진 간격마다 같은 금액을 나눠 사요.",
  D: "가격 범위를 여러 칸으로 나눠 오갈 때마다 사고팔아요.",
  E: "오르는 동안 들고 가다가 고점에서 밀리면 정리해요.",
  F: "RSI로 과매도와 과매수 구간을 판단해요.",
  G: "볼린저밴드 안팎의 움직임으로 진입과 정리를 정해요.",
  H: "가격이 내릴 때 추가 매수해 평균 매수가를 낮춰요.",
  I: "전날 변동폭을 기준으로 강한 돌파를 따라가요.",
  J: "짧은 이동평균과 긴 이동평균의 교차를 따라가요.",
  K: "하락하면 롱을 줄이고 숏으로 전환해 방어해요.",
};

const select = (key, label, question, options, help) => ({
  key,
  label,
  question,
  options,
  help,
  type: "select",
});

const number = (key, label, question, suffix, help, constraints = {}) => ({
  key,
  label,
  question,
  suffix,
  help,
  type: "number",
  min: constraints.min ?? 0.0001,
  max: constraints.max,
  step: constraints.step || "any",
});

const CONDITION_FIELDS = {
  A: [
    number("take_profit_pct", "익절 기준", "몇 % 오르면 이익을 확정할까요?", "%", "처음에는 3~5%처럼 단순한 값으로 확인해도 돼요."),
  ],
  B: [
    number("buy_price", "살 가격", "어느 가격에서 살까요?", "USDT", "현재가보다 낮은 가격을 정하는 지정가 전략이에요."),
    number("sell_price", "팔 가격", "어느 가격에서 팔까요?", "USDT", "살 가격보다 높은 값이어야 해요."),
  ],
  C: [
    number("amount_per_buy", "한 번에 살 금액", "한 번에 얼마씩 나눠 살까요?", "USDT", "전체 시작 자금 안에서 반복할 수 있는 금액을 정해요."),
    number("interval_days", "매수 간격", "며칠마다 한 번씩 살까요?", "일", "기간을 짧게 잡을수록 매수 횟수가 늘어요.", { step: 1 }),
  ],
  D: [
    number("lower_price", "가격 범위 하단", "어디부터 나눠 살까요?", "USDT", "그리드가 동작할 가장 낮은 가격이에요."),
    number("upper_price", "가격 범위 상단", "어디까지 나눠 팔까요?", "USDT", "하단보다 높은 가격을 입력해요."),
    number("grid_count", "그리드 칸 수", "가격 범위를 몇 칸으로 나눌까요?", "칸", "칸이 많으면 거래 간격이 촘촘해져요.", { min: 2, max: 200, step: 1 }),
  ],
  E: [
    number("activation_profit", "추적 시작 수익", "몇 % 오른 뒤부터 고점을 따라갈까요?", "%", "이 수익에 닿기 전에는 추적 손절이 움직이지 않아요.", { min: 0 }),
    number("trail_percent", "허용 하락폭", "고점에서 몇 % 밀리면 정리할까요?", "%", "작을수록 빠르게 이익을 확정해요."),
  ],
  F: [
    number("entry_threshold", "진입 RSI", "RSI가 얼마 이하일 때 들어갈까요?", "", "보통 낮을수록 많이 팔린 구간으로 봐요.", { min: 0, max: 100 }),
    number("exit_threshold", "정리 RSI", "RSI가 얼마 이상일 때 정리할까요?", "", "진입 기준보다 높은 값이어야 해요.", { min: 0, max: 100 }),
    number("rsi_period", "RSI 계산 기간", "몇 개 봉으로 RSI를 계산할까요?", "봉", "대표적인 시작값은 14예요.", { min: 2, max: 200, step: 1 }),
  ],
  G: [
    number("bb_period", "평균 계산 기간", "몇 개 봉으로 밴드의 중심을 잡을까요?", "봉", "대표적인 시작값은 20이에요.", { min: 2, max: 200, step: 1 }),
    number("bb_std", "밴드 폭", "평균에서 얼마나 떨어진 곳을 밴드로 볼까요?", "σ", "값이 클수록 밴드가 넓어져 신호가 줄어요.", { step: 0.1 }),
    select("strategy", "밴드 사용법", "밴드 안으로 돌아올 때와 돌파할 때 중 무엇을 볼까요?", [
      { value: "reversion", label: "밴드 안으로 되돌아오기" },
      { value: "breakout", label: "밴드 밖으로 돌파하기" },
    ], "횡보장은 되돌림, 강한 추세장은 돌파 방식이 더 자주 쓰여요."),
  ],
  H: [
    number("base_order_size", "첫 매수 금액", "처음에는 얼마를 살까요?", "USDT", "추가 매수까지 고려해 시작 금액을 작게 잡아요."),
    number("safety_order_size", "첫 추가매수 금액", "가격이 내리면 얼마를 더 살까요?", "USDT", "계속 하락하면 필요한 자금이 빠르게 늘 수 있어요."),
    number("max_safety_orders", "최대 추가매수", "추가 매수를 몇 번까지 허용할까요?", "회", "횟수가 많을수록 필요한 최대 자금도 함께 커져요.", { min: 0, max: 50, step: 1 }),
  ],
  I: [
    number("k", "돌파 기준 계수", "전날 변동폭의 몇 배를 돌파선에 더할까요?", "k", "대표적인 시작값은 0.5예요.", { max: 2, step: 0.1 }),
    select("exit_mode", "정리 기준", "돌파 뒤 언제 정리할까요?", [
      { value: "next_open", label: "다음 봉 시작 가격" },
      { value: "trailing", label: "고점에서 밀릴 때" },
      { value: "take_profit", label: "정한 익절률에 닿을 때" },
    ], "선택한 방식의 세부값은 전체 빌더에서 더 조정할 수 있어요."),
  ],
  J: [
    number("fast_period", "짧은 이동평균", "최근 몇 개 봉의 흐름을 빠르게 볼까요?", "봉", "긴 이동평균보다 작은 값이어야 해요.", { min: 1, max: 400, step: 1 }),
    number("slow_period", "긴 이동평균", "큰 흐름은 몇 개 봉으로 볼까요?", "봉", "짧은 이동평균보다 큰 값이어야 해요.", { min: 2, max: 400, step: 1 }),
  ],
  K: [
    number("drop_trigger_pct", "방어 시작 하락폭", "몇 % 내리면 하락 방어를 시작할까요?", "%", "롱 진입가를 기준으로 계산해요."),
    number("partial_exit_pct", "먼저 팔 비율", "들고 있던 롱의 몇 %를 먼저 정리할까요?", "%", "0%보다 크고 100% 이하여야 해요.", { max: 100 }),
    number("short_stop_loss_pct", "숏 손절 기준", "숏 전환 뒤 몇 % 반등하면 손절할까요?", "%", "숏 손실은 커질 수 있어 반드시 제한해요."),
  ],
};

export function getConditionFields(ruleType, form = {}) {
  const fields = [...(CONDITION_FIELDS[ruleType] || CONDITION_FIELDS.A)];
  if (ruleType === "I" && form.exit_mode === "trailing") {
    fields.push(number("trail_percent", "고점 허용 하락폭", "고점에서 몇 % 밀리면 정리할까요?", "%", "작을수록 더 빠르게 정리해요."));
  }
  if (ruleType === "I" && form.exit_mode === "take_profit") {
    fields.push(number("take_profit", "익절 기준", "돌파 뒤 몇 % 오르면 정리할까요?", "%", "선택한 익절 방식에 필요한 값이에요."));
  }
  return fields;
}

export function conditionError(field, form) {
  const value = form[field.key];
  if (field.type === "select") return value ? "" : "한 가지를 선택해 주세요.";
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric < field.min) return `${field.min} 이상으로 입력해 주세요.`;
  if (field.max != null && numeric > field.max) return `${field.max} 이하로 입력해 주세요.`;
  if (Number(field.step) === 1 && !Number.isInteger(numeric)) return "정수로 입력해 주세요.";
  if (field.key === "sell_price" && numeric <= Number(form.buy_price)) return "팔 가격은 살 가격보다 높아야 해요.";
  if (field.key === "upper_price" && numeric <= Number(form.lower_price)) return "상단 가격은 하단 가격보다 높아야 해요.";
  if (field.key === "exit_threshold" && numeric <= Number(form.entry_threshold)) return "정리 RSI는 진입 RSI보다 높아야 해요.";
  if (field.key === "slow_period" && numeric <= Number(form.fast_period)) return "긴 이동평균은 짧은 이동평균보다 길어야 해요.";
  return "";
}

function ruleSentence(form) {
  const coin = (form.symbol || "BTCUSDT").split(",")[0].trim().replace(/USDT$/i, "") || "BTC";
  const type = form.rule_type;
  if (type === "A") return `${coin}가 ${form.take_profit_pct}% 오르면 이익을 확정하고, ${form.stop_loss_pct}% 내리면 손실을 제한해요.`;
  if (type === "C") return `${coin}를 ${form.interval_days}일마다 ${Number(form.amount_per_buy || 0).toLocaleString()} USDT씩 나눠 사요.`;
  if (type === "J") return `${coin}의 ${form.fast_period}봉 평균이 ${form.slow_period}봉 평균을 위로 지나면 진입해요.`;
  return `${coin}에 ${RULE_TYPES[type]?.label?.replace(/^[A-K] · /, "") || "선택한 전략"} 규칙을 적용해요.`;
}

export function BuildScene({ form }) {
  return (
    <div className="hero-proof hero-rule-transform" aria-label="매크로가 만들어지는 방식">
      <section>
        <div className="t-caption text-slate-500">거래 아이디어</div>
        <p className="mt-3 t-h4 text-slate-900">“오르면 팔고, 내리면 손실을 제한하고 싶어요.”</p>
      </section>
      <div className="hero-proof-arrow" aria-hidden="true">→</div>
      <section>
        <div className="t-caption text-slate-500">실행할 수 있는 규칙</div>
        <p className="mt-3 t-title text-slate-900">{ruleSentence(form)}</p>
      </section>
      <p className="hero-rule-transform-note t-small text-slate-700">
        코드를 쓰지 않아도 종목과 숫자를 고르면 같은 규칙을 반복해서 시험할 수 있어요.
      </p>
    </div>
  );
}

export function AssetScene({ form, setForm, error, searchError = "", busy = false }) {
  const choices = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"];
  const helpId = "hero-symbol-help";
  const errorId = "hero-symbol-error";
  return (
    <div className="hero-form-block">
      <label htmlFor="hero-symbol" className="block t-small font-semibold text-slate-700 mb-2">차트로 확인할 종목 검색</label>
      <input
        id="hero-symbol"
        type="search"
        className="field t-title num"
        value={form.symbol}
        placeholder="예: BTCUSDT"
        enterKeyHint="search"
        onChange={(event) => setForm((current) => ({ ...current, symbol: event.target.value.toUpperCase() }))}
        onBlur={() => setForm((current) => ({ ...current, symbol: current.symbol.trim().toUpperCase() }))}
        aria-invalid={!!(error || searchError)}
        aria-describedby={[helpId, error || searchError ? errorId : ""].filter(Boolean).join(" ")}
        disabled={busy}
        autoComplete="off"
        spellCheck="false"
      />
      <div className="mt-3 flex flex-wrap gap-2" aria-label="대표 종목 빠른 선택">
        {choices.map((symbol) => (
          <button
            key={symbol}
            type="button"
            className={"chip num " + (form.symbol === symbol ? "border-slate-300 bg-slate-100 text-slate-900" : "")}
            aria-pressed={form.symbol === symbol}
            disabled={busy}
            onClick={() => setForm((current) => ({ ...current, symbol }))}
          >
            {symbol.replace("USDT", "")}
          </button>
        ))}
      </div>
      <p id={helpId} className="mt-5 t-small text-slate-500">종목 코드를 검색하거나 대표 종목을 고르세요. 다음 화면부터 이 종목의 실시간 차트를 왼쪽에 두고 조건을 정해요.</p>
      {error || searchError ? <p id={errorId} className="mt-2 t-small text-red-600" role="alert">{error || searchError}</p> : null}
    </div>
  );
}

export function StrategyScene({ form, setForm }) {
  const recommended = ["A", "C", "J"];
  const choose = (value) => setForm((current) => {
    const next = withTypeDefaults(current, value);
    // H's default safety-order ladder needs 6.3M at worst. The quick guide does
    // not expose capital sizing, so give this preset enough demo capital before
    // handing it to the complete Builder.
    if (value === "H") next.initial_capital = Math.max(Number(next.initial_capital) || 0, 10000000);
    else if (current.rule_type === "H" && ["A", "B", "C"].includes(value)) next.initial_capital = 1000000;
    return next;
  });
  return (
    <div className="hero-form-block">
      <label htmlFor="hero-strategy" className="block t-small font-semibold text-slate-700 mb-2">매매 전략</label>
      <select id="hero-strategy" className="field t-label" value={form.rule_type} onChange={(event) => choose(event.target.value)}>
        {Object.entries(RULE_TYPES).map(([key, value]) => (
          <option key={key} value={key}>{value.label}</option>
        ))}
      </select>
      <p className="mt-4 t-body text-slate-600">{STRATEGY_COPY[form.rule_type]}</p>
      <div className="mt-5 border-t border-slate-200 pt-4">
        <div className="t-caption text-slate-500 mb-2">처음이라면</div>
        <div className="flex flex-wrap gap-2">
          {recommended.map((key) => (
            <button
              key={key}
              type="button"
              className={"chip " + (form.rule_type === key ? "border-slate-300 bg-slate-100 text-slate-900" : "")}
              aria-pressed={form.rule_type === key}
              onClick={() => choose(key)}
            >
              {RULE_TYPES[key].label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

export function ConditionScene({ field, form, setForm, error }) {
  const value = form[field.key];
  const helpId = `hero-field-${field.key}-help`;
  const unitId = `hero-field-${field.key}-unit`;
  const errorId = `hero-field-${field.key}-error`;
  const describedBy = [field.suffix ? unitId : "", helpId, error ? errorId : ""].filter(Boolean).join(" ");
  return (
    <div className="hero-form-block">
      <label htmlFor={`hero-field-${field.key}`} className="block t-h4 text-slate-900">
        {field.question}
      </label>
      <div className="mt-5 flex items-center gap-3">
        {field.type === "select" ? (
          <select
            id={`hero-field-${field.key}`}
            className="field t-label"
            value={value}
            aria-invalid={!!error}
            aria-describedby={describedBy}
            onChange={(event) => setForm((current) => ({ ...current, [field.key]: event.target.value }))}
          >
            {field.options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        ) : (
          <>
            <input
              id={`hero-field-${field.key}`}
              className="field t-h4 num"
              type="number"
              min={field.min}
              max={field.max}
              step={field.step}
              value={value}
              aria-invalid={!!error}
              aria-describedby={describedBy}
              onChange={(event) => setForm((current) => ({ ...current, [field.key]: event.target.value }))}
            />
            {field.suffix ? (
              <span id={unitId} className="t-title text-slate-700 shrink-0">
                <span className="sr-only">단위 </span>{field.suffix}
              </span>
            ) : null}
          </>
        )}
      </div>
      <div id={helpId} className="mt-3 t-small text-slate-500">{field.help}</div>
      {error ? <div id={errorId} className="mt-2 t-small text-red-600" role="alert">{error}</div> : null}
    </div>
  );
}

export function RiskScene({ form, setForm, error }) {
  const helpId = "hero-stop-loss-help";
  const unitId = "hero-stop-loss-unit";
  const errorId = "hero-stop-loss-error";
  return (
    <div className="hero-form-block">
      <label className="flex items-center gap-3 min-h-11 t-title text-slate-900 cursor-pointer">
        <input
          type="checkbox"
          checked={!!form.use_stop_loss}
          onChange={(event) => setForm((current) => ({ ...current, use_stop_loss: event.target.checked }))}
        />
        손실 제한 사용
      </label>
      <div className="mt-5">
        <label htmlFor="hero-stop-loss" className="block t-small font-semibold text-slate-700 mb-2">손절 기준</label>
        <div className="flex items-center gap-3">
          <input
            id="hero-stop-loss"
            className="field t-h4 num"
            type="number"
            min="0.1"
            step="0.1"
            value={form.stop_loss_pct}
            disabled={!form.use_stop_loss}
            aria-invalid={!!error}
            aria-describedby={[unitId, helpId, error ? errorId : ""].filter(Boolean).join(" ")}
            onChange={(event) => setForm((current) => ({ ...current, stop_loss_pct: event.target.value }))}
          />
          <span id={unitId} className="t-title text-slate-700"><span className="sr-only">단위 </span>%</span>
        </div>
      </div>
      <div id={helpId} className="notice-risk mt-5 t-small text-slate-700">
        수익 목표보다 먼저, 한 번의 실패에서 얼마까지 감수할지 정해요. 레버리지는 전체 빌더에서 별도로 설정할 수 있어요.
      </div>
      {error ? <div id={errorId} className="mt-2 t-small text-red-600" role="alert">{error}</div> : null}
    </div>
  );
}

export function PeriodScene({ form, setForm, error }) {
  const helpId = "hero-period-help";
  const errorId = "hero-period-error";
  return (
    <div className="hero-form-block">
      <label htmlFor="hero-period" className="block t-small font-semibold text-slate-700 mb-2">확인할 과거 기간</label>
      <select
        id="hero-period"
        className="field t-title"
        value={form.preset}
        aria-invalid={!!error}
        aria-describedby={[helpId, error ? errorId : ""].filter(Boolean).join(" ")}
        onChange={(event) => setForm((current) => ({ ...current, preset: event.target.value, start: "", end: "" }))}
      >
        <option value="3m">최근 3개월</option>
        <option value="6m">최근 6개월</option>
        <option value="1y">최근 1년</option>
        {form.preset === "custom" ? <option value="custom">직접 지정한 기간</option> : null}
      </select>
      <p id={helpId} className="mt-4 t-small text-slate-500">한 기간에서 좋았다는 이유만으로 미래 결과가 보장되지는 않아요. 전체 빌더에서는 날짜를 직접 지정할 수 있어요.</p>
      {error ? <p id={errorId} className="mt-2 t-small text-red-600" role="alert">{error}</p> : null}
    </div>
  );
}

function ConditionEditor({ screen, form, setForm, error }) {
  if (screen.kind === "strategy") return <StrategyScene form={form} setForm={setForm} />;
  if (screen.kind === "condition") {
    return <ConditionScene field={screen.field} form={form} setForm={setForm} error={error} />;
  }
  if (screen.kind === "risk") return <RiskScene form={form} setForm={setForm} error={error} />;
  return <PeriodScene form={form} setForm={setForm} error={error} />;
}

export function ConditionWorkbench({ screen, form, setForm, error }) {
  const symbol = (form.symbol || "").trim().toUpperCase();
  return (
    <div className="grid gap-7 xl:grid-cols-2 items-start">
      <section className="min-w-0" aria-label={`${symbol} 조건 참고 차트`}>
        <div className="flex items-center justify-between gap-3 pb-3 border-b border-slate-200 flex-wrap">
          <div>
            <div className="t-caption text-slate-500">조건 참고 차트</div>
            <h2 className="mt-1 t-title text-slate-900 num">{symbol}</h2>
          </div>
          <span className="t-caption text-slate-500">봉 간격도 조건에 반영돼요</span>
        </div>
        <Suspense fallback={<SceneLoading label="조건 참고 차트 불러오는 중…" />}>
          <CandleChart
            symbol={symbol}
            interval={form.candle_interval}
            onIntervalChange={(value) => setForm((current) => ({ ...current, candle_interval: value }))}
            compact
          />
        </Suspense>
      </section>
      <div key={screen.id} className="min-w-0 xl:border-l xl:border-slate-200 xl:pl-7">
        <ConditionEditor screen={screen} form={form} setForm={setForm} error={error} />
      </div>
    </div>
  );
}

export function BacktestScene({ form, backtest }) {
  const hasResult = !!backtest.result;

  return (
    <section className="hero-live-output" aria-label="실제 백테스트 결과">
      <div className="flex items-center justify-between gap-3 pb-4 border-b border-slate-200 flex-wrap">
        <div>
          <div className="t-caption text-slate-500">실제 서버 계산</div>
          <div className="mt-1 t-title text-slate-900 num">{backtest.testedMacro?.symbol || form.symbol}</div>
        </div>
        <span className="badge badge-flat">기존 빌더와 같은 결과</span>
      </div>

      <div className="sr-only" role="status" aria-live="polite">
        {backtest.currentBusy
          ? "백테스트 결과를 계산하고 있어요."
          : backtest.resultIsFresh
          ? "현재 설정의 백테스트 결과가 준비됐어요."
          : ""}
      </div>

      {backtest.error ? (
        <div className="mt-5 t-small text-red-600" role="alert">오류: {backtest.error}</div>
      ) : null}
      {backtest.currentBusy ? (
        <div className="hero-calculating py-12 border-b border-slate-200" role="status">
          <div className="t-title text-slate-900">과거 데이터를 계산하고 있어요</div>
          <div className="mt-2 t-small text-slate-500">종목과 기간에 따라 잠시 걸릴 수 있어요.</div>
        </div>
      ) : null}
      {!hasResult && !backtest.currentBusy ? (
        <div className="py-12 border-b border-slate-200">
          <div className="t-title text-slate-900">아직 계산한 결과가 없어요</div>
          <p className="mt-2 t-small text-slate-700">아래 ‘이 조건으로 백테스트’를 누르면 앞에서 고른 조건으로 실제 계산을 시작해요.</p>
        </div>
      ) : null}
      {hasResult && !backtest.resultIsFresh ? (
        <div className="notice-warn my-5 t-small text-slate-700">
          결과를 낸 뒤 조건이 바뀌었어요. 아래 숫자는 이전 조건의 결과이며 다음 단계에는 사용할 수 없어요.
        </div>
      ) : null}
      {hasResult ? (
        <div className="mt-6">
          <Suspense fallback={<SceneLoading label="결과 화면 불러오는 중…" />}>
            <ResultView
              result={backtest.result}
              perSymbol={backtest.perSymbol}
              explanation={backtest.explanation}
              onAiExplain={backtest.explain}
              aiBusy={backtest.aiBusy}
              aiError={backtest.aiError}
              summary={backtest.summary}
              dataSource={backtest.dataSource}
              periodLabel={backtest.periodLabel}
              symbol={backtest.testedMacro?.symbol || form.symbol}
              leverage={backtest.testedMacro?.leverage || 1}
            />
          </Suspense>
        </div>
      ) : null}
    </section>
  );
}

export function PaperScene({ macro, valErr, controller }) {
  return (
    <div className="hero-live-output">
      <Suspense fallback={<SceneLoading label="페이퍼 트레이딩 화면 불러오는 중…" />}>
        <PaperPanelView macro={macro} valErr={valErr} controller={controller} />
      </Suspense>
      <div className="notice mt-5 t-small text-slate-700">
        다음 등록 단계로 이동할 때 이 미리보기 세션은 종료돼요. 등록을 완료하면 같은 설정의 리더보드 집계 세션이 새로 시작돼요.
      </div>
    </div>
  );
}

const pad = (value) => String(value).padStart(2, "0");

function formatCountdown(seconds) {
  if (seconds == null || !Number.isFinite(Number(seconds))) return "";
  const value = Math.max(0, Number(seconds));
  return `${pad(Math.floor(value / 3600))}:${pad(Math.floor((value % 3600) / 60))}:${pad(Math.floor(value % 60))}`;
}

function returnLabel(entry) {
  const value = entry?.return_pct ?? entry?.current_return;
  if (value == null || !Number.isFinite(Number(value))) return { text: "집계중…", className: "text-slate-500" };
  const numeric = Number(value);
  return {
    text: `${numeric >= 0 ? "+" : ""}${numeric.toFixed(2)}%`,
    className: numeric >= 0 ? "text-green-600" : "text-red-600",
  };
}

function RegisteredLeaderboard({ entry, snapshot, loading, syncError }) {
  const [remain, setRemain] = useState(snapshot?.seconds_to_reset ?? null);
  const fetched = snapshot?.items || [];
  const items = fetched.some((item) => item.id === entry.id) ? fetched : [entry, ...fetched];
  const registeredIndex = Math.max(0, items.findIndex((item) => item.id === entry.id));
  const windowStart = registeredIndex > 4 ? Math.max(0, registeredIndex - 2) : 0;
  const visibleItems = items.slice(windowStart, windowStart + 5);
  if (!visibleItems.some((item) => item.id === entry.id)) visibleItems.push(entry);

  useEffect(() => {
    const nextRemain = snapshot?.seconds_to_reset;
    setRemain(nextRemain ?? null);
    if (nextRemain == null) return undefined;
    const timer = window.setInterval(() => setRemain((current) => current > 0 ? current - 1 : 0), 1000);
    return () => window.clearInterval(timer);
  }, [snapshot?.seconds_to_reset]);

  return (
    <section id="hero-registered-board" tabIndex={-1} className="hero-product-screen hero-register-result hero-success-enter" aria-label="등록된 실제 리더보드 화면">
      <header className="hero-screen-page-header">
        <div>
          <div className="t-caption text-slate-500 mb-2">매일 KST 00:00 초기화</div>
          <h2 className="t-h2 text-slate-900">오늘의 리더보드</h2>
          <p className="mt-3 t-small text-slate-700 measure">실시간 모의 수익률로 겨루는 오늘의 보드예요. 방금 등록한 매크로를 실제 목록에서 바로 확인하고 있어요.</p>
        </div>
        <span className="badge badge-mine">실제 등록 완료</span>
      </header>

      <div className="notice-good mb-5 t-small text-slate-700" role="status">
        같은 설정으로 모의 수익률 집계를 시작했어요. 아래 노란 선이 방금 등록한 항목이에요.
      </div>

      <div className="flex items-center justify-between flex-wrap gap-3 mb-4 pb-4 border-b border-slate-200">
        <div className="t-small text-slate-700">
          {remain != null ? (
            <>리더보드 초기화까지 <span className="t-title num text-slate-900">{formatCountdown(remain)}</span><span className="text-slate-500"> 남음</span></>
          ) : (
            <>오늘 <span className="font-bold text-slate-900">KST 00:00</span>까지 집계해요.</>
          )}
        </div>
        <span className="t-caption text-slate-500">
          {loading ? "실제 순위 동기화 중…" : syncError ? "등록 응답 표시 중 · 순위는 잠시 후 갱신" : "실제 순위 동기화 완료"}
        </span>
      </div>

      <div className="hero-live-leaderboard-list">
        {visibleItems.map((item) => {
          const actualIndex = items.findIndex((candidate) => candidate.id === item.id);
          const isRegistered = item.id === entry.id;
          const result = returnLabel(item);
          const initial = (item.username || item.nickname || "?").charAt(0);
          return (
            <div key={item.id} className={"hero-live-leader-row " + (isRegistered ? "is-registered" : "")}>
              <div className="w-7 shrink-0 text-center t-h4 num text-slate-700">
                {isRegistered && (loading || syncError) ? "—" : actualIndex >= 0 ? actualIndex + 1 : registeredIndex + 1}
              </div>
              <div className={"hero-live-leader-avatar " + (!loading && !syncError && actualIndex === 0 ? "is-first" : "")}>{initial}</div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-x-2 gap-y-1 flex-wrap">
                  <span className="t-title text-slate-900">{item.username || item.nickname || "내 매크로"}</span>
                  {(item.is_owner || item.is_mine || isRegistered) ? <span className="badge badge-mine">내 것</span> : null}
                  {item.macro?.leverage > 1 ? <span className="badge badge-risk">고위험 · {item.macro.leverage}배</span> : null}
                  <span className="t-caption text-slate-500">· 오늘 {item.created_kst || "방금"} 등록</span>
                </div>
                <div className="mt-1 t-small text-slate-700">
                  {item.locked ? "잠김 · 언락하면 전략과 설정이 공개돼요" : item.human_summary || `${item.symbol || ""} 매크로 · 모의 집계 시작`}
                </div>
                <div className="mt-2 t-caption text-slate-500 num">
                  좋아요 {item.likes || 0} · 싫어요 {item.dislikes || 0}
                </div>
              </div>
              <div className={"w-28 shrink-0 text-right t-h4 num " + result.className}>{result.text}</div>
            </div>
          );
        })}
      </div>
      <p className="mt-4 t-caption text-slate-500">수익률과 좋아요는 참고용이며 투자 조언이 아니에요.</p>
    </section>
  );
}

export function RegisterScene({ macro, summary, canRegister, registeredEntry, registeredBoard, boardLoading, boardError, onOpen }) {
  if (registeredEntry) {
    return <RegisteredLeaderboard entry={registeredEntry} snapshot={registeredBoard} loading={boardLoading} syncError={boardError} />;
  }

  return (
    <section className="hero-product-screen hero-register-confirm" aria-labelledby="hero-register-confirm-title">
      <header className="hero-screen-page-header">
        <div>
          <div className="t-caption text-slate-500 mb-2">등록 전 마지막 확인</div>
          <h2 id="hero-register-confirm-title" className="t-h2 text-slate-900">오늘의 순위에 올릴까요?</h2>
          <p className="mt-3 t-small text-slate-700 measure">
            등록하면 이 화면이 실제 리더보드 목록으로 바뀌고, 방금 등록한 매크로를 바로 보여줘요.
          </p>
        </div>
        <span className="badge badge-mine">모의 수익률 집계</span>
      </header>

      <ol className="hero-register-flow">
        <li><span className="num">01</span><div><b>오늘 동안 설정 고정</b><p>지금 확인한 조건 그대로 집계해요.</p></div></li>
        <li><span className="num">02</span><div><b>모의 체결만 반영</b><p>실제 자산과 주문은 사용하지 않아요.</p></div></li>
        <li><span className="num">03</span><div><b>등록 결과 바로 확인</b><p>완료 즉시 실제 순위 화면으로 전환해요.</p></div></li>
      </ol>

      <footer className="hero-register-confirm-footer">
        <p className="t-caption text-slate-500">로그인 전이라면 설정을 보관한 뒤 이 화면으로 돌아옵니다.</p>
        <button type="button" onClick={onOpen} disabled={!canRegister} className="btn btn-l btn-primary">
          리더보드 등록 시작
        </button>
      </footer>
    </section>
  );
}

const COMMUNITY_MESSAGES = [
  ["09:12", "차분한고래", "DCA 간격을 7일에서 14일로 바꾸니 MDD가 조금 줄었어요."],
  ["09:18", "초록앵무", "SOL은 변동성이 큰데 손절 기준을 몇 %로 두셨나요?"],
  ["09:23", "차분한고래", "저는 우선 8%로 테스트하고 다른 기간도 같이 보고 있어요."],
  ["09:31", "코린이7일차", "수익률보다 홀딩 대비 결과를 먼저 보는 게 맞을까요?"],
  ["09:34", "느린거북", "네, 저는 홀딩 대비랑 MDD를 같이 봐요. 한 숫자만 보면 헷갈리더라고요."],
  ["09:41", "초록앵무", "좋네요. 같은 조건으로 1년 구간도 돌려볼게요."],
];

const BOARD_POSTS = [
  { title: "DCA 매수 간격 7일·14일 비교해 봤어요", snippet: "같은 SOLUSDT 조건에서 기간만 바꿔 본 결과와 느낀 점을 정리했습니다.", author: "차분한고래", time: "오늘 09:44", comments: 12 },
  { title: "백테스트 수익률보다 MDD를 먼저 봐야 하나요?", snippet: "첫 전략을 만들었는데 수익률은 높고 최대낙폭도 커서 기준이 궁금해요.", author: "코린이7일차", time: "오늘 09:08", comments: 8 },
  { title: "횡보장에서 그리드 간격 정하는 방법", snippet: "너무 촘촘하게 잡았을 때 수수료가 결과에 미친 영향을 비교했습니다.", author: "느린거북", time: "오늘 08:36", comments: 5 },
  { title: "페이퍼 트레이딩 첫날 기록", snippet: "실제 돈 없이 체결 흐름을 보니 조건이 언제 작동하는지 이해하기 쉬웠어요.", author: "보라여우", time: "어제 22:17", comments: 17, image: true },
  { title: "이동평균 전략 기간을 바꿀 때 체크할 것", snippet: "20·60과 50·200 조합을 각각 돌려 본 표를 공유합니다.", author: "캔들읽는새", time: "어제 20:52", comments: 9 },
  { title: "초보자용 결과 지표 읽는 순서", snippet: "홀딩 비교 → MDD → 거래 횟수 → 자산곡선 순서로 보고 있어요.", author: "한걸음씩", time: "어제 18:05", comments: 21 },
];

export function CommunityScene() {
  return (
    <div className="hero-product-screen hero-community-stack" aria-label="커뮤니티 전체 화면 목업">
      <div className="hero-mock-disclosure"><span className="badge badge-flat">화면 목업</span><span>아래 대화와 게시글은 기능을 설명하기 위한 예시 데이터예요.</span></div>

      <section className="hero-community-page" aria-labelledby="hero-chat-mock-title">
        <header className="hero-screen-page-header">
          <div>
            <div className="t-caption text-slate-500 mb-2">오늘의 전략 대화</div>
            <h2 id="hero-chat-mock-title" className="t-h2 text-slate-900">리더보드 채팅</h2>
            <p className="mt-3 t-small text-slate-700">결과를 보며 짧게 묻고 답하는 실시간 공간이에요.</p>
          </div>
          <span className="t-caption text-slate-500">매일 KST 00:00 초기화</span>
        </header>
        <div className="hero-community-chat-log" role="img" aria-label="예시 메시지가 채워진 리더보드 채팅 화면">
          {COMMUNITY_MESSAGES.map(([time, name, message]) => (
            <div key={`${time}-${name}`}>
              <span className="t-caption num text-slate-500">{time}</span>
              <b className="t-small text-slate-900">{name}</b>
              <span className="t-small text-slate-700">{message}</span>
            </div>
          ))}
        </div>
        <div className="hero-community-compose" aria-hidden="true">
          <span className="hero-mock-field is-name">내 아이디</span>
          <span className="hero-mock-field">메시지 입력 (최대 300자)</span>
          <span className="btn btn-m btn-secondary">전송</span>
        </div>
        <p className="mt-3 t-caption text-slate-500">채팅 내용은 투자 조언이 아니고, 매매 판단과 책임은 본인에게 있어요.</p>
      </section>

      <section className="hero-community-page" aria-labelledby="hero-board-mock-title">
        <header className="hero-screen-page-header">
          <div>
            <div className="t-caption text-slate-500 mb-2">전략·질문·정보</div>
            <h2 id="hero-board-mock-title" className="t-h2 text-slate-900">껄무새 게시판</h2>
            <p className="mt-3 t-small text-slate-700">전략·질문·후기처럼 오래 남길 이야기를 나눠요. 투자 조언은 아니에요.</p>
          </div>
          <span className="btn btn-m btn-primary" aria-hidden="true">새 글 쓰기</span>
        </header>
        <ul className="hero-community-board-list">
          {BOARD_POSTS.map((post) => (
            <li key={post.title}>
              <div className="min-w-0">
                <div className="t-title text-slate-900">
                  {post.image ? <span className="badge badge-flat mr-2">사진</span> : null}
                  {post.title}<span className="ml-2 t-caption font-bold num text-indigo-800">[{post.comments}]</span>
                </div>
                <div className="mt-1 t-small text-slate-500">{post.snippet}</div>
              </div>
              <div className="text-right shrink-0">
                <div className="t-caption text-slate-700">{post.author}</div>
                <div className="t-caption text-slate-500 num">{post.time}</div>
              </div>
            </li>
          ))}
        </ul>
        <div className="hero-mock-pagination" aria-hidden="true"><span>‹</span><span className="is-current">1</span><span>2</span><span>3</span><span>›</span></div>
        <p className="mt-4 t-caption text-slate-500 text-center">예시 게시글이며 실제 투자 조언이 아니에요.</p>
      </section>
    </div>
  );
}

const NEWS_HEADLINES = [
  ["비트코인 현물 거래량 증가…주요 가격대 변동성도 확대", "마켓브리프", "오늘 08:42"],
  ["글로벌 규제기관, 가상자산 공시 기준과 이용자 보호 논의", "디지털파이낸스", "오늘 07:18"],
  ["이더리움 네트워크 수수료 안정세…활성 주소는 소폭 증가", "체인데일리", "오늘 06:54"],
  ["스테이블코인 결제 실험 확대, 준비금 투명성 기준이 쟁점", "블록리포트", "어제 23:31"],
  ["미국 거시지표 발표 앞두고 코인 시장 관망세", "글로벌마켓", "어제 21:06"],
];

const TRENDING_COINS = [
  ["BTC", 2.41],
  ["ETH", 1.18],
  ["SOL", 5.76],
  ["XRP", -1.34],
  ["DOGE", 3.09],
];

export function NewsScene() {
  return (
    <div className="hero-product-screen hero-news-page" aria-label="코인동향 전체 화면 목업">
      <div className="hero-mock-disclosure"><span className="badge badge-flat">화면 목업</span><span>기사와 등락률은 화면 구성을 보여주기 위한 예시 데이터예요.</span></div>
      <header className="hero-screen-page-header">
        <div>
          <div className="t-caption text-slate-500 mb-2">시장·규제 헤드라인</div>
          <h2 className="t-h2 text-slate-900">오늘의 코인동향</h2>
          <p className="mt-3 t-small text-slate-700 measure">코인 시장·규제 관련 최신 뉴스를 하루 한 번 모아 요약해요. (예시 기준 오늘 09:00 · KST)</p>
        </div>
      </header>

      <section>
        <h3 className="t-h4 text-slate-900 mb-1">시장·규제 한눈에</h3>
        <div className="notice mt-3">
          <div className="t-caption text-slate-500 mb-1">AI 요약 · 예시</div>
          <p className="t-label font-medium text-slate-700 leading-relaxed measure">
            주요 시장은 거시지표 발표를 앞두고 방향을 탐색하는 가운데 거래량과 변동성이 함께 늘었어요. 규제 뉴스는 공시 기준과 이용자 보호에 초점이 맞춰졌고, 개별 코인별 움직임 차이가 커 헤드라인의 시각과 원문을 함께 확인할 필요가 있어요.
          </p>
        </div>

        <h3 className="mt-7 mb-2 t-title text-slate-900">최신 헤드라인</h3>
        <ul className="hero-news-headlines">
          {NEWS_HEADLINES.map(([headline, source, time]) => (
            <li key={headline}>
              <div className="t-label text-slate-900 leading-snug">{headline}</div>
              <div className="mt-1 t-caption text-slate-500">{source} · {time} · 원문 보기 ↗</div>
            </li>
          ))}
        </ul>
        <div className="hero-news-terms" aria-label="뉴스 용어 예시"><span>현물 거래량</span><span>공시 기준</span><span>스테이블코인</span><span>거시지표</span></div>
        <div className="notice-warn mt-4 t-caption text-slate-700"><b className="text-slate-900">주의 · </b>뉴스와 요약은 참고용이며 매수·매도 추천이 아니에요.</div>
      </section>

      <section className="mt-10">
        <h3 className="t-h4 text-slate-900 mb-1">경주마 동향</h3>
        <p className="t-small text-slate-700 mb-3">급등하거나 활발히 거래되는 코인별 최신 뉴스를 눌러 펼쳐 보는 영역이에요.</p>
        <div className="hero-trending-coin-list">
          {TRENDING_COINS.map(([symbol, change]) => (
            <div key={symbol}>
              <span className="flex items-center gap-3">
                <b className="t-title text-slate-900">{symbol}</b>
                <b className={"t-label num " + (change >= 0 ? "text-green-600" : "text-red-600")}>{change >= 0 ? "+" : ""}{change.toFixed(2)}%</b>
              </span>
              <span className="t-small font-semibold text-slate-500">뉴스 보기 ▼</span>
            </div>
          ))}
        </div>
        <p className="mt-4 t-caption text-slate-500">경주마 선정과 뉴스는 참고용이고 투자 권유가 아니에요.</p>
      </section>
    </div>
  );
}

export function GuideScene() {
  return (
    <section className="hero-product-screen hero-guide-live" aria-label="실제 사용 가이드 첫 화면">
      <div className="hero-mock-disclosure"><span className="badge badge-flat">실제 화면</span><span>좌측 사이드바의 ‘사용 가이드’를 누르면 아래 문서로 이동해요. 검색과 목차도 직접 눌러볼 수 있어요.</span></div>
      <Suspense fallback={<SceneLoading label="실제 가이드 첫 화면 불러오는 중…" />}>
        <GuidePage embedded initialSection="start" />
      </Suspense>
    </section>
  );
}
