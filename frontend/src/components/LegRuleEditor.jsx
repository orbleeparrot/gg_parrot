// 레그(종목) 하나의 규칙 판. 묶음의 기본 규칙 대신 이 종목만 다른 규칙으로 돌릴 때 쓴다.
// 규칙 세부값은 Builder 의 전략 조건 판을 그대로 재사용한다 — 규칙별 칸을 두 번 구현하지 않는다.
//
// Builder 가 이 컴포넌트를 쓰고 이 컴포넌트가 Builder 를 쓴다. scope="leg" 로 그리는 Builder 는
// 종목 고르기(= 이 컴포넌트를 부르는 자리)를 그리지 않으므로 고리가 한 번에 끝난다.
import Builder from "./Builder.jsx";
import { Icon } from "./icons.jsx";

// 규칙 객체는 폼 모양(defaultForm 바탕)이다 — buildLegs 가 buildParams 로 읽는다.
// 종목 · 거래소는 묶음이 정하므로 판에는 넘기되 저장하는 규칙에는 남기지 않는다.
export default function LegRuleEditor({ id, symbol, rule, exchange = "binance", onChange, onClear }) {
  const form = { ...rule, symbol, exchange };
  const setForm = (next) => {
    const value = typeof next === "function" ? next(form) : next;
    const { symbol: _symbol, exchange: _exchange, ...saved } = value;
    onChange(saved);
  };
  return (
    <div className="bd-legrule" id={id} aria-label={`${symbol} 규칙`}>
      <Builder form={form} setForm={setForm} variant="dense" scope="leg" />
      <p className="bd-hint bd-legrule-note">시작 자금은 위 종목 비중대로 나뉘어요. 포지션 · 기간 · 위험 관리는 묶음 설정을 따라요.</p>
      <button type="button" className="bd-legrule-clear" onClick={onClear}>
        <Icon name="x" size={12} strokeWidth={2.25} /> 묶음 기본 규칙으로
      </button>
    </div>
  );
}
