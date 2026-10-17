import InfoTooltip from "./InfoTooltip.jsx";
import { NEWS_GLOSSARY, newsTermRegex } from "../lib/newsTerms.js";

// 본문 문자열에서 알려진 용어의 '첫 등장'에만 ⓘ 설명을 인라인으로 붙인다.
// (링크 안에는 넣지 않음 — 툴팁 버튼과 앵커가 중첩되면 안 되니 개요 등 순수 텍스트에만.)
export function AnnotatedText({ text }) {
  if (!text) return null;
  const re = newsTermRegex();
  const nodes = [];
  const used = new Set();
  let last = 0;
  let m;
  while ((m = re.exec(text)) !== null) {
    const term = m[0];
    if (used.has(NEWS_GLOSSARY[term])) continue; // 같은 뜻 변형은 처음 한 번만
    used.add(NEWS_GLOSSARY[term]);
    if (m.index > last) nodes.push(text.slice(last, m.index));
    nodes.push(
      <span
        key={m.index}
        className="underline decoration-dotted decoration-indigo-400 underline-offset-2"
      >
        {term}
        <InfoTooltip text={NEWS_GLOSSARY[term]} />
      </span>
    );
    last = m.index + term.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return <>{nodes}</>;
}
