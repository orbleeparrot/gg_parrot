import { cloneElement, isValidElement, useId, useState } from "react";
import { RULE_TYPES, PERIOD_PRESETS, CANDLE_INTERVALS, MAX_LEVERAGE, withTypeDefaults } from "../lib/macro.js";
import InfoTooltip from "./InfoTooltip.jsx";
import { api } from "../api.js";
import { quoteOf, fmtKrw } from "../lib/format.js";
import { useUsdKrw } from "../lib/usdkrw.js";

// USDT amount plus an approximate KRW reference (when a rate is available).
const money = (v, symbol, rate) => {
  const usdt = `${Number(v || 0).toLocaleString("en-US")} ${quoteOf(symbol)}`;
  const krw = rate ? fmtKrw(v, rate) : "";
  return krw ? `${usdt} · ${krw}` : usdt;
};

// Live risk read-out for a chosen leverage. Price move to liquidation ≈ 100/N %.
// 청산 경고는 '끼어드는 것'이라 §1-3 예외로 상자를 유지한다(alert).
function leverageRisk(lev) {
  const n = Math.max(1, Math.round(Number(lev) || 1));
  if (n <= 1) return null;
  const movePct = 100 / n;
  let level, cls;
  if (n >= 10) { level = n >= 20 ? "매우 위험" : "고위험"; cls = "alert-risk"; }
  else { level = n >= 4 ? "주의" : "낮음"; cls = "alert-warn"; }
  return { n, movePct, level, cls };
}

// §4 자리별 적용표: 입력 라벨 14/600, 도움말 14/500 — 둘 다 '작은 글씨' 단계.
function Field({ label, term, children, hint, anchor }) {
  const controlId = useId();
  const labelId = `${controlId}-label`;
  const hintId = `${controlId}-hint`;
  const directControl =
    isValidElement(children) &&
    typeof children.type === "string" &&
    ["input", "select", "textarea"].includes(children.type);
  const renderedControl = directControl
    ? cloneElement(children, {
        id: children.props.id || controlId,
        "aria-describedby": hint
          ? [children.props["aria-describedby"], hintId].filter(Boolean).join(" ")
          : children.props["aria-describedby"],
      })
    : children;

  return (
    <div
      className="block"
      data-tour={anchor}
      role={directControl ? undefined : "group"}
      aria-labelledby={directControl ? undefined : labelId}
      aria-describedby={!directControl && hint ? hintId : undefined}
    >
      <div className="flex items-center t-small font-semibold text-slate-700 mb-2">
        {directControl ? (
          <label id={labelId} htmlFor={children.props.id || controlId}>{label}</label>
        ) : (
          <span id={labelId}>{label}</span>
        )}
        {term && <InfoTooltip term={term} />}
      </div>
      {renderedControl}
      {hint && <div id={hintId} className="t-small text-slate-500 mt-2">{hint}</div>}
    </div>
  );
}

// 빌더의 입력 묶음. 상자로 감싸지 않고 괘선 + 제목으로만 나눈다(§1-3) —
// 폼 상자 예외는 화면 전체를 감싸는 폼(로그인·모달)에만 적용한다.
function Group({ title, term, children, note, anchor }) {
  return (
    <section className="pt-5 border-t border-slate-200" data-tour={anchor}>
      <div className="flex items-center text-slate-700 mb-3">
        <h3 className="t-title">{title}</h3>
        {term && <InfoTooltip term={term} />}
        {note}
      </div>
      {children}
    </section>
  );
}

const inputCls = "field";

