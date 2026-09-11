// 매크로 조건 문장(서버 human_summary 의 핵심)을 조판 단위로 나눈다.
// 구(句)는 " · " 로 나뉘고, 구 안의 숫자(범위·퍼센트·σ·배수)는 따로 떼어 고정폭 굵게 놓는다.
// 부호가 붙은 값은 등락색: +5% 익절 → up, -3% 손절 → down. 단어는 손대지 않는다.
// 숫자는 자릿수 쉼표를 품되 쉼표로 끝나지 않는다 — "볼린저(20, 2σ)" 의 "20," 가 숫자에 딸려 오지 않게.
const NUM = /([+\-−]?\d(?:[\d,]*\d)?(?:\.\d+)?(?:\s?~\s?[+\-−]?\d(?:[\d,]*\d)?(?:\.\d+)?)?(?:%|σ|x)?)/g;

// 서버 요약 끝에 붙는 "3배 레버리지(격리)" 구. 카드 머리의 시장 태그(선물 · 격리 3배)가 같은 값을 보여 주므로 뺄 수 있다.
const LEVERAGE_PHRASE = /^\d+배 레버리지(\(.*\))?$/;

export function strategyPhrases(text, { dropLeverage = false } = {}) {
  const phrases = String(text || "")
    .split(" · ")
    .map((phrase) => phrase.trim())
    .filter(Boolean)
    .filter((phrase) => !(dropLeverage && LEVERAGE_PHRASE.test(phrase)));
  return phrases.map((phrase) => {
    const tokens = [];
    let last = 0;
    for (const match of phrase.matchAll(NUM)) {
      const value = match[0];
      const start = match.index;
      // 단어에 붙은 숫자(RSI14 처럼 영문 바로 뒤)는 그대로 둔다 — "SMA 7/25" 의 7·25 는 앞이 공백이라 숫자로 뗀다.
      const prev = start > 0 ? phrase[start - 1] : " ";
      if (/[A-Za-z]/.test(prev)) continue;
      if (start > last) tokens.push({ t: "text", v: phrase.slice(last, start) });
      const sign = value[0];
      const tone = sign === "+" ? "up" : sign === "-" || sign === "−" ? "down" : null;
      tokens.push({ t: "num", v: value, tone });
      last = start + value.length;
    }
    if (last < phrase.length) tokens.push({ t: "text", v: phrase.slice(last) });
    return tokens;
  });
}
