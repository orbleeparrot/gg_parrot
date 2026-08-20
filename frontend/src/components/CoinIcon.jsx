import { useState } from "react";
import { baseOf } from "../lib/format.js";

// 종목 로고. 바이낸스 공개 로고 CDN 을 쓴다 — 이 앱이 다루는 종목이 곧 바이낸스
// 상장 종목이라 커버리지가 그대로 맞아떨어진다. 실측으로 BTC·ETH 같은 메이저부터
// RE·HEMI·MUBARAK 같은 신규 상장까지 전부 나온다(대안으로 본 cryptocurrency-icons
// 는 같은 12종 중 5종만 있었다).
//
// 못 불러오면 티커 첫 글자로 조용히 되돌아간다 — 상장 직후처럼 로고가 아직 없는
// 종목에서 깨진 이미지 아이콘이 뜨는 것보다 낫다.
const LOGO_BASE = "https://bin.bnbstatic.com/static/assets/logos";

export default function CoinIcon({ symbol, size = 36, className = "", alt }) {
  const [failed, setFailed] = useState(false);
  const base = baseOf(symbol || "");
  const label = base || "?";
  const style = { width: size, height: size };

  if (!base || failed) {
    return (
      <span
        className={`coin-icon is-fallback ${className}`}
        style={{ ...style, fontSize: Math.max(11, Math.round(size * 0.34)) }}
        aria-hidden={alt === "" ? "true" : undefined}
        title={label}
      >
        {label.slice(0, 3)}
      </span>
    );
  }

  return (
    <img
      src={`${LOGO_BASE}/${base}.png`}
      alt={alt ?? `${label} 로고`}
      width={size}
      height={size}
      loading="lazy"
      /* 바이낸스 로고 CDN 은 리퍼러가 붙으면 핫링크로 보고 막는다(실측: 기본
         요청은 10종 전부 실패, no-referrer 면 통과). 로고는 공개 정적 파일이라
         리퍼러를 빼도 잃는 게 없다. */
      referrerPolicy="no-referrer"
      draggable="false"
      className={`coin-icon ${className}`}
      style={style}
      onError={() => setFailed(true)}
    />
  );
}
