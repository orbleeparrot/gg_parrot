import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import InfoTooltip from "./InfoTooltip.jsx";

// Poll interval (ms). Env-configurable; default 5 min (server caches too, so this
// is only how often the browser re-reads the shared server value).
const POLL_MS = Number(import.meta.env?.VITE_HANGANG_POLL_MS) || 300000;

// Light, GGparrot-tone comment by temperature band. Intentionally just a mild
// weather-style remark — no self-harm / "jump in" connotations of any kind.
function comment(t) {
  if (t == null) return "";
  if (t >= 20) return "물이 미지근하네요";
  if (t >= 10) return "슬슬 차가워져요";
  return "오늘은 집이 최고";
}

export default function HangangTempBanner() {
  const [data, setData] = useState(null);
  const [failed, setFailed] = useState(false);
  const timer = useRef(null);

  useEffect(() => {
    let alive = true;
    async function tick() {
      try {
        const d = await api.hangangTemp();
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

  // No data at all (upstream down and no cache) -> hide the widget entirely so
  // the existing layout is never broken.
  if (!data || !data.ok || data.temperature == null) {
    if (failed && data && !data.ok) {
      return (
        <div className="border-b border-slate-200">
          <div className="max-w-6xl mx-auto px-5 sm:px-6 py-2 t-caption text-slate-500">
            한강 수온 정보 없음 (잠시 후 다시 시도)
          </div>
        </div>
      );
    }
    return null;
  }

  const t = data.temperature;
  // 하늘색 면을 깔지 않는다 — 띠 셋이 각자 다른 색으로 칠하면 상단이 색 블록이 된다.
  // 정체성은 배경이 아니라 라벨 글자색으로만 남긴다.
  return (
    <div className="border-b border-slate-200">
      <div className="max-w-6xl mx-auto px-5 sm:px-6 py-2 flex flex-wrap items-center gap-x-3 gap-y-1 t-caption">
        <span className="flex items-center font-semibold text-sky-800">
          한강 수온
          <InfoTooltip term="hangang_temp" placement="bottom" />
        </span>
        <span className="t-label font-bold num text-sky-800">{t.toFixed(1)}°C</span>
        <span className="text-slate-500">
          ({data.location}
          {data.observed_label ? ` · ${data.observed_label} 기준` : ""})
        </span>
        <span className="text-slate-700">{comment(t)}</span>
        {data.stale && <span className="font-semibold text-amber-700">(최신 갱신 실패, 이전 값)</span>}
        <span className="text-slate-500 ml-auto hidden md:inline">참고용 실시간 관측값</span>
      </div>
    </div>
  );
}
