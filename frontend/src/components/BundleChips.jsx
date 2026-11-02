// 빌더의 묶음 줄 — 기준 묶음과 분야 묶음을 **두 줄로** 그린다.
//
// 설계: docs/superpowers/specs/2026-11-02-sector-bundles-design.md
//
// 한 줄에 몰아 놓으면 "시가총액 TOP10" 과 "레이어1" 이 같은 것처럼 읽힌다. 앞엣것은
// 큰 것들의 목록이고 뒤엣것은 한 분야다. 비어 있는 줄은 이름표까지 통째로 안 그린다.
import { splitBundles } from "../lib/portfolio.js";

export const BUNDLE_SIZE_LABEL = "추천 묶음";
export const BUNDLE_SECTOR_LABEL = "분야 묶음";

function Row({ label, items, disabled, onPick }) {
  if (!items.length) return null;
  return (
    <div className="bd-presets">
      <span className="bd-presets-label">{label}</span>
      <div className="bd-presets-row">
        {items.map((item) => (
          <button
            key={item.key}
            type="button"
            className="bd-preset"
            disabled={disabled}
            title={item.lead}
            /* 보이는 글자만으로는 이름이 안 읽힌다 — title 이 이름 자리를 가져간다. */
            aria-label={`${item.name} · ${item.legs.length}종목 · ${item.lead}`}
            onClick={() => onPick(item)}
          >
            {item.name}
            <small>{item.legs.length}종목</small>
          </button>
        ))}
      </div>
    </div>
  );
}

export function BundleChips({ items, disabled = false, onPick }) {
  const { sectors, sizes } = splitBundles(items);
  if (!sectors.length && !sizes.length) return null;
  return (
    <>
      <Row label={BUNDLE_SIZE_LABEL} items={sizes} disabled={disabled} onPick={onPick} />
      <Row label={BUNDLE_SECTOR_LABEL} items={sectors} disabled={disabled} onPick={onPick} />
    </>
  );
}

export default BundleChips;
