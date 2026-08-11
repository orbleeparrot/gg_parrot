import { useMemo, useState } from "react";
import { PageHeader } from "../components/Page.jsx";

// 코린이(코인 입문자)용 가이드 — 문서(docs) 형식: 좌측 목차 + 검색 + 본문.
// 각 섹션은 검색용 plain `text`와 렌더용 `body`(JSX 또는 (go)=>JSX)를 함께 갖는다.
// 규칙 A~K는 개요 페이지(rules) 아래 하위 페이지(rule-a ... rule-k)로 나뉜다.

// 본문 램프(§3-2): body 17/1.55, 제목은 17/700 + 자간 조임.
function P({ children }) {
  return <p className="t-body text-slate-600 mb-3">{children}</p>;
}
function H({ children }) {
  return <h3 className="t-title text-slate-900 mt-6 mb-2">{children}</h3>;
}
// 상자 대신 왼쪽 규칙(§6 notice) — 가이드 본문이 색 블록으로 끊기지 않게 한다.
function Note({ children }) {
  return <div className="notice-warn my-4 t-label font-medium text-slate-700">{children}</div>;
}
function Tip({ children }) {
  return <div className="notice my-4 t-label font-medium text-slate-700">{children}</div>;
}
function Steps({ items }) {
  return (
    <ol className="list-decimal pl-5 space-y-2 t-body text-slate-600 mb-3">
      {items.map((it, i) => (
        <li key={i}>{it}</li>
      ))}
    </ol>
  );
}
// 파라미터 표 — 빌더에서 조정하는 숫자들의 뜻.
function Params({ rows }) {
  return (
    <div className="my-4 overflow-x-auto">
      <table className="w-full min-w-[360px]">
        <thead>
          <tr className="border-b border-slate-200 t-caption text-slate-700">
            <th className="text-left py-2 pr-3">파라미터</th>
            <th className="text-left pr-3">뜻</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([k, v], i) => (
            <tr key={i} className="border-b border-slate-200 last:border-0 align-top">
              <td className="py-3 pr-3 num t-caption text-slate-900 whitespace-nowrap">{k}</td>
              <td className="py-3 pr-3 t-label font-medium text-slate-700">{v}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
// 언제 쓰나 / 주의 — 색 카드 두 장 대신 왼쪽 규칙 두 줄.
function GoodBad({ good, bad }) {
  return (
    <div className="my-4 grid sm:grid-cols-2 gap-4">
      <div className="notice-good t-label font-medium text-slate-700">
        <div className="font-bold text-slate-900 mb-1">잘 맞는 경우</div>
        {good}
      </div>
      <div className="notice-risk t-label font-medium text-slate-700">
        <div className="font-bold text-slate-900 mb-1">주의할 경우</div>
        {bad}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 미니 일러스트 차트 (의존성 없음, 다크모드는 --chart-* 변수로 대응)
// 좌표계: x,y 모두 0~100 (y는 0=바닥, 100=천장). 아래 fx/fy가 패딩 적용해 매핑.
// ---------------------------------------------------------------------------
// viewBox 는 '그려질 실제 크기'로 잡는다. 예전엔 340×172 로 그려 놓고 880px 폭에
// 늘려서 썼는데, 그러면 배율이 2.6배가 되어 라벨 10.5px 이 27px 로 — 페이지 제목(24px)
// 보다 크게 찍혔다. 도형 안 글자는 본문 글자와 같은 크기로 보여야 한다.
const FIG_W = 560, FIG_H = 220, FIG_PAD = { l: 16, r: 16, t: 16, b: 30 };
const fx = (x) => FIG_PAD.l + (x / 100) * (FIG_W - FIG_PAD.l - FIG_PAD.r);
const fy = (y) => FIG_PAD.t + (1 - y / 100) * (FIG_H - FIG_PAD.t - FIG_PAD.b);
const poly = (pts) => pts.map(([x, y]) => `${fx(x).toFixed(1)},${fy(y).toFixed(1)}`).join(" ");

const C = {
  price: "rgb(var(--chart-axis))",
  grid: "rgb(var(--chart-grid))",
  up: "rgb(var(--chart-up))",
  down: "rgb(var(--chart-down))",
  band: "rgb(var(--c-indigo-500))",
  band2: "rgb(var(--c-indigo-300))",
};

// 차트를 상자에 담지 않는다(§6 bar-chart) — 캡션은 13/600.
function Fig({ caption, legend, children }) {
  return (
    <figure className="my-5 max-w-[560px]">
      <svg viewBox={`0 0 ${FIG_W} ${FIG_H}`} className="w-full h-auto" role="img" aria-label={caption}>
        {children}
      </svg>
      {legend && <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 t-caption">{legend}</div>}
      {caption && <figcaption className="mt-2 t-caption text-slate-700 leading-relaxed">{caption}</figcaption>}
    </figure>
  );
}
function Key({ color, label, dash }) {
  return (
    <span className="inline-flex items-center gap-1 text-slate-700">
      <svg width="16" height="8" aria-hidden>
        <line x1="0" y1="4" x2="16" y2="4" stroke={color} strokeWidth="2.5" strokeDasharray={dash || "0"} />
      </svg>
      {label}
    </span>
  );
}
function Line2({ points, color = C.price, dash, width = 2 }) {
  return (
    <polyline
      points={poly(points)}
      fill="none"
      stroke={color}
      strokeWidth={width}
      strokeDasharray={dash || "0"}
      strokeLinejoin="round"
      strokeLinecap="round"
    />
  );
}
function HL({ y, color, dash = "4 3" }) {
  return <line x1={fx(0)} x2={fx(100)} y1={fy(y)} y2={fy(y)} stroke={color} strokeWidth="1.5" strokeDasharray={dash} />;
}
function Dot({ x, y, color, r = 4 }) {
  // 겹치는 선 위에서도 떨어져 보이도록 캔버스색 링을 두른다. `#fff` 하드코딩은
  // 다크에서 흰 테두리가 되어 오히려 튀었다.
  return (
    <circle cx={fx(x)} cy={fy(y)} r={r * 1.4} fill={color}
      stroke="rgb(var(--c-slate-50))" strokeWidth="1.5" />
  );
}
function Tag({ x, y, text, color, dy = -9, anchor = "middle" }) {
  return (
    <text x={fx(x)} y={fy(y) + dy * 1.5} fontSize="13" fill={color} textAnchor={anchor} fontWeight="700">
      {text}
    </text>
  );
}

// --- 규칙별 일러스트 -------------------------------------------------------
const KEY_PRICE = <Key color={C.price} label="가격" />;

function FigA() {
  const price = [[4, 38], [14, 52], [24, 66], [30, 71], [40, 54], [50, 46], [60, 39], [70, 47], [82, 58], [96, 64]];
  return (
    <Fig caption="평단 대비 +X%(익절선)에 닿으면 팔고, -Y%(손절선)에 닿으면 자르고, 자리가 비면 다시 진입해요."
      legend={<><Key color={C.up} label="익절선" dash="4 3" /><Key color={C.down} label="손절선" dash="4 3" />{KEY_PRICE}</>}>
      <HL y={66} color={C.up} />
      <HL y={42} color={C.down} />
      <Line2 points={price} />
      <Dot x={4} y={38} color={C.price} r={3} /><Tag x={4} y={38} text="진입" color={C.price} dy={16} anchor="start" />
      <Dot x={30} y={71} color={C.up} /><Tag x={30} y={71} text="익절" color={C.up} />
      <Dot x={60} y={39} color={C.down} /><Tag x={60} y={39} text="손절" color={C.down} dy={16} />
      <Dot x={70} y={47} color={C.price} r={3} /><Tag x={70} y={47} text="재진입" color={C.price} />
    </Fig>
  );
}

function FigB() {
  const price = [[4, 50], [16, 36], [28, 40], [40, 58], [52, 66], [64, 52], [76, 37], [88, 46], [96, 58]];
  return (
    <Fig caption="정한 가격(매수가) 이하로 내려오면 사고, 정한 가격(매도가) 이상으로 오르면 팔아요."
      legend={<><Key color={C.up} label="매수가(이하 매수)" dash="4 3" /><Key color={C.down} label="매도가(이상 매도)" dash="4 3" />{KEY_PRICE}</>}>
      <HL y={64} color={C.down} />
      <HL y={37} color={C.up} />
      <Line2 points={price} />
      <Dot x={16} y={36} color={C.up} /><Tag x={16} y={36} text="매수" color={C.up} dy={16} />
      <Dot x={52} y={66} color={C.down} /><Tag x={52} y={66} text="매도" color={C.down} />
      <Dot x={76} y={37} color={C.up} /><Tag x={76} y={37} text="매수" color={C.up} dy={16} />
    </Fig>
  );
}

function FigC() {
  const price = [[4, 62], [20, 48], [36, 38], [52, 34], [68, 42], [84, 54], [96, 60]];
  const buys = [8, 26, 44, 62, 80];
  const yAt = (x) => {
    // 대략적인 위치만: 가격선 근처에 매수 점을 찍기 위한 보간
    for (let i = 1; i < price.length; i++) {
      const [x0, y0] = price[i - 1], [x1, y1] = price[i];
      if (x >= x0 && x <= x1) return y0 + ((y1 - y0) * (x - x0)) / (x1 - x0);
    }
    return price[price.length - 1][1];
  };
  return (
    <Fig caption="가격이 오르든 내리든 상관없이, 정해진 간격(N일)마다 같은 금액을 계속 사서 평단을 시간에 분산해요."
      legend={<><Key color={C.up} label="정기 매수" />{KEY_PRICE}<Key color={C.band} label="평단(평균 매입가)" dash="4 3" /></>}>
      <HL y={46} color={C.band} />
      <Line2 points={price} />
      {buys.map((x, i) => (
        <g key={i}>
          <line x1={fx(x)} x2={fx(x)} y1={fy(yAt(x))} y2={fy(10)} stroke={C.up} strokeWidth="1" strokeDasharray="2 2" opacity="0.5" />
          <Dot x={x} y={yAt(x)} color={C.up} r={3.5} />
        </g>
      ))}
      <Tag x={46} y={46} text="평단" color={C.band} dy={-6} />
    </Fig>
  );
}

function FigD() {
  const grids = [30, 40, 50, 60, 70];
  const price = [[4, 55], [14, 44], [24, 52], [34, 38], [46, 48], [56, 34], [68, 46], [80, 58], [90, 50], [96, 62]];
  return (
    <Fig caption="상·하단 구간을 격자로 나눠, 한 칸 내려가면 사고 한 칸 오르면 파는 걸 반복해요. 위아래로 흔들리는 횡보장에서 잔수익을 모아요."
      legend={<><Key color={C.grid} label="그리드 선" /><Key color={C.up} label="매수" /><Key color={C.down} label="매도" /></>}>
      {grids.map((y, i) => <HL key={i} y={y} color={C.grid} dash="0" />)}
      <Line2 points={price} />
      <Dot x={14} y={44} color={C.up} r={3.5} />
      <Dot x={24} y={52} color={C.down} r={3.5} />
      <Dot x={34} y={38} color={C.up} r={3.5} />
      <Dot x={46} y={48} color={C.down} r={3.5} />
      <Dot x={56} y={34} color={C.up} r={3.5} />
      <Dot x={80} y={58} color={C.down} r={3.5} />
    </Fig>
  );
}

function FigE() {
  const price = [[6, 34], [20, 48], [34, 60], [46, 70], [58, 75], [70, 58], [82, 48], [92, 50]];
  const trail = [[6, 24], [20, 38], [34, 50], [46, 60], [58, 65], [92, 65]];
  return (
    <Fig caption="+X% 이익이 난 뒤부터 '따라 올라가는 손절선'을 두고, 고점 대비 Y% 빠지면 청산해 이익을 지켜요."
      legend={<><Key color={C.band} label="트레일링 스탑(따라 오름)" dash="4 3" />{KEY_PRICE}</>}>
      <Line2 points={trail} color={C.band} dash="4 3" width={1.6} />
      <Line2 points={price} />
      <Dot x={58} y={75} color={C.up} /><Tag x={58} y={75} text="고점" color={C.up} />
      <Dot x={66} y={65} color={C.down} /><Tag x={66} y={65} text="청산" color={C.down} dy={16} />
    </Fig>
  );
}

function FigF() {
  const rsi = [[4, 52], [16, 34], [24, 22], [33, 30], [42, 50], [54, 68], [63, 79], [73, 66], [84, 50], [96, 44]];
  return (
    <Fig caption="RSI는 0~100 사이 '과열 온도계'예요. 30 아래(과매도)면 진입, 70 위(과매수)면 청산하는 대표적 지표 전략."
      legend={<><Key color={C.up} label="30 · 과매도" dash="4 3" /><Key color={C.down} label="70 · 과매수" dash="4 3" /><Key color={C.band} label="RSI" /></>}>
      <HL y={70} color={C.down} />
      <HL y={30} color={C.up} />
      <Line2 points={rsi} color={C.band} />
      <Dot x={33} y={30} color={C.up} /><Tag x={33} y={30} text="진입" color={C.up} dy={16} />
      <Dot x={63} y={79} color={C.down} /><Tag x={63} y={79} text="청산" color={C.down} />
      <text x={fx(2)} y={fy(70) - 5} fontSize="12" fill={C.down} fontWeight="600">70</text>
      <text x={fx(2)} y={fy(30) + 16} fontSize="12" fill={C.up} fontWeight="600">30</text>
    </Fig>
  );
}

function FigG() {
  const mid = [[4, 50], [18, 53], [32, 49], [46, 50], [60, 54], [74, 50], [88, 49], [96, 50]];
  const up = mid.map(([x, y]) => [x, y + 14]);
  const lo = mid.map(([x, y]) => [x, y - 14]);
  const price = [[4, 50], [14, 40], [22, 37], [32, 48], [40, 55], [50, 50], [60, 42], [70, 38], [80, 50], [88, 56], [96, 52]];
  return (
    <Fig caption="가운데선(이동평균) 위·아래로 변동성만큼 밴드를 그려요. 하단 터치에 사서 중앙선/상단에서 파는 '역추세'가 기본형(strategy=reversion)."
      legend={<><Key color={C.band} label="상·하단 밴드" /><Key color={C.band2} label="중앙선(MA)" dash="4 3" />{KEY_PRICE}</>}>
      <Line2 points={up} color={C.band} width={1.5} />
      <Line2 points={lo} color={C.band} width={1.5} />
      <Line2 points={mid} color={C.band2} dash="4 3" width={1.4} />
      <Line2 points={price} />
      <Dot x={22} y={37} color={C.up} /><Tag x={22} y={37} text="하단→매수" color={C.up} dy={16} />
      <Dot x={40} y={55} color={C.down} /><Tag x={40} y={55} text="중앙→매도" color={C.down} />
      <Dot x={70} y={38} color={C.up} /><Tag x={70} y={38} text="매수" color={C.up} dy={16} />
    </Fig>
  );
}

function FigH() {
  const price = [[6, 72], [20, 62], [34, 53], [48, 46], [62, 42], [74, 50], [86, 58], [96, 62]];
  const avg = [[6, 72], [20, 66], [34, 60], [48, 55], [62, 51], [96, 51]];
  return (
    <Fig caption="가격이 내려갈수록 더 크게 추가 매수(물타기·세이프티오더)해 평단을 끌어내리고, 평단 대비 +X%에 한 번에 익절해요. 자금 관리가 생명."
      legend={<><Key color={C.up} label="매수(내려갈수록 크게)" /><Key color={C.band} label="평단" dash="4 3" />{KEY_PRICE}</>}>
      <Line2 points={avg} color={C.band} dash="4 3" width={1.6} />
      <Line2 points={price} />
      <Dot x={6} y={72} color={C.up} r={3} />
      <Dot x={20} y={62} color={C.up} r={4} />
      <Dot x={34} y={53} color={C.up} r={5} />
      <Dot x={48} y={46} color={C.up} r={6} />
      <Dot x={62} y={42} color={C.up} r={7} /><Tag x={62} y={42} text="추가매수" color={C.up} dy={18} />
      <Dot x={80} y={54} color={C.down} /><Tag x={80} y={55} text="평단+X% 익절" color={C.down} anchor="end" />
    </Fig>
  );
}

function FigI() {
  const price = [[6, 44], [18, 43], [30, 46], [42, 45], [52, 50], [60, 58], [72, 68], [84, 72], [94, 66]];
  return (
    <Fig caption="'오늘 시가 + k×(전일 변동폭)'을 돌파선으로 잡고, 그 위로 뚫으면 따라 들어가요(래리 윌리엄스 변동성 돌파)."
      legend={<><Key color={C.band} label="돌파선(시가+k×전일폭)" dash="4 3" /><Key color={C.up} label="돌파 진입" />{KEY_PRICE}</>}>
      <HL y={54} color={C.band} />
      <Line2 points={price} />
      <Dot x={57} y={54} color={C.up} /><Tag x={57} y={54} text="돌파 → 진입" color={C.up} anchor="start" dy={-9} />
      <Tag x={6} y={44} text="시가" color={C.price} dy={16} anchor="start" />
    </Fig>
  );
}

function FigJ() {
  const slow = [[4, 52], [24, 51], [44, 52], [64, 55], [84, 57], [96, 57]];
  const fast = [[4, 44], [18, 47], [30, 52], [44, 58], [58, 60], [72, 55], [86, 49], [96, 46]];
  const price = [[4, 46], [14, 42], [24, 50], [34, 56], [44, 61], [54, 63], [64, 58], [74, 52], [84, 47], [96, 45]];
  return (
    <Fig caption="단기 이평선이 장기 이평선을 위로 뚫으면(골든크로스) 진입, 아래로 뚫으면(데드크로스) 청산. 추세를 타는 대표 전략."
      legend={<><Key color={C.band} label="단기 이평선" /><Key color={C.band2} label="장기 이평선" /><Key color={C.price} label="가격" dash="2 2" /></>}>
      <Line2 points={price} color={C.price} dash="2 2" width={1.2} />
      <Line2 points={slow} color={C.band2} width={2} />
      <Line2 points={fast} color={C.band} width={2} />
      <Dot x={41} y={55} color={C.up} /><Tag x={41} y={55} text="골든크로스" color={C.up} dy={16} />
      <Dot x={79} y={52} color={C.down} /><Tag x={79} y={53} text="데드크로스" color={C.down} />
    </Fig>
  );
}

function FigK() {
  const longSeg = [[4, 40], [16, 52], [28, 62], [38, 66], [46, 58]];
  const shortSeg = [[46, 58], [56, 48], [68, 40], [76, 42]];
  const long2 = [[76, 42], [86, 50], [96, 56]];
  return (
    <Fig caption="롱을 들고 있다가 고점 대비 하락선을 깨면 일부 매도 후 숏으로 전환(방어), 더 빠지면 숏에서 이익. 바닥에서 다시 롱으로. 선물 전용."
      legend={<><Key color={C.up} label="롱 구간" /><Key color={C.down} label="숏 전환 구간" /><Key color={C.band} label="하락 전환선" dash="4 3" /></>}>
      <HL y={58} color={C.band} />
      <Line2 points={longSeg} color={C.up} width={2.4} />
      <Line2 points={shortSeg} color={C.down} width={2.4} />
      <Line2 points={long2} color={C.up} width={2.4} />
      <Dot x={38} y={66} color={C.up} /><Tag x={38} y={66} text="고점" color={C.up} />
      <Dot x={46} y={58} color={C.down} /><Tag x={46} y={58} text="숏 전환" color={C.down} dy={16} />
      <Dot x={76} y={42} color={C.up} /><Tag x={76} y={42} text="롱 재진입" color={C.up} dy={16} />
    </Fig>
  );
}

// ---------------------------------------------------------------------------
// 규칙 A~K 하위 페이지
// ---------------------------------------------------------------------------
const RULE_PAGES = [
  {
    id: "rule-a", letter: "A", name: "익절/손절 후 재진입",
    text: "A 익절 손절 재진입 take_profit_pct stop_loss 기본형 목표수익 리스크",
    body: (
      <>
        <P>가장 기본이 되는 전략이에요. 사고 나서 <b>평단 대비 +X% 오르면 익절</b>, <b>-Y% 내리면 손절</b>하고, 포지션이 비면 다시 같은 규칙으로 진입해요.</P>
        <FigA />
        <Params rows={[
          ["take_profit_pct", "익절 목표 수익률(%). 예: 5면 평단보다 5% 오르면 팔아요."],
          ["stop_loss_pct", "손절 기준(%). 공통 리스크 관리에서 설정. 미사용 체크를 풀면 손절 없이 익절만."],
          ["invest_ratio_pct", "한 번에 자금의 몇 %를 넣을지."],
        ]} />
        <GoodBad
          good="목표·손절을 딱 정해두고 규칙적으로 사고파는 습관을 들이고 싶을 때. 모든 전략의 출발점."
          bad="추세가 크게 나올 때 +5%에 팔아버리면 그 뒤 상승을 놓쳐요(익절이 너무 빠르면 손익비가 나빠짐)."
        />
        <Tip>처음이라면 A부터 돌려보며 익절·손절 숫자만 바꿔 결과가 어떻게 달라지는지 감을 잡는 걸 추천해요.</Tip>
      </>
    ),
  },
  {
    id: "rule-b", letter: "B", name: "지정가 밴드 매매",
    text: "B 지정가 밴드 매수가 매도가 buy_price sell_price 싸게사서 비싸게",
    body: (
      <>
        <P>"이 가격 이하면 사고, 이 가격 이상이면 판다"를 미리 정해두는 가장 직관적인 규칙이에요.</P>
        <FigB />
        <Params rows={[
          ["buy_price", "이 가격 이하로 내려오면 매수."],
          ["sell_price", "이 가격 이상으로 오르면 매도."],
        ]} />
        <GoodBad
          good="일정한 가격대(박스권)를 오르내리는 종목에서, 지지·저항을 눈으로 정해 매매하고 싶을 때."
          bad="가격이 밴드를 뚫고 한 방향으로 계속 가면(추세장), 산 뒤 계속 내리거나 판 뒤 계속 올라 불리해요."
        />
      </>
    ),
  },
  {
    id: "rule-c", letter: "C", name: "정기 분할매수 (DCA)",
    text: "C DCA 분할매수 적립식 amount_per_buy interval_days 평단 물타기 롱전용",
    body: (
      <>
        <P>가격을 예측하지 않고, <b>정해진 간격마다 같은 금액을 꾸준히 사는</b> 적립식 전략이에요. 살 때마다 평단이 시간에 분산돼요. (롱 전용)</P>
        <FigC />
        <Params rows={[
          ["amount_per_buy", "한 번에 매수할 금액(USDT)."],
          ["interval_days", "며칠마다 살지. 예: 7이면 매주 1회."],
        ]} />
        <GoodBad
          good="타이밍을 못 맞추겠고 길게 모아가고 싶을 때. 심리적 부담이 가장 적은 방식."
          bad="계속 우하향만 하는 종목이면 사도 사도 손실이 누적돼요(하락장에서 무한 매수 주의)."
        />
      </>
    ),
  },
  {
    id: "rule-d", letter: "D", name: "그리드 매매",
    text: "D 그리드 격자 grid_count lower_price upper_price 횡보 박스권 자동매매",
    body: (
      <>
        <P>상·하단 사이를 격자(그리드)로 잘게 나눠, <b>한 칸 내려가면 사고 한 칸 오르면 파는</b> 걸 자동 반복해요. 위아래로 흔들리는 장에서 잔수익을 쌓아요.</P>
        <FigD />
        <Params rows={[
          ["lower_price / upper_price", "그리드를 깔 하단·상단 가격."],
          ["grid_count", "격자 칸 수. 많을수록 촘촘하게 자주 매매."],
          ["grid_mode", "arithmetic(등간격) / geometric(등비율)."],
          ["band_exit_action", "가격이 밴드를 벗어났을 때의 처리(정지 등)."],
        ]} />
        <GoodBad
          good="뚜렷한 방향 없이 박스권을 오르내리는 횡보장. 그리드 매매가 가장 빛나는 구간이에요."
          bad="밴드 아래로 계속 빠지면(추세 하락) 산 물량이 모두 물려요. 상·하단 설정이 핵심."
        />
      </>
    ),
  },
  {
    id: "rule-e", letter: "E", name: "트레일링 스탑",
    text: "E 트레일링 스탑 trailing activation_profit trail_percent 고점 추적 이익보호",
    body: (
      <>
        <P>이익이 어느 정도 난 뒤부터 <b>고점을 따라 올라가는 손절선</b>을 두고, 고점 대비 정해진 % 빠지면 청산해요. "번 이익은 지키되, 더 갈 땐 같이 간다".</P>
        <FigE />
        <Params rows={[
          ["activation_profit", "이 수익률(%)에 도달해야 트레일링이 켜져요."],
          ["trail_percent", "고점 대비 몇 % 빠지면 청산할지."],
          ["entry_mode / entry_dip", "즉시 진입 / 눌림목(entry_dip%)에서 진입."],
        ]} />
        <GoodBad
          good="추세가 크게 나오는 장에서 수익을 끝까지 따라가며 지키고 싶을 때."
          bad="잔파도가 심하면 살짝 눌릴 때마다 청산돼 이익이 잘려요(trail_percent가 너무 좁을 때)."
        />
      </>
    ),
  },
  {
    id: "rule-f", letter: "F", name: "RSI 조건 매매",
    text: "F RSI 과매도 과매수 rsi_period entry_threshold exit_threshold 지표 오실레이터",
    body: (
      <>
        <P>RSI는 최근 상승·하락의 힘을 0~100으로 나타내는 <b>과열 온도계</b>예요. 낮으면(과매도) 진입, 높으면(과매수) 청산하는 지표 전략.</P>
        <FigF />
        <Params rows={[
          ["rsi_period", "RSI 계산 기간(보통 14)."],
          ["entry_threshold", "이 값 아래로 내려오면 진입(예: 30)."],
          ["exit_threshold", "이 값 위로 오르면 청산(예: 70)."],
          ["exit_mode / take_profit", "지표로만 청산할지, 익절(%)도 함께 쓸지."],
        ]} />
        <GoodBad
          good="박스권에서 과하게 빠졌다 되돌아오는 '되돌림'을 노릴 때."
          bad="강한 추세장에선 RSI가 과매수(70+)에 오래 머물러, 일찍 팔고 상승을 놓칠 수 있어요."
        />
      </>
    ),
  },
  {
    id: "rule-g", letter: "G", name: "볼린저밴드",
    text: "G 볼린저 밴드 bollinger bb_period bb_std 역추세 reversion 돌파 스퀴즈 변동성",
    body: (
      <>
        <P>가운데 이동평균선 위·아래로 <b>변동성(표준편차)만큼</b> 밴드를 그려요. 가격이 밴드 하단에 닿으면 싸다고 보고 사서 중앙선/상단에서 파는 <b>역추세</b>가 기본형이에요.</P>
        <FigG />
        <Params rows={[
          ["bb_period", "중앙선(이동평균) 기간(보통 20)."],
          ["bb_std", "밴드 폭 = 표준편차 몇 배(보통 2.0). 클수록 밴드가 넓어요."],
          ["strategy", "reversion(역추세) / breakout(밴드 돌파 추종)."],
          ["exit_target", "청산 목표: mid(중앙선) / upper(상단)."],
          ["squeeze_filter", "밴드가 좁아진(변동성 수축) 구간만 노릴지."],
        ]} />
        <GoodBad
          good="역추세(reversion): 박스권에서 밴드 하단·상단을 오갈 때. 돌파(breakout): 오래 눌렸다 터지는 변동성 확장을 노릴 때."
          bad="추세장에서 역추세로 쓰면 '싼 줄 알고 샀는데 더 싸지는' 상황이 반복돼요. strategy 선택이 중요."
        />
        <Tip>같은 볼린저라도 <b>reversion</b>과 <b>breakout</b>은 정반대로 움직여요. 두 개를 각각 백테스트해 비교해보면 차이가 확 보여요.</Tip>
      </>
    ),
  },
  {
    id: "rule-h", letter: "H", name: "마틴게일 / 세이프티오더",
    text: "H 마틴게일 세이프티오더 물타기 base_order safety_order price_deviation max_safety_orders 평단 자금관리",
    body: (
      <>
        <P>기본 주문 후 가격이 내려갈 때마다 <b>더 큰 금액으로 추가 매수(물타기)</b>해 평단을 끌어내리고, 평단 대비 +X%에 한 번에 익절해요. 강력하지만 <b>자금 관리가 생명</b>이에요.</P>
        <FigH />
        <Params rows={[
          ["base_order_size", "첫 진입 금액."],
          ["safety_order_size", "첫 추가매수 금액."],
          ["price_deviation", "몇 % 더 빠질 때마다 추가매수할지."],
          ["safety_order_volume_scale", "추가매수마다 금액을 몇 배로 키울지(예: 2.0)."],
          ["max_safety_orders", "최대 추가매수 횟수(총알 개수)."],
          ["take_profit", "평단 대비 익절률(%)."],
        ]} />
        <GoodBad
          good="일시적으로 빠졌다 다시 오르는 종목에서 평단을 낮춰 반등에 익절하고 싶을 때."
          bad="계속 하락하면 총알(max_safety_orders)이 바닥나고 큰 물량이 물려요. 레버리지와 겹치면 청산 위험 급증. 반드시 감당 가능한 자금 안에서."
        />
        <Note><b>주의:</b> 마틴게일은 '작은 수익을 자주, 큰 손실을 가끔' 내는 구조예요. 한 번의 큰 하락이 그동안의 수익을 다 지울 수 있어요.</Note>
      </>
    ),
  },
  {
    id: "rule-i", letter: "I", name: "변동성 돌파",
    text: "I 변동성 돌파 breakout k 래리윌리엄스 전일 변동폭 시가 돌파선 session",
    body: (
      <>
        <P>"오늘 시가 + <b>k × 전일 변동폭</b>"을 돌파선으로 잡고, 그 위로 뚫으면 <b>추세가 시작됐다</b> 보고 따라 들어가는 전략이에요(래리 윌리엄스).</P>
        <FigI />
        <Params rows={[
          ["k", "전일 변동폭의 몇 배를 돌파 기준으로 삼을지(보통 0.5). 작을수록 자주 진입."],
          ["exit_mode", "청산 방식: next_open(다음날 시가) / take_profit / trailing."],
          ["trail_percent / take_profit", "청산방식에 따른 추적 폭 / 익절률."],
          ["ma_filter_period", "이 이평선 위일 때만 진입(추세 필터, 선택)."],
        ]} />
        <GoodBad
          good="장 초반 힘이 실려 하루 방향이 크게 나오는 날을 노릴 때. 규칙이 단순해 검증하기 쉬워요."
          bad="자잘하게 위아래로 속이는 장(가짜 돌파)에선 진입 후 바로 밀려요. k와 필터로 걸러야 해요."
        />
      </>
    ),
  },
  {
    id: "rule-j", letter: "J", name: "이동평균 크로스",
    text: "J 이동평균 크로스 골든크로스 데드크로스 fast_period slow_period ma_type SMA EMA 추세추종",
    body: (
      <>
        <P>단기 이평선이 장기 이평선을 <b>위로 뚫으면(골든크로스) 진입</b>, <b>아래로 뚫으면(데드크로스) 청산</b>해요. 가장 널리 쓰이는 추세추종 신호예요.</P>
        <FigJ />
        <Params rows={[
          ["fast_period / slow_period", "단기·장기 이평선 기간. 단기 < 장기 여야 해요(예: 20 / 60)."],
          ["ma_type", "SMA(단순) / EMA(지수, 최근값에 가중)."],
          ["entry_signal / exit_signal", "진입·청산 신호(golden_cross / dead_cross / take_profit 등)."],
          ["confirm_candles", "신호 후 몇 봉 유지되면 확정할지(속임수 완화)."],
        ]} />
        <GoodBad
          good="한 방향으로 길게 가는 추세장. 큰 흐름을 놓치지 않고 올라타기 좋아요."
          bad="횡보장에선 골든·데드가 번갈아 뜨며 자잘한 손절(휩쏘)이 반복돼요."
        />
      </>
    ),
  },
  {
    id: "rule-k", letter: "K", name: "하락 방어 전환 (SAR)",
    text: "K SAR 하락방어 전환 flip_to_short drop_trigger_pct partial_exit 롱 숏 전환 선물전용 헤지",
    body: (
      <>
        <P>롱을 들고 있다가 <b>고점 대비 정해진 % 하락하면</b> 일부를 팔고 <b>숏으로 전환</b>해 하락을 방어(오히려 이익)하고, 바닥에서 다시 롱으로 돌아와요. <b>선물 전용</b>이에요.</P>
        <FigK />
        <Params rows={[
          ["drop_trigger_pct", "고점 대비 몇 % 빠지면 방어를 발동할지."],
          ["partial_exit_pct", "발동 시 롱을 몇 % 정리할지."],
          ["flip_to_short", "정리 후 숏으로 전환할지 여부."],
          ["short_take_profit_pct / short_stop_loss_pct", "숏의 익절·손절(%)."],
          ["reenter_long_after", "숏 종료 후 다시 롱으로 복귀할지."],
        ]} />
        <GoodBad
          good="급락 구간에서 손실을 방어하거나 하락에서도 수익을 내고 싶을 때(방향 전환형)."
          bad="위아래로 흔드는 장에선 전환이 잦아 양쪽에서 손절날 수 있어요. 선물·숏이라 청산 위험도 함께 고려."
        />
        <Note><b>주의:</b> 숏·선물이 포함돼 레버리지 청산 위험이 있어요. 코린이라면 개념을 이해한 뒤 낮은 배수(1~2배)로만 실험하세요.</Note>
      </>
    ),
  },
];

// ---------------------------------------------------------------------------
const BASE_SECTIONS = [
  {
    id: "start",
    title: "껄무새가 뭐예요?",
    text: "껄무새 소개 교육 모의 페이퍼 백테스트 실거래 아님 코린이 입문",
    body: (
      <>
        <P>
          껄무새는 <b>실제 돈을 넣지 않고</b> 코인 매매 전략(매크로)을 만들어보고, 과거 데이터로
          돌려보고(<b>백테스트</b>), 실시간 모의로 굴려보는(<b>페이퍼 트레이딩</b>) <b>교육용 놀이터</b>예요.
        </P>
        <P>목표는 "돈 잃지 않고 배우기". 여기서 마음껏 실패하면서 감을 잡는 게 핵심이에요.</P>
        <Note><b>주의:</b> 웹 화면의 모든 수치는 과거·모의 시뮬레이션 결과예요. 투자 조언이 아니고, 수익을 보장하지 않아요.</Note>
      </>
    ),
  },
  {
    id: "buy-transfer",
    title: "코인 사서 바이낸스로 옮기기 (한국 투자자)",
    text: "업비트 빗썸 트론 이더리움 전송 바이낸스 지갑 usdt 매수 출금 네트워크 수수료 김프",
    body: (
      <>
        <P>
          한국 거래소(업비트·빗썸)에서는 <b>원화로 코인</b>을 사고, 해외 거래소(바이낸스)에서는 보통
          <b> USDT</b>로 매매해요. 그래서 "원화 → 코인 → 바이낸스로 전송 → USDT" 흐름을 많이 씁니다.
        </P>
        <H>보통의 흐름</H>
        <Steps
          items={[
            "업비트/빗썸에서 원화로 전송이 빠르고 수수료가 싼 코인(예: 트론 TRX, 리플 XRP, 이더리움 ETH)을 매수해요.",
            "바이낸스에서 그 코인의 '입금(Deposit)' 주소 + 네트워크를 확인해요. (예: TRX는 TRON 네트워크)",
            "업비트/빗썸의 '출금'에서 바이낸스 입금 주소로 보내요. 네트워크가 양쪽 동일한지 꼭 확인!",
            "몇 분~하루 안에 바이낸스 지갑에 코인이 들어오면, 그 코인을 팔아 USDT로 바꿔요.",
            "이제 그 USDT로 원하는 코인을 매매하면 됩니다.",
          ]}
        />
        <Note>
          <b>주의: 네트워크·주소를 틀리면 자산이 사라질 수 있어요.</b> 첫 전송은 꼭 소액으로 테스트하세요.
          전송 코인/네트워크 선택, 수수료, 한국 거래소의 트래블룰(수취인 확인)도 미리 확인하세요.
          이건 절차 설명일 뿐 특정 코인 추천이 아니고, 실제 자산 이동 책임은 본인에게 있어요.
        </Note>
        <P>
          참고로 한국 가격이 해외보다 비싼 정도를 <b>김치 프리미엄(김프)</b>이라고 해요. 껄무새 상단에서
          김프를 참고용으로 볼 수 있어요.
        </P>
      </>
    ),
  },
  {
    id: "long-short",
    title: "롱 포지션 / 숏 포지션이 뭐예요?",
    text: "롱 숏 포지션 공매도 상승 하락 현물 선물 레버리지",
    body: (
      <>
        <H>롱(Long) = 오르면 이익</H>
        <P>싸게 사서 비싸게 파는 거예요. 가격이 <b>오르면</b> 이익. 현물 매수도 롱이에요.</P>
        <H>숏(Short) = 내리면 이익</H>
        <P>
          빌려서 먼저 팔고 나중에 싸게 되사는 방식(공매도). 가격이 <b>내리면</b> 이익이에요. 숏은
          <b> 선물</b>에서만 가능하고, 오르면 손실이 이론상 무제한이라 <b>손절이 필수</b>예요.
        </P>
        <Note>
          <b>주의: 레버리지</b>는 수익도 손실도 배로 키워요. 예: 10배면 가격이 약 10%만 반대로 움직여도
          <b> 청산(투입금 전액 손실)</b>돼요. 코린이라면 레버리지는 낮게(1~3배) 또는 안 쓰는 걸 권해요.
        </Note>
      </>
    ),
  },
  {
    id: "rules",
    title: "규칙(전략) A~K 설명",
    text: "규칙 전략 A B C D E F G H I J K 익절 손절 DCA 그리드 트레일링 RSI 볼린저 마틴게일 변동성돌파 이동평균 골든크로스 SAR " +
      RULE_PAGES.map((r) => r.name + " " + r.text).join(" "),
    body: (go) => (
      <>
        <P>껄무새 빌더에서 고를 수 있는 매매 규칙(전략)이에요. 하나를 고르고 숫자(파라미터)만 조정하면 매크로가 완성돼요. 각 항목을 눌러 자세한 설명·차트 예시를 보세요.</P>
        {/* 카드 목록이 아니라 괘선 리스트 — 행 제목 17/700, chevron 만 장식색. */}
        <div className="border-t border-slate-200">
          {RULE_PAGES.map((r) => (
            <button
              key={r.id}
              onClick={() => go(r.id)}
              className="w-full text-left border-b border-slate-200 py-4 px-2 -mx-2 rounded-lg hover:bg-slate-100"
            >
              <div className="flex items-center justify-between gap-3">
                <span className="t-title text-slate-900">
                  <span className="inline-block w-6 num text-slate-500">{r.letter}</span> {r.name}
                </span>
                <span className="t-small font-semibold text-slate-400 shrink-0">자세히 →</span>
              </div>
            </button>
          ))}
        </div>
        <Note>어떤 걸 골라야 할지 모르겠다면, <b>A(익절/손절)</b>나 <b>C(DCA)</b>처럼 단순한 것부터 백테스트해보며 감을 잡아요.</Note>
      </>
    ),
  },
  // 규칙 A~K 하위 페이지
  ...RULE_PAGES.map((r) => ({
    id: r.id,
    title: `${r.letter} · ${r.name}`,
    sub: true,
    text: r.text,
    body: r.body,
  })),
  {
    id: "read-result",
    title: "백테스트 결과 읽는 법",
    text: "백테스트 수익률 MDD 최대낙폭 승률 샤프 손익비 홀딩 HODL 페이퍼 자산곡선",
    body: (
      <>
        <P>백테스트는 과거 데이터로 이 전략을 돌렸다면 어땠을지 보여줘요. 숫자 뜻만 알면 절반은 이해한 거예요.</P>
        <Steps
          items={[
            "수익률: 기간 끝 최종 손익. '그냥 홀딩(HODL)'보다 나은지 함께 봐요.",
            "MDD(최대낙폭): 가는 길에 최고점 대비 얼마나 깊게 빠졌나. 수익률만큼 중요해요 — 실제로 버텨야 하니까.",
            "승률 / 손익비(PF): 몇 번 중 이겼나 / 번 돈 대비 잃은 돈 비율.",
            "샤프지수: 변동성(위험) 대비 수익. 1 이상이면 준수.",
            "최대 연속손절: 몇 번 연속으로 손절났나 — 심리적으로 버티기 힘든 구간의 신호.",
          ]}
        />
        <P>결과 밑의 <b>🦜 껄무새 AI 해설</b>을 누르면 "왜 이렇게 나왔는지 + 이 매크로를 쓴다면" 관점을 쉽게 정리해줘요.</P>
        <Note><b>주의:</b> 백테스트가 좋아도 '과거의 한 구간'일 뿐이에요. 다른 기간·다른 종목에서도 되는지 확인하는 습관(과최적화 주의)이 중요해요.</Note>
      </>
    ),
  },
  {
    id: "leaderboard",
    title: "리더보드 · 포인트 · 언락",
    text: "리더보드 포인트 언락 등록 수익률 경쟁 왕관 판매 70% 마이페이지 AI 챌린지",
    body: (
      <>
        <P>
          <b>오늘의 리더보드</b>에 내 매크로를 등록하면 실시간 모의 수익률로 다른 사람들과 겨뤄요. 매일
          자정(KST)에 초기화돼요.
        </P>
        <Steps
          items={[
            "회원가입하면 스타터 포인트를 줘요(헤더에서 잔액 확인).",
            "남의 매크로는 아이디·종목·등락률만 보이고, 포인트로 언락하면 전략과 설정이 공개·복사돼요.",
            "내 매크로를 남이 언락하면 그 포인트의 70%가 나에게 적립돼요.",
            "판매·좋아요가 쌓이면 인기 셀러로 표시돼요. 내 활동은 마이페이지에서 확인해요.",
          ]}
        />
        <Note>포인트는 서비스 내 가상 재화예요. 실거래/투자 자문이 아니에요.</Note>
      </>
    ),
  },
  {
    id: "faq",
    title: "코린이 자주 헷갈리는 것",
    text: "FAQ 자주 묻는 질문 실거래 되나요 왜 안 사요 조건 진입 청산 리플레이 라이브 종목 심볼 BTCUSDT 헷갈림 포트폴리오 멀티종목",
    body: (
      <>
        <H>실제로 돈이 움직이나요?</H>
        <P>
          웹의 백테스트·페이퍼·리더보드는 실제 주문을 보내지 않아요. 다만 페이퍼 화면 아래에서 내려받는
          실행 파일은 사용자 PC에서 거래소 API 키를 넣어 실행하며, 기본은 테스트넷이지만 설정을 바꾸면 실제 주문을 보낼 수 있어요.
        </P>
        <H>백테스트에서 매매가 0번이에요.</H>
        <P>진입 조건이 그 기간에 한 번도 안 맞은 거예요. 조건을 느슨하게 하거나 기간·봉 단위를 바꿔보세요.</P>
        <H>종목(symbol)은 어떻게 쓰나요?</H>
        <P>거래쌍 형식이에요. 예: 비트코인은 <b>BTCUSDT</b>, 이더리움은 <b>ETHUSDT</b>. 쉼표로 여러 개를 넣으면(<b>BTCUSDT, ETHUSDT</b>) 자금을 균등 분할하는 <b>포트폴리오</b>가 돼요.</P>
        <H>실시간 / 데모 리플레이 차이는?</H>
        <P>실시간은 지금 시세로, 데모 리플레이는 최근 시세를 빠르게 되감아 보여줘요(발표·데모용).</P>
        <Note>더 궁금한 게 있으면 상단 메뉴의 각 화면에서 ⓘ 아이콘을 눌러 용어 설명을 볼 수 있어요.</Note>
      </>
    ),
  },
];

const SECTIONS = BASE_SECTIONS;

export default function Guide({ embedded = false, initialSection = "start" }) {
  const [q, setQ] = useState("");
  const [activeId, setActiveId] = useState(() =>
    SECTIONS.some((section) => section.id === initialSection) ? initialSection : SECTIONS[0].id
  );
  const [tocOpen, setTocOpen] = useState(false); // mobile only; ≥md the list is always shown

  const filtered = useMemo(() => {
    const query = q.trim().toLowerCase();
    if (!query) return SECTIONS;
    return SECTIONS.filter(
      (s) => s.title.toLowerCase().includes(query) || s.text.toLowerCase().includes(query)
    );
  }, [q]);

  // Keep a valid active section as the filter changes.
  const active = filtered.find((s) => s.id === activeId) || filtered[0] || null;

  return (
    <div className={embedded ? "guide-embedded" : ""}>
      {embedded ? (
        <p className="guide-embedded-intro t-small text-slate-600">
          궁금한 단어를 검색하거나 매매 방식 A–K의 작동 원리를 확인해요.
        </p>
      ) : (
        <PageHeader
          eyebrow="용어부터 전략까지"
          title="사용 가이드"
          description="궁금한 단어를 검색하거나, 매매 방식 A~K의 작동 원리를 그림과 함께 확인해요."
          headingAs="h1"
        />
      )}
      <div className="grid md:grid-cols-[230px_1fr] gap-4 md:gap-6">
      {/* sidebar */}
      <aside className={(embedded ? "md:sticky md:top-0 " : "md:sticky md:top-20 ") + "self-start"}>
        <input
          value={q}
          aria-label="가이드 검색"
          onChange={(e) => setQ(e.target.value)}
          placeholder="가이드 검색…"
          className="field field-sm mb-3"
        />
        {/* On a phone the full contents list would push every article a screen
            and a half down, so it collapses behind the current section name. */}
        <button
          type="button"
          onClick={() => setTocOpen((v) => !v)}
          aria-expanded={tocOpen}
          className="btn btn-m btn-secondary md:hidden w-full mb-2 justify-between"
        >
          <span className="truncate">목차 · {active ? active.title : "문서 선택"}</span>
          <span className="shrink-0 text-slate-400">{tocOpen ? "▲" : "▼"}</span>
        </button>
        {/* 목차는 단일 선택 — 선택된 문서 하나만 노란 채움(화면당 하나, §1-4). */}
        <nav aria-label="가이드 목차" className={(tocOpen ? "block " : "hidden ") + "md:block space-y-1"}>
          {filtered.map((s) => (
            <button
              key={s.id}
              aria-current={active && active.id === s.id ? "page" : undefined}
              onClick={() => {
                setActiveId(s.id);
                setTocOpen(false);
              }}
              className={
                "w-full min-h-[44px] text-left rounded-[10px] t-small " +
                (s.sub ? "pl-6 pr-3 py-2 " : "px-3 py-3 ") +
                (active && active.id === s.id
                  ? "bg-brand text-brand-ink font-bold"
                  : "font-semibold text-slate-700 hover:bg-slate-100")
              }
            >
              {s.title}
            </button>
          ))}
          {filtered.length === 0 && (
            <div className="t-small text-slate-500 px-3 py-2">검색 결과가 없어요.</div>
          )}
        </nav>
      </aside>

      {/* content */}
      <article className="min-w-0">
        {active ? (
          <>
            {active.sub && (
              <button
                onClick={() => setActiveId("rules")}
                className="mb-3 t-small font-semibold text-slate-700 hover:text-slate-900"
              >
                ← 전략 목록으로
              </button>
            )}
            <h2 className="t-h2 text-slate-900 mb-4">{active.title}</h2>
            {typeof active.body === "function" ? active.body(setActiveId) : active.body}
          </>
        ) : (
          <div className="t-small text-slate-500">문서를 골라주세요.</div>
        )}
        <p className="mt-8 pt-4 border-t border-slate-200 t-caption text-slate-500">
          본 가이드는 교육용 정보이고 투자 조언이 아니에요. 실제 거래·자산 이동의 책임은 본인에게 있어요.
        </p>
      </article>
      </div>
    </div>
  );
}
