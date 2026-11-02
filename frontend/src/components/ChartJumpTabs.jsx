// 여러 종목 차트 위에 붙는 종목 줄 — 누르면 그 차트까지 바로 내려간다.
//
// **탭이 아니다.** 차트는 그대로 다 쌓여 있고 이 줄은 자리만 옮겨 준다. 한 번에 하나만
// 보이게 하면 위아래로 견주어 보던 길이 막힌다. 그래서 role="tab" 을 안 쓴다 —
// 보조기기가 '누르면 이 화면만 보인다' 고 읽으면 거짓말이 된다.
import { baseOf } from "../lib/format.js";

export function ChartJumpTabs({ symbols, active = "", onJump }) {
  const rows = Array.isArray(symbols) ? symbols : [];
  // 한 종목짜리는 스크롤할 것이 없다 — 줄이 자리만 먹는다.
  if (rows.length < 2) return null;
  return (
    <nav className="studio-chart-jumps" aria-label="차트 종목 바로가기">
      {rows.map((symbol) => {
        const on = symbol === active;
        return (
          <button
            key={symbol}
            type="button"
            className={"studio-chart-jump" + (on ? " is-on" : "")}
            aria-current={on ? "true" : undefined}
            onClick={() => onJump(symbol)}
          >
            {baseOf(symbol)}
          </button>
        );
      })}
    </nav>
  );
}

export default ChartJumpTabs;