// `chartSlot` 은 '전략 조건' 바로 위에 끼워 넣을 참고 차트다. Studio 가 넘겨준다 —
// 폼 컴포넌트가 차트·시세 폴링까지 끌어안지 않도록 자리만 비워 둔다.
export default function Builder({ form, setForm, chartSlot = null }) {
  const instanceId = useId().replace(/:/g, "");
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const setChk = (k) => (e) => setForm({ ...form, [k]: e.target.checked });
  const rt = form.rule_type;
  const meta = RULE_TYPES[rt];
  const isShort = form.position_side === "short";
  const { rate: krwRate } = useUsdKrw();
  const [fundingBusy, setFundingBusy] = useState(false);
  const [fundingMsg, setFundingMsg] = useState("");

  async function loadFunding() {
    setFundingBusy(true);
    setFundingMsg("");
    try {
      const d = await api.fundingRate(form.symbol.toUpperCase(), form.preset, form.start, form.end);
      if (d.available && d.avg_daily_funding_pct != null) {
        setForm((f) => ({ ...f, funding_pct: d.avg_daily_funding_pct }));
        setFundingMsg(`실제 평균 펀딩비 적용: 일 ${d.avg_daily_funding_pct}%`);
      } else {
        setFundingMsg("이 종목은 선물 펀딩 데이터가 없어요 (현물 전용일 수 있음).");
      }
    } catch (e) {
      setFundingMsg("펀딩비 조회 실패: " + String(e.message || e));
    } finally {
      setFundingBusy(false);
    }
  }

  // Field builders — plain functions (invoked, not JSX components) so inputs
  // keep focus across keystrokes. They close over the current `form`.
  const num = (k, label, opts = {}) => (
    <Field key={k} label={label} term={opts.term} hint={opts.hint} anchor={opts.anchor}>
      <input className="field num" type="number" step={opts.step || "any"} value={form[k]} onChange={set(k)} />
    </Field>
  );
  const sel = (k, label, options, opts = {}) => (
    <Field key={k} label={label} term={opts.term} hint={opts.hint} anchor={opts.anchor}>
      <select className={inputCls} value={form[k]} onChange={set(k)}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </Field>
  );
  const chk = (k, label, opts = {}) => (
    <div key={k} className="flex items-center gap-1 h-12 t-small text-slate-700">
      <label htmlFor={`${instanceId}-${k}`} className="flex items-center gap-2 cursor-pointer">
        <input id={`${instanceId}-${k}`} type="checkbox" checked={!!form[k]} onChange={setChk(k)} />
        {label}
      </label>
      {opts.term && <InfoTooltip term={opts.term} />}
    </div>
  );
  const cap = num("initial_capital", `시작 자금 (${quoteOf(form.symbol)})`, {
    hint: money(form.initial_capital, form.symbol, krwRate),
  });

  return (
    <div className="space-y-5">
      <div>
        <h3 className="t-title text-slate-900">기본 설정</h3>
        <p className="mt-1 t-small text-slate-500">무엇을, 어떤 규칙으로, 어느 기간에 확인할지 정해요.</p>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 gap-y-5">
        <Field label="종목" anchor="symbol" hint="여러 종목은 쉼표로 나눠 써요. 자금은 종목 수만큼 균등하게 나눠요.">
          <input className={inputCls} value={form.symbol} onChange={set("symbol")} placeholder="BTCUSDT 또는 BTCUSDT, ETHUSDT" />
        </Field>
        <Field label="매매 방식" anchor="strategy" term={`strat_${rt}`}>
          <select className={inputCls} value={rt} onChange={(e) => setForm(withTypeDefaults(form, e.target.value))}>
            {Object.entries(RULE_TYPES).map(([k, v]) => (
              <option key={k} value={k}>{v.label}</option>
            ))}
          </select>
        </Field>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <Field
          label="포지션"
          anchor="position"
          hint={
            <span className="inline-flex items-center flex-wrap">
              롱 <InfoTooltip term="long" />
              <span className="ml-2">숏</span> <InfoTooltip term="short" />
            </span>
          }
        >
          <select className={inputCls} value={form.position_side} onChange={set("position_side")} disabled={!meta.allowShort}>
            <option value="long">롱 (long)</option>
            <option value="short" disabled={!meta.allowShort}>숏 (short)</option>
          </select>
        </Field>
        {sel("candle_interval", "봉 간격", CANDLE_INTERVALS, {
          term: "candle_interval",
          anchor: "interval",
          hint: meta.indicator ? "지표 계산 기준(필수)" : "체결 판정 기준",
        })}
        <Field label="테스트 기간" anchor="period" term="backtest">
          <select className={inputCls} value={form.preset} onChange={set("preset")}>
            {PERIOD_PRESETS.map((p) => (
              <option key={p.value} value={p.value}>{p.label}</option>
            ))}
          </select>
        </Field>
      </div>

      {form.preset === "custom" && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <Field label="시작일" hint="YYYY-MM-DD">
            <input className={inputCls} type="date" value={form.start} onChange={set("start")} />
          </Field>
          <Field label="종료일" hint="YYYY-MM-DD">
            <input className={inputCls} type="date" value={form.end} onChange={set("end")} />
          </Field>
        </div>
      )}

      {/* leverage — a macro condition (backtest/paper only; C is excluded) */}
      {rt !== "C" && (() => {
        const lev = Math.max(1, Math.round(Number(form.leverage) || 1));
        const risk = leverageRisk(lev);
        return (
          <Group
            title="레버리지"
            term="leverage"
            anchor="leverage"
            note={<span className="ml-2 t-caption text-slate-500">격리(isolated) · 백테스트·모의만</span>}
          >
            <div className="flex items-center gap-4">
              <input
                aria-label="레버리지 배수"
                type="range" min="1" max={MAX_LEVERAGE} step="1" value={lev}
                onChange={set("leverage")}
                className="flex-1 accent-red-500"
              />
              <div className="flex items-center gap-2">
                <input
                  aria-label="레버리지 배수 직접 입력"
                  className="field w-20 text-center num"
                  type="number" min="1" max={MAX_LEVERAGE} step="1" value={form.leverage}
                  onChange={set("leverage")}
                />
                <span className="t-label text-slate-700">배</span>
              </div>
            </div>
            {risk ? (
              <div className={"alert mt-3 t-small flex items-start gap-2 " + risk.cls}>
                <span>
                  <b>레버리지 <span className="num">{risk.n}</span>배 · {risk.level}</b> — 가격이 약{" "}
                  <b className="num">{risk.movePct.toFixed(risk.movePct < 1 ? 2 : 1)}%</b> 반대로 움직이면{" "}
                  <b>청산(전액 손실)</b>돼요.
                  {lev >= 10 && " 처음이라면 특히 위험해요."}
                  <InfoTooltip term="liquidation" />
                </span>
              </div>
            ) : (
              <div className="mt-3 t-small text-slate-500">1배 = 현물과 같아요(청산 없음). 배수를 올리면 수익도 손실도 그만큼 커지고 청산 위험이 생겨요.</div>
            )}
          </Group>
        );
      })()}

      {/* price-data source (spot vs USDT-M futures) */}
      {sel(
        "market",
        "시세 데이터",
        [
          { value: "auto", label: "자동 (숏·레버리지 → 선물, 그 외 현물)" },
          { value: "spot", label: "현물 (spot)" },
          { value: "futures", label: "선물 (USDT-M Futures)" },
        ],
        {
          anchor: "market",
          hint:
            "실제 사용될 데이터: " +
            (form.market === "spot"
              ? "현물 캔들"
              : form.market === "futures"
              ? "선물 캔들 + 실제 펀딩비 반영 가능"
              : isShort || Number(form.leverage) > 1 || rt === "K"
              ? "선물 캔들 (숏/레버리지라 자동 선택)"
              : "현물 캔들 (롱·1배라 자동 선택)"),
        }
      )}

      {/* 참고 차트는 '전략 조건' 바로 위에 둔다. 오버레이를 실제로 움직이는 값이
          바로 아래(익절·손절·기간·밴드 폭…)라, 값을 만지면 시선 바로 위에서
          보조지표가 따라 움직인다. 레버리지·시세 데이터는 오버레이와 무관해서
          그 사이에 끼우면 차트와 조절값이 갈린다. */}
      {chartSlot}

      {/* rule-specific params */}
      <Group title={<>전략 조건 · <span className="text-slate-900">{meta.label}</span></>} anchor="strategy-params">
        {rt === "A" && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {num("take_profit_pct", "익절 기준 (%)", { term: "take_profit" })}
            {cap}
          </div>
        )}
        {rt === "B" && (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            {num("buy_price", `살 가격 (${quoteOf(form.symbol)})`, { term: "limit_order" })}
            {num("sell_price", `팔 가격 (${quoteOf(form.symbol)})`, { term: "limit_order" })}
            {cap}
          </div>
        )}
        {rt === "C" && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {num("amount_per_buy", `한 번에 살 금액 (${quoteOf(form.symbol)})`, { term: "dca", hint: money(form.amount_per_buy, form.symbol, krwRate) })}
            {num("interval_days", "매수 간격 (일)", { term: "dca" })}
          </div>
        )}

        {rt === "D" && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {num("lower_price", `가격 범위 하단 (${quoteOf(form.symbol)})`, { term: "grid" })}
            {num("upper_price", `가격 범위 상단 (${quoteOf(form.symbol)})`, { term: "grid" })}
            {num("grid_count", "나눌 칸 수", { term: "grid_count", step: "1" })}
            {sel("grid_mode", "칸 간격", [{ value: "arithmetic", label: "같은 금액 간격" }, { value: "geometric", label: "같은 비율 간격" }], { term: "grid_mode" })}
            {num("per_grid_invest", `격자당 투입액 (빈칸=균등, ${quoteOf(form.symbol)})`, { hint: "비우면 예산을 격자 수로 균등 분배" })}
            {sel("band_exit_action", "가격 범위를 벗어나면", [{ value: "stop", label: "전량 정리하고 중단" }, { value: "hold", label: "보유 유지" }])}
            <div className="col-span-full grid grid-cols-1 sm:grid-cols-2 gap-4 items-end">
              {chk("rebalance_on_start", "시작 가격에 맞춰 칸 다시 배치")}
              {cap}
            </div>
          </div>
        )}

        {rt === "E" && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {sel("entry_mode", "처음 들어갈 때", [{ value: "immediate", label: "바로 진입" }, { value: "dip", label: "가격이 내리면 진입" }])}
            {num("entry_dip", "진입할 하락폭 (%)", { hint: "가격이 내리면 진입을 골랐을 때 써요" })}
            {num("activation_profit", "추적을 시작할 이익 (%)", { term: "activation_profit" })}
            {num("trail_percent", "고점에서 허용할 하락폭 (%)", { term: "trail_percent" })}
            {chk("reenter_after_exit", "정리한 뒤 다시 진입")}
            {cap}
          </div>
        )}

        {rt === "F" && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {num("rsi_period", "RSI 계산 기간", { term: "rsi", step: "1" })}
            {num("confirm_candles", "신호를 확인할 봉 수", { step: "1", hint: "연속해서 조건을 만족해야 신호로 봐요" })}
            {num("entry_threshold", "진입할 RSI", { hint: "이 숫자 이하일 때 진입해요" })}
            {num("exit_threshold", "정리할 RSI", { hint: "이 숫자 이상일 때 정리해요" })}
            {sel("exit_mode", "정리 기준", [{ value: "indicator", label: "RSI 신호" }, { value: "take_profit", label: "익절 기준" }, { value: "both", label: "둘 중 먼저" }])}
            {num("take_profit", "익절 기준 (%)", { hint: "익절 기준을 포함할 때 써요" })}
            {cap}
          </div>
        )}

        {rt === "G" && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {num("bb_period", "평균 계산 기간", { term: "bollinger", step: "1" })}
            {num("bb_std", "밴드 폭 (표준편차 σ)", { term: "bollinger" })}
            {sel("strategy", "밴드를 쓰는 방식", [{ value: "reversion", label: "밴드 안으로 되돌아오기" }, { value: "breakout", label: "밴드 밖으로 돌파하기" }])}
            {sel("exit_target", "정리할 위치", [{ value: "mid", label: "가운데 선" }, { value: "opposite", label: "반대쪽 밴드" }])}
            <div className="col-span-full grid grid-cols-1 sm:grid-cols-2 gap-4 items-end">
              {chk("squeeze_filter", "변동성이 줄어든 구간만 사용", { term: "squeeze" })}
              {num("squeeze_lookback", "변동성 비교 기간", { step: "1" })}
            </div>
            {cap}
          </div>
        )}

        {rt === "H" && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {num("base_order_size", `처음 살 금액 (${quoteOf(form.symbol)})`, { term: "martingale" })}
            {num("safety_order_size", `첫 추가매수 금액 (${quoteOf(form.symbol)})`, { term: "safety_order" })}
            {num("price_deviation", "추가매수할 하락 간격 (%)", { hint: "가격이 이만큼 더 내릴 때마다 추가로 사요" })}
            {num("max_safety_orders", "최대 추가매수 횟수", { step: "1" })}
            {num("safety_order_step_scale", "하락 간격 배율")}
            {num("safety_order_volume_scale", "추가매수 금액 배율")}
            {num("take_profit", "평균 매수가 기준 익절 (%)", { hint: "전체 평균 매수가를 기준으로 계산해요" })}
            {cap}
            <div className="col-span-full t-caption text-amber-700">손절은 평균 매수가를 기준으로 적용돼요. 총 소요자금이 (시작 자금 × 투입 비율)을 넘으면 저장되지 않아요.</div>
          </div>
        )}

        {rt === "I" && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {num("k", "돌파 기준 계수 (k)", { term: "volatility_breakout" })}
            {sel("exit_mode", "정리 기준", [{ value: "next_open", label: "다음 봉 시작 가격" }, { value: "trailing", label: "고점 추적" }, { value: "take_profit", label: "익절 기준" }])}
            {num("trail_percent", "고점에서 허용할 하락폭 (%)", { term: "trail_percent", hint: "고점 추적을 골랐을 때 써요" })}
            {num("take_profit", "익절 기준 (%)", { hint: "익절 기준을 골랐을 때 써요" })}
            {num("ma_filter_period", "이동평균 필터 기간", { step: "1", hint: "비워두면 사용하지 않아요" })}
            {num("session_start_hour", "하루 계산 시작 시각", { step: "1" })}
            {cap}
            <div className="col-span-full t-caption text-slate-500">봉 단위 데이터로 전일 변동폭을 계산해요.</div>
          </div>
        )}

        {rt === "J" && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {sel("ma_type", "이동평균 종류", [{ value: "SMA", label: "단순 이동평균 (SMA)" }, { value: "EMA", label: "최근 가격 비중이 큰 평균 (EMA)" }], { term: "ma_cross" })}
            {num("confirm_candles", "신호를 확인할 봉 수", { step: "1" })}
            {num("fast_period", "짧은 이동평균 기간", { step: "1" })}
            {num("slow_period", "긴 이동평균 기간", { step: "1" })}
            {sel("exit_signal", "정리 기준", [{ value: "dead_cross", label: "평균선이 아래로 교차" }, { value: "take_profit", label: "익절 기준" }, { value: "both", label: "둘 중 먼저" }])}
            {num("take_profit", "익절 기준 (%)", { hint: "익절 기준을 포함할 때 써요" })}
            {cap}
          </div>
        )}

        {rt === "K" && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {num("drop_trigger_pct", "방어를 시작할 하락폭 (%)", { hint: "진입가보다 이만큼 내리면 방어를 시작해요" })}
            {num("partial_exit_pct", "방어할 때 팔 비율 (%)", { hint: "들고 있는 수량 중 몇 %를 팔지 정해요" })}
            {chk("flip_to_short", "일부를 판 뒤 숏으로 전환")}
            {num("long_take_profit_pct", "롱 익절 기준 (%)", { hint: "비워두면 하락 방어만 사용해요" })}
            {num("short_take_profit_pct", "숏 익절 기준 (%)", { hint: "숏 전환 뒤 가격이 더 내릴 때 정리해요" })}
            {num("short_stop_loss_pct", "숏 손절 기준 (%)", { hint: "가격이 반등할 때 손실을 제한해요. 필수예요" })}
            {chk("reenter_long_after", "숏을 끝낸 뒤 롱으로 다시 진입")}
            {cap}
            <div className="col-span-full t-caption text-amber-700">숏 전환이 포함돼 선물(USDT-M)로 실행돼요. 숏 손절 기준은 필수예요.</div>
          </div>
        )}
      </Group>

      {/* common risk */}
      <Group title="손실 제한" anchor="risk">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 gap-y-5">
          {num("invest_ratio_pct", "한 번에 사용할 자금 (%)", { term: "invest_ratio", hint: "시작 자금 중 한 번에 얼마를 쓸지 정해요" })}
          <Field label="손절 기준 (%)" term="stop_loss" hint={isShort && (rt === "A" || rt === "B") ? "숏은 손절이 필수예요" : "사용하지 않으려면 체크를 풀어요"}>
            <div className="flex items-center gap-2">
              <input aria-label="손절 기준 사용" type="checkbox" checked={form.use_stop_loss} disabled={isShort && (rt === "A" || rt === "B")} onChange={setChk("use_stop_loss")} />
              <input aria-label="손절률 (%)" className="field num" type="number" value={form.stop_loss_pct} disabled={!form.use_stop_loss} onChange={set("stop_loss_pct")} />
            </div>
          </Field>
        </div>
      </Group>

      {/* advanced common risk. For DCA (rule C) the time-based holding/cooldown
          controls don't apply (buy-and-accumulate, no round-trip exits), so they
          are disabled with a note; 일일 최대손실 still works (halts buys for the day). */}
      {(() => {
        const isDca = rt === "C";
        return (
          <details className="pt-5 border-t border-slate-200" data-tour="advanced-risk">
            <summary className="t-label text-slate-700 cursor-pointer">고급 위험 관리</summary>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 gap-y-5 mt-4">
              <Field label="하루 최대 손실 (%)" term="daily_max_loss" hint={isDca ? "도달하면 그날 추가 매수를 멈춰요" : "도달하면 그날 거래를 멈춰요"}>
                <div className="flex items-center gap-2">
                  <input aria-label="하루 최대 손실 사용" type="checkbox" checked={form.use_daily_max_loss} onChange={setChk("use_daily_max_loss")} />
                  <input aria-label="하루 최대 손실률 (%)" className="field num" type="number" value={form.daily_max_loss_pct} disabled={!form.use_daily_max_loss} onChange={set("daily_max_loss_pct")} />
                </div>
              </Field>
              <Field label="가장 오래 보유할 시간" term="max_holding" hint={isDca ? "분할매수에는 사용하지 않아요" : "이 시간을 넘기면 강제로 정리해요"}>
                <div className="flex items-center gap-2">
                  <input aria-label="최대 보유시간 사용" type="checkbox" checked={!isDca && form.use_max_holding} disabled={isDca} onChange={setChk("use_max_holding")} />
                  <input aria-label="최대 보유시간 (시간)" className="field num" type="number" value={form.max_holding_hours} disabled={isDca || !form.use_max_holding} onChange={set("max_holding_hours")} />
                </div>
              </Field>
              {isDca ? (
                <Field label="손절 뒤 쉬는 시간 (분)" term="cooldown" hint="분할매수에는 사용하지 않아요">
                  <input className="field num" type="number" value={form.cooldown_minutes} disabled onChange={set("cooldown_minutes")} />
                </Field>
              ) : (
                num("cooldown_minutes", "손절 뒤 쉬는 시간 (분)", { term: "cooldown", hint: "이 시간 동안 다시 진입하지 않아요" })
              )}
            </div>
            {isDca && (
              <div className="notice mt-4 t-small text-slate-700">
                DCA 전략은 사고 나서 계속 들고 가는 방식이라 <b className="text-slate-900">최대 보유시간·재진입 금지</b>는 적용되지 않아요. 익절·청산이 있는 전략(A·B·E~J)에서 동작해요.
              </div>
            )}
          </details>
        );
      })()}

      {/* fees */}
      <details className="pt-5 border-t border-slate-200" data-tour="fees">
        <summary className="t-label text-slate-700 cursor-pointer">
          거래 비용과 펀딩비
        </summary>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 gap-y-5 mt-4">
          {num("commission_pct", "거래 수수료 (%)", { term: "commission", step: "0.01" })}
          {num("slippage_pct", "체결 가격 차이 (%)", { term: "slippage", step: "0.01" })}
          {num("funding_pct", "하루 펀딩비 (숏, %)", { step: "0.01" })}
        </div>
        <div className="mt-3 flex items-center gap-2 flex-wrap">
          <button
            type="button"
            onClick={loadFunding}
            disabled={fundingBusy}
            className="btn btn-s btn-secondary"
          >
            {fundingBusy ? "불러오는 중…" : "실제 펀딩비 가져오기"}
          </button>
          <span className="t-caption text-slate-500">
            {fundingMsg || "선물 시장의 실제 평균 펀딩비(일)를 이 기간 기준으로 가져와 채워요."}
          </span>
        </div>
      </details>
    </div>
  );
}
