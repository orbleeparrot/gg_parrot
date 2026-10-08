import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import InfoTooltip from "./InfoTooltip.jsx";

// Upstream (Alternative.me) refreshes ~daily and the backend caches for ~1h, so
// this is only how often the browser re-reads the shared server value.
const POLL_MS = Number(import.meta.env?.VITE_FEARGREED_POLL_MS) || 600000; // 10min

// Colour by value: fear = red/amber, neutral = slate, greed = green. Returns
// Tailwind text/track classes so it themes with the rest of the app.
function tone(v) {
  if (v == null) return { text: "text-slate-700", bar: "bg-slate-400" };
  if (v < 25) return { text: "text-red-600", bar: "bg-red-500" };
  if (v < 45) return { text: "text-amber-700", bar: "bg-amber-500" };
  if (v < 55) return { text: "text-slate-700", bar: "bg-slate-400" };
  if (v < 75) return { text: "text-green-600", bar: "bg-green-600" };
  return { text: "text-green-700", bar: "bg-green-600" };
}

export default function FearGreedBanner() {
  const [data, setData] = useState(null);
  const [failed, setFailed] = useState(false);
  const timer = useRef(null);

  useEffect(() => {
    let alive = true;
    async function tick() {
      try {
        const d = await api.fearGreed();
        if (!alive) return;
        setData(d);
        setFailed(!d.ok);
      } catch (_) {
        if (alive) setFailed(true);
      }
    }
    tick();
    timer.current = setInterval(tick, POLL_MS);
    return () => {
      alive = false;
      clearInterval(timer.current);
    };
  }, []);

  // Upstream down and no cached value -> hide entirely so the layout never breaks.
  if (!data || !data.ok || data.value == null) {
    if (failed && data && !data.ok) {
      return (
        <div className="border-b border-slate-200">
          <div className="max-w-6xl mx-auto px-5 sm:px-6 py-2 t-caption text-slate-500">
            공포·탐욕 지수 정보 없음 (잠시 후 다시 시도)
          </div>
        </div>
      );
    }
    return null;
  }

  const v = data.value;
  const t = tone(v);

  return (
    <div className="border-b border-slate-200">
      <div className="max-w-6xl mx-auto px-5 sm:px-6 py-2 flex flex-wrap items-center gap-x-3 gap-y-1 t-caption">
        <span className="flex items-center font-semibold text-slate-700">
          공포·탐욕 지수
          <InfoTooltip term="fear_greed" placement="bottom" />
        </span>

        <span className={"t-label font-bold num " + t.text}>{v}</span>
        <span className={"font-semibold " + t.text}>{data.classification_ko}</span>

        {/* 0~100 mini gauge */}
        <span className="hidden sm:inline-block w-28 h-1.5 rounded-full bg-slate-200 overflow-hidden align-middle">
          <span className={"block h-full w-(--fill) " + t.bar} style={{ "--fill": `${Math.max(2, Math.min(v, 100))}%` }} />
        </span>

        <span className="text-slate-500">시장 전체 기준 (종목별 아님)</span>
        {data.stale && <span className="font-semibold text-amber-700">(최신 갱신 실패, 이전 값)</span>}

        <span className="text-slate-500 ml-auto hidden md:inline">
          <span className="num">0</span> 공포 ↔ <span className="num">100</span> 탐욕 · 참고용 (매매 신호 아님)
        </span>
      </div>
    </div>
  );
}
