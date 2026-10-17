import { useState } from "react";
import { baseOf } from "../lib/format.js";

// 종목 로고. 바이낸스 공개 로고 CDN 을 쓴다 — 이 앱이 다루는 종목이 곧 바이낸스
// 상장 종목이라 커버리지가 그대로 맞아떨어진다. 실측으로 BTC·ETH 같은 메이저부터
// RE·HEMI·MUBARAK 같은 신규 상장까지 전부 나온다(대안으로 본 cryptocurrency-icons
// 는 같은 12종 중 5종만 있었다).
//
// 바이낸스에 없는 국내 전용 종목(업비트·빗썸)은 우리 서버 사본(/api/coin-logo — 바이낸스 → 업비트 순)으로
// 한 번 더 찾고, 그래도 없으면 티커 첫 글자로 조용히 되돌아간다 — 깨진 이미지 아이콘이 뜨는 것보다 낫다.
const LOGO_BASE = "https://bin.bnbstatic.com/static/assets/logos";

// proxy — 카드를 이미지로 뜰 때(html2canvas)는 다른 출처 이미지를 캔버스에 못 그리므로 우리 서버의 사본(/api/coin-logo)을 쓴다.
export default function CoinIcon({ symbol, size = 36, className = "", alt, proxy = false }) {
  // 0: 바이낸스 CDN · 1: 서버 사본 · 2: 글자. proxy(이미지 내보내기)면 처음부터 서버 사본.
  // 단계는 종목마다 따로 — 같은 자리에서 종목만 바뀌면(코인 정보 칸) 앞 종목의 실패를 물려받지 않는다.
  const base = baseOf(symbol || "");
  const start = proxy ? 1 : 0;
  const [tried, setTried] = useState({ base, stage: start });
  const stage = tried.base === base ? tried.stage : start;
  const failed = stage >= 2;
  const label = base || "?";
  // 크기는 CSS 가 --coin-size 로 정한다 — 감싸는 칸이 줄면(.lb-coin 휴대폰 32px) CSS 가 맞춘다.
  const style = { "--coin-size": `${size}px` };

  if (!base || failed) {
    return (
      <span
        className={`coin-icon is-fallback ${className}`}
        style={style}
        aria-hidden={alt === "" ? "true" : undefined}
        title={label}
      >
        {label.slice(0, 3)}
      </span>
    );
  }

  return (
    <img
      src={stage === 1 ? `/api/coin-logo/${encodeURIComponent(base)}.png` : `${LOGO_BASE}/${base}.png`}
      alt={alt ?? `${label} 로고`}
      width={size}
      height={size}
      loading="lazy"
      /* 바이낸스 로고 CDN 은 리퍼러가 붙으면 핫링크로 보고 막는다(실측: 기본
         요청은 10종 전부 실패, no-referrer 면 통과). 로고는 공개 정적 파일이라
         리퍼러를 빼도 잃는 게 없다. */
      referrerPolicy={stage === 1 ? undefined : "no-referrer"}
      crossOrigin={proxy ? "anonymous" : undefined}
      draggable="false"
      className={`coin-icon ${className}`}
      style={style}
      onError={() => setTried((current) => ({ base, stage: (current.base === base ? current.stage : start) + 1 }))}
    />
  );
}
