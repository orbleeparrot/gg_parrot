import InfoTooltip from "./InfoTooltip.jsx";
import { NEWS_GLOSSARY, newsTermRegex, findNewsTerms } from "../lib/newsTerms.js";

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

// 여러 텍스트(개요 + 헤드라인들)에서 발견된 용어를 칩으로 모아 보여준다.
// 헤드라인은 링크라 인라인 ⓘ를 못 넣으니, 그 용어들을 여기서 대신 풀어준다.
export function TermChips({ texts }) {
  const joined = (texts || []).filter(Boolean).join("  •  ");
  const terms = findNewsTerms(joined);
  if (terms.length === 0) return null;
  return (
    <div className="news-term-chips mt-3 flex flex-wrap items-center gap-2">
      <span className="news-term-chips-label t-caption text-slate-700">용어</span>
      {terms.map((t) => (
        <span key={t} className="news-term-chip chip chip-sm">
          {t}
          <InfoTooltip text={NEWS_GLOSSARY[t]} />
        </span>
      ))}
    </div>
  );
}
