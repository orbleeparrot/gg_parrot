// 스퀘어리파이드 트리맵 — 경주마 동향의 벤토 배치.
//
// 한 직사각형을 상승률(가중치)에 비례하는 타일로 나눈다. Bruls·Huizing·van Wijk 의
// squarified 알고리즘: 남은 영역의 짧은 변을 따라 타일을 한 줄씩 쌓되, 줄에 하나를 더
// 넣었을 때 줄 안 타일들의 최악 종횡비가 나빠지면 줄을 확정하고 다음 줄로 넘어간다.
// 결과는 정사각형에 가까운 타일들이라 글자를 얹기 좋다(Finviz 맵과 같은 방식).
//
// 입력 순서를 가중치 내림차순으로 정렬해서 쓴다 — 큰 타일이 왼쪽 위에 온다.

// 가중치 하한. 상승률 0·음수도 자리는 있어야 하므로 아주 작은 타일로 남긴다.
const MIN_CHANGE = 0.5;

// 면적 ∝ √상승률. 상승률 그대로 쓰면 +115% 하나가 지도의 절반을 먹고 +4% 는 글자도 못 얹는다.
// 제곱근이면 115%:4% 가 5.4:1 — 순위 차이는 한눈에 보이면서 작은 타일도 티커는 읽힌다.
export function racerWeight(changePct) {
  const value = Number(changePct);
  const change = Number.isFinite(value) ? Math.max(value, MIN_CHANGE) : MIN_CHANGE;
  return Math.sqrt(change);
}

function worstRatio(row, length) {
  // row: 가중치(면적) 배열, length: 줄을 놓는 변의 길이.
  const sum = row.reduce((acc, v) => acc + v, 0);
  if (!sum || !length) return Infinity;
  const side = sum / length; // 줄의 두께
  let worst = 0;
  for (const area of row) {
    const other = area / side;
    const ratio = Math.max(side / other, other / side);
    if (ratio > worst) worst = ratio;
  }
  return worst;
}

// items: [{ weight, ...rest }], 반환: [{ x, y, width, height, item }] — 모두 0~1 비율.
export function layoutTreemap(items, width = 1, height = 1) {
  const list = items
    .map((item) => ({ item, weight: Math.max(Number(item.weight) || 0, 0) }))
    .filter((entry) => entry.weight > 0);
  if (!list.length || width <= 0 || height <= 0) return [];

  const total = list.reduce((acc, entry) => acc + entry.weight, 0);
  const scale = (width * height) / total; // 가중치 → 면적
  const areas = list.map((entry) => ({ item: entry.item, area: entry.weight * scale }));

  const rects = [];
  let x = 0;
  let y = 0;
  let w = width;
  let h = height;
  let row = [];

  const flush = () => {
    if (!row.length) return;
    const sum = row.reduce((acc, entry) => acc + entry.area, 0);
    const horizontal = w >= h; // 짧은 변(세로)을 따라 줄을 세운다 → 줄은 왼쪽에 세로로 쌓임
    if (horizontal) {
      const rowWidth = sum / h;
      let cy = y;
      for (const entry of row) {
        const rowHeight = entry.area / rowWidth;
        rects.push({ x, y: cy, width: rowWidth, height: rowHeight, item: entry.item });
        cy += rowHeight;
      }
      x += rowWidth;
      w -= rowWidth;
    } else {
      const rowHeight = sum / w;
      let cx = x;
      for (const entry of row) {
        const rowWidth = entry.area / rowHeight;
        rects.push({ x: cx, y, width: rowWidth, height: rowHeight, item: entry.item });
        cx += rowWidth;
      }
      y += rowHeight;
      h -= rowHeight;
    }
    row = [];
  };

  for (const entry of areas) {
    const side = Math.min(w, h);
    const current = row.map((r) => r.area);
    if (row.length && worstRatio([...current, entry.area], side) > worstRatio(current, side)) {
      flush();
    }
    row.push(entry);
  }
  flush();

  return rects.map((rect) => ({
    x: rect.x / width,
    y: rect.y / height,
    width: rect.width / width,
    height: rect.height / height,
    item: rect.item,
  }));
}
