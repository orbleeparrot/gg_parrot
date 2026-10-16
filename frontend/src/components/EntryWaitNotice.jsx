// 시작 전 안내 — 진입을 기다리는 매크로를 실행기로 넘기기 직전에 "바로 사지 않아요" 와 평소 기다린 시간을 먼저 말한다.
// 답답함은 '바로 살 줄 알았는데' 에서 오므로, 실행기를 열기 전에 기대치를 맞춘다.
import useMacroCandles from "../hooks/useMacroCandles.js";
import { entryWaitModel, fmtRoughSpan, isWaitingMacro } from "../lib/entryWait.js";
import "./EntryWaitCard.css";

function marketOf(macro) {
  if (macro?.market === "futures" || macro?.market === "spot") return macro.market;
  return macro?.position_side === "short" || Number(macro?.leverage || 1) > 1 ? "futures" : "spot";
}

export default function EntryWaitNotice({ macro, symbol }) {
  const waiting = isWaitingMacro(macro);
  const candles = useMacroCandles({
    symbol: symbol || macro?.symbol, interval: macro?.candle_interval || "1d", market: marketOf(macro),
    exchange: macro?.exchange || "binance", enabled: waiting,
  });
  if (!waiting) return null;
  const model = entryWaitModel({ macro: { ...macro, symbol: symbol || macro?.symbol }, candles });
  if (!model) return null;
  const typical = model.typical;
  return (
    <section className="ewn" aria-label="이 매크로는 바로 사지 않아요">
      <p className="ewn-title">이 매크로는 바로 사지 않아요.</p>
      <p className="ewn-body">{model.sentence}</p>
      {typical ? (
        <p className="ewn-body">
          최근 {fmtRoughSpan(typical.spanMs).replace("약 ", "")} 동안 조건이 맞은 건 <b className="num">{typical.episodes}번</b>
          {typical.avgMs ? <>, 평균 <b className="num">{fmtRoughSpan(typical.avgMs)}</b>마다였어요.</> : "이에요. 한참 기다릴 수 있어요."}
        </p>
      ) : null}
      <p className="ewn-body">실행기에서는 모의로 준비돼요. 조건이 맞아 사는 걸 확인한 뒤 실전으로 바꿔도 돼요.</p>
    </section>
  );
}
