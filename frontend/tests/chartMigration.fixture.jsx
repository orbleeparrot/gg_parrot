import React, { useCallback, useLayoutEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import CandleChart from "../src/components/CandleChart.jsx";
import EquityChart from "../src/components/EquityChart.jsx";
import { computeStrategyOverlay, computeSessionOverlay } from "../src/lib/indicators.js";
import "../src/index.css";
import "../src/pages/Studio.css";

const params = {
  A: { take_profit_pct: 5, use_stop_loss: true, stop_loss_pct: 3 },
  B: { buy_price: 96, sell_price: 106 }, C: {},
  D: { lower_price: 90, upper_price: 110, grid_count: 8, grid_mode: "arithmetic" },
  E: { trail_percent: 3, entry_mode: "dip", entry_dip: 2 },
  F: { rsi_period: 7, entry_threshold: 35, exit_threshold: 65 },
  G: { bb_period: 12, bb_std: 1.3, strategy: "reversion", exit_target: "opposite" },
  H: { price_deviation: 1, safety_order_step_scale: 1.4, max_safety_orders: 4, take_profit: 3 },
  I: { k: 0.4 }, J: { fast_period: 5, slow_period: 16, ma_type: "EMA" },
  K: { drop_trigger_pct: 3, long_take_profit_pct: 5, short_take_profit_pct: 4, short_stop_loss_pct: 2 },
};
const defaults = { rule: "G", side: "long", average: false, variant: "default", theme: "dark", symbol: "BTCUSDT", interval: "1m", market: "spot", epoch: 0, equity: false, stretch: false, params: {} };
const observations = { data: [], loads: [], overlay: null, config: null };
const initialCurve = Array.from({ length: 90 }, (_, i) => ({ t: new Date(Date.UTC(2026, 0, 1 + i)).toISOString(), equity: 1000 + i * 3 + Math.sin(i / 3) * 25 }));

function Fixture() {
  const [config, setConfig] = useState(defaults);
  const [curve, setCurve] = useState(initialCurve);
  const overlay = useCallback((candles) => {
    const form = { rule_type: config.rule, position_side: config.side, ...params[config.rule], ...config.params };
    const spec = config.average ? computeSessionOverlay(config.rule ? { rule_type: config.rule, position_side: config.side, params: form, risk: { stop_loss_pct: form.use_stop_loss ? form.stop_loss_pct : null } } : null, 100, config.side, candles) : computeStrategyOverlay(form, candles);
    observations.overlay = spec;
    return spec;
  }, [config.rule, config.side, config.average, config.params]);
  const onData = useCallback((payload) => { observations.data.push(payload); }, []);
  const onLoadState = useCallback((payload) => { observations.loads.push(payload); }, []);
  useLayoutEffect(() => {
    document.documentElement.classList.toggle("dark", config.theme === "dark");
    observations.config = config;
  }, [config]);
  const methods = useMemo(() => ({
    configure: (next) => setConfig((current) => ({ ...current, ...next })),
    reset: (next = {}) => { observations.data = []; observations.loads = []; setConfig((current) => ({ ...defaults, ...next, epoch: current.epoch + 1 })); },
    inspect: () => ({ ...observations, curve }),
    setCurve,
  }), [curve]);
  useLayoutEffect(() => { window.chartFixture = methods; }, [methods]);
  return (
    <main style={{ maxWidth: 1180, margin: "0 auto", padding: 16 }}>
      <h1 className="t-title" style={{ marginBottom: 16 }}>실제 차트 이식 검증</h1>
      <section id="chart-under-test" style={config.variant === "studio" ? { height: 550, display: "flex", minWidth: 0 } : { minWidth: 0 }}>
        <CandleChart key={config.epoch} symbol={config.symbol} market={config.market} interval={config.interval} onIntervalChange={(interval) => setConfig((current) => ({ ...current, interval }))} onData={onData} onLoadState={onLoadState} overlay={overlay} variant={config.variant === "studio" ? "studio" : "default"} compact={config.variant === "compact"} minimal={config.variant === "minimal"} expanded={config.variant === "minimal"} />
      </section>
      {config.equity && <section id="equity-under-test" className={config.stretch ? "sd-eq" : undefined} style={{ marginTop: 40, ...(config.stretch ? { height: 220, display: "flex", flexDirection: "column" } : {}) }}><h2 className="t-title">백테스트 자산곡선</h2><EquityChart curve={curve} height={config.stretch ? 140 : 240} stretch={config.stretch} /></section>}
      <div style={{ height: 500 }} aria-hidden="true" />
    </main>
  );
}
createRoot(document.getElementById("root")).render(<Fixture />);
