// 전략 한 줄 — 리더보드 전략 칸의 표기 그대로: 티커 | 포지션 | 전략 설명 | 자금 (+ 필요하면 봉 간격·기간 같은 덧붙임).
// 직접 만들기의 제목 띠도 이 부품을 쓴다. 값은 lib/leaderboardStrategy 가 매크로와 서버 요약에서 뽑는다.
import { baseOf, quoteOf } from "../lib/format.js";
import { leaderboardStrategy } from "../lib/leaderboardStrategy.js";
import "./StrategyDetails.css";

export default function StrategyDetails({ entry, extra = [] }) {
  const details = leaderboardStrategy(entry);
  const sideLabel = { long: "롱", short: "숏", switch: "롱 → 숏" }[details?.side] || "—";
  const extraText = extra.map((item) => `${item.label} ${item.value}`).join(" | ");
  const fullText = (details
    ? `${entry.symbol} | ${sideLabel} | ${details.description} | ${details.capital ? `${details.capital.label} ${details.capital.value} ${details.capital.unit}` : "자금 —"}`
    : `${entry.symbol} | 잠긴 전략`) + (extraText ? ` | ${extraText}` : "");
  return (
    <dl className="lb-strategy-facts" title={fullText}>
      <div className="lb-fact lb-fact-ticker">
        <dt className="sr-only">티커</dt>
        <dd className="lb-strategy-ticker num"><strong>{baseOf(entry.symbol)}</strong><small>{quoteOf(entry.symbol)}</small></dd>
      </div>
      {details ? <>
        <div className="lb-fact lb-fact-position">
          <dt className="sr-only">포지션</dt>
          <dd className={`lb-position is-${details.side || "unknown"}`}>
            {sideLabel}
          </dd>
        </div>
        <div className="lb-fact lb-fact-strategy">
          <dt className="sr-only">전략</dt>
          <dd className="lb-summary-text">{details.description}</dd>
        </div>
        <div className="lb-fact lb-fact-capital">
          <dt>{details.capital?.label || "자금"}</dt>
          <dd><span className="num">{details.capital?.value || "—"}</span>{details.capital ? <small>{details.capital.unit}</small> : null}</dd>
        </div>
      </> : <div className="lb-fact lb-fact-locked"><dt className="sr-only">전략</dt><dd>잠긴 전략</dd></div>}
      {extra.map((item) => (
        <div key={item.label} className="lb-fact lb-fact-extra">
          <dt>{item.label}</dt>
          <dd>{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}
