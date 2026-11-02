// 물어볼까의 묶음 후보 — 분야마다 카드 한 장. 담길 종목을 **누르기 전에** 다 보여 준다.
//
// 설계: docs/superpowers/specs/2026-11-02-sector-bundles-design.md
//
// 왜 티커를 다 적는가: 이 제품은 값이 오를지 내릴지 말하지 않는다. 그러면 고르기 전에
// 쥘 수 있는 정보는 "무엇이 담기느냐" 뿐이다. 칸 이름만 보여 주면 "레이어1" 을 눌렀더니
// 들어 본 적 없는 코인이 들어오는 일이 생긴다.
//
// 숫자 두 칸은 서버가 받아 둔 티커로 계산해 붙인 것이다. 못 구했으면 칸을 안 그린다 —
// 0 을 적으면 화면이 그걸 '거래가 없다' 로 읽는다.
import { baseOf } from "../lib/format.js";
import { bundleStatPhrases } from "../lib/portfolio.js";

export const BUNDLE_NONE_NOTE = "지금 분야 묶음을 불러오지 못했어요. 아래에서 종목을 직접 골라 담아 주세요.";
const RANK_LABEL = "거래대금 순위";
const RANGE_LABEL = "하루 변동 평균";

function sameSet(legs, picked) {
  const chosen = new Set(picked || []);
  return legs.length > 0 && legs.length === chosen.size && legs.every((symbol) => chosen.has(symbol));
}

export function BundleCards({ items, marketText = "", picked = [], disabled = false, onPick, i = 0 }) {
  const rows = Array.isArray(items) ? items : [];
  if (!rows.length) {
    return <p className="ask-bcard-none ask-in" style={{ "--i": i }} role="note">{BUNDLE_NONE_NOTE}</p>;
  }
  return (
    <div className="ask-bcards">
      {rows.map((item, idx) => {
        const symbols = (item.legs || []).map((leg) => leg.symbol);
        const stats = bundleStatPhrases(item.stats);
        const on = sameSet(symbols, picked);
        return (
          <button key={item.key} type="button" disabled={disabled} aria-pressed={on}
            className={"ask-bcard ask-in" + (on ? " is-picked" : "")}
            style={{ "--i": i + idx + 1 }}
            onClick={() => onPick(symbols)}>
            <span className="ask-bcard-head">
              <span className="ask-bcard-name">{item.name}</span>
              <span className="ask-bcard-count">
                <span className="num">{symbols.length}</span>종목{marketText ? ` · ${marketText}` : ""}
              </span>
              <span className="ask-bcard-pick" aria-hidden="true">{on ? "✓" : "+"}</span>
            </span>
            <span className="ask-bcard-legs">
              {symbols.map((symbol) => (
                <span key={symbol} className="ask-bcard-leg num">{baseOf(symbol)}</span>
              ))}
            </span>
            {item.lead ? <span className="ask-bcard-lead">{item.lead}</span> : null}
            {stats ? (
              <span className="ask-bcard-stats">
                <span className="ask-bcard-stat">
                  <span className="ask-lbl">{RANK_LABEL}</span><b className="num">{stats.rank}</b>
                </span>
                <span className="ask-bcard-stat">
                  <span className="ask-lbl">{RANGE_LABEL}</span><b className="num">{stats.range}</b>
                </span>
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

export default BundleCards;
