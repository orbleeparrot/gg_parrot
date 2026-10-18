import CoinIcon from "./CoinIcon.jsx";
import { exchangeLabel, exchangeLogo } from "../lib/exchanges.js";
import "./CoinMark.css";

// 지금 무슨 코인을 어느 거래소에서 돌리는지 — 코인 로고 모서리에 거래소 작은 로고(시안 B, 2026-10-18).
// 마켓 이름(KRW-B3 · BTCUSDT)은 따로 쓰지 않는다 — 거래소마다 마켓이 하나뿐(업비트 · 빗썸 원화, 바이낸스 USDT)이라
// 작은 로고가 보이면 겹치는 정보다. 작은 로고 둘레는 놓인 바탕색(--coin-mark-ring)으로 끊어 겹침이 보이게 한다.
export default function CoinMark({ symbol, exchange = null, size = 32, className = "" }) {
  const mini = Math.round(size * 0.44);
  return (
    <span className={`coin-mark ${className}`} style={{ "--coin-mark-size": `${size}px`, "--coin-mark-mini": `${mini}px` }}>
      <CoinIcon symbol={symbol} size={size} alt="" />
      {exchange ? <img className="coin-mark-ex" src={exchangeLogo(exchange)} alt={exchangeLabel(exchange)} width={mini} height={mini} draggable="false" /> : null}
    </span>
  );
}
