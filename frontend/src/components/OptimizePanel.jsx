import { useState } from "react";
import { api } from "../api.js";
import { buildMacro } from "../lib/macro.js";
import InfoTooltip from "./InfoTooltip.jsx";

// 수익률 격자의 발산(diverging) 스케일.
//
// 이전 버전은 red(0°) → amber(65°) → green(130°) 로 이어지는 무지개였고, 가운데가
// 노랑이었다. 발산 스케일에서 가운데는 "아무것도 아님"으로 읽혀야 하는데 노랑은
// 우리 브랜드 강조색이라 오히려 '여기가 좋다'로 읽혔다. 게다가 기준점을 [min,max]의
// 중앙에 두어서, 전 구간이 이익인 격자에서는 '가장 덜 번 칸'이 빨갛게 칠해졌다.
//
// 지금은 두 축을 바로잡았다:
//   · 기준점은 데이터 중앙이 아니라 **0%(본전)** — 발산 측정값의 진짜 피벗이다.
//   · 양극은 서로 반대로 읽히는 두 색(하락 빨강 ↔ 상승 초록), 가운데는 **무채색**.
//     채도를 크기에 비례시키므로 0 근처는 자동으로 회색이 된다.
//   · 좌우를 같은 배율로 재서(대칭 extent) 손실 쪽이 과장되지 않게 한다.
//
// 채도·명도는 CSS 변수라 테마가 바뀌면 스케일이 통째로 뒤집힌다. 셀 글자는
// `text-slate-800` — 이 변수도 테마에 따라 뒤집혀 늘 반대편 명도에 놓인다.
function heatStyle(value, extent) {
  if (!(extent > 0)) return { background: "rgb(var(--c-slate-100))" };
  const t = Math.max(-1, Math.min(1, value / extent)); // -1 = 최대손실, +1 = 최대이익
  const mag = Math.abs(t);
  const hue = t >= 0 ? 145 : 5; // 상승 초록 ↔ 하락 빨강 (따뜻함/차가움이 반대)
  return {
    background:
      `hsl(${hue} calc(var(--heat-s) * ${mag.toFixed(3)})` +
      ` calc(var(--heat-l) + var(--heat-l-shift) * ${mag.toFixed(3)}))`,
  };
}

// 연속 색 스케일은 범례 없이는 읽을 수 없다.
function HeatLegend({ extent }) {
  const stops = [-1, -0.5, 0, 0.5, 1];
  return (
    <div className="flex items-center gap-2 t-caption text-slate-500">
      <span className="num">-{extent.toFixed(1)}%</span>
      <span className="flex rounded overflow-hidden" aria-hidden>
        {stops.map((t) => (
          <span key={t} className="w-7 h-3" style={heatStyle(t * extent, extent)} />
        ))}
      </span>
      <span className="num">+{extent.toFixed(1)}%</span>
      <span>· 가운데(회색)가 본전</span>
    </div>
  );
}

const pct = (v) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
const tone = (v) => (v >= 0 ? "text-green-600" : "text-red-600");

// Verdict on the winning cell: did the value picked on the training window
// still work on the window it was never allowed to see?
function verdict(best, v) {
  if (!v?.split) {
    return {
      cls: "notice-warn",
      label: "검증 부족",
      text: "기간이 짧아 검증을 못 했어요. 과거 전체에 맞춘 값이라 그대로 믿기 어려워요.",
    };
  }
  const oos = best.oos_return_pct;
  if (oos == null) return null;
  if (oos <= 0) {
    return {
      cls: "notice-risk",
      label: "검증 손실",
      text: "학습 구간에선 좋았지만 검증 구간에선 손실이에요. 과거에 맞춘 값일 가능성이 높아요.",
    };
  }
  if (best.final_return_pct > 0 && oos < best.final_return_pct * 0.3) {
    return {
      cls: "notice-warn",
      label: "성능 저하",
      text: "검증 구간에서도 이익이지만 학습 구간보다 크게 나빠졌어요. 기대치를 낮춰 잡아요.",
    };
  }
  return {
    cls: "notice-good",
    label: "검증 통과",
    text: "고를 때 쓰지 않은 검증 구간에서도 이익이 났어요. 상대적으로 견고한 편이에요.",
  };
}

function BestSummary({ best, v }) {
  const info = verdict(best, v);
  return (
    <div className="space-y-3">
      <div className="t-small text-slate-700">
        학습 구간 최적: <b className="text-slate-900">익절 <span className="num">{best.tp}%</span> · 손절 <span className="num">{best.sl}%</span></b> →{" "}
        <b className={"num " + tone(best.final_return_pct)}>{pct(best.final_return_pct)}</b>{" "}
        <span className="text-slate-500 num">
          (MDD -{best.mdd_pct.toFixed(1)}% · 매매 {best.total_trades}회)
        </span>
      </div>

      {v?.split && best.oos_return_pct != null && (
        <div className="t-small text-slate-700">
          같은 값의 <b className="text-slate-900">검증 구간</b> 성적:{" "}
          <b className={"num " + tone(best.oos_return_pct)}>{pct(best.oos_return_pct)}</b>{" "}
          <span className="text-slate-500 num">
            (매매 {best.oos_trades}회
            {v.overfit_gap != null && ` · 학습 대비 ${v.overfit_gap >= 0 ? "-" : "+"}${Math.abs(v.overfit_gap).toFixed(2)}%p`})
          </span>
        </div>
      )}

      {info && (
        <div className={info.cls + " t-small text-slate-700"}>
          <b className="text-slate-900">{info.label} · </b>{info.text}
        </div>
      )}

      {v?.split && (
        <div className="flex flex-wrap gap-x-4 gap-y-1 t-caption text-slate-500">
          <span>학습 {v.train_label} (<span className="num">{v.train_bars}</span>봉)</span>
          <span>검증 {v.test_label} (<span className="num">{v.test_bars}</span>봉)</span>
          {v.generalization_rate != null && (
            <span>
              학습에서 이익이던 조합 중 <b className="num text-slate-700">{v.generalization_rate}%</b>가 검증에서도 이익
            </span>
          )}
        </div>
      )}
    </div>
  );
}

export default function OptimizePanel({ form, setForm, valErr, onResult }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [data, setData] = useState(null);

  // Only rule A carries take_profit_pct + stop_loss to sweep.
  if (form.rule_type !== "A") return null;

  async function run() {
    setError("");
    if (valErr) return setError(valErr);
    setBusy(true);
    try {
      const res = await api.optimize(buildMacro(form));
      setData(res);
      onResult?.(res);
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  function applyCell(tp, sl) {
    setForm((f) => ({ ...f, take_profit_pct: tp, stop_loss_pct: sl, use_stop_loss: true }));
  }

  // 0%를 기준으로 좌우 같은 배율 — 한쪽이 더 튀면 손익이 편향돼 보인다(§2-2와 같은 이유).
  const returns = data ? data.cells.map((c) => c.final_return_pct) : [];
  const extent = returns.length ? Math.max(...returns.map((v) => Math.abs(v))) : 0;
  const cellAt = (tp, sl) =>
    data?.cells.find((c) => c.tp === tp && c.sl === sl) || null;
  const isCurrent = (tp, sl) =>
    data && data.current && data.current.tp === tp && data.current.sl === sl;
  const isBest = (tp, sl) => data?.best && data.best.tp === tp && data.best.sl === sl;

  return (
    <section className="pt-5 border-t border-slate-200 space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center t-h4 text-slate-900">
          익절 / 손절 자동 최적화
          <InfoTooltip term="optimize" />
        </div>
        <button onClick={run} disabled={busy || !!valErr} className="btn btn-m btn-secondary">
          {busy ? "최적화 중…" : data ? "다시 최적화" : "최적화 돌려보기"}
        </button>
      </div>

      <p className="t-small text-slate-700">
        익절(가로)×손절(세로) 조합을 모두 돌려봐요. 기간을 <b className="text-slate-900">학습</b>과 <b className="text-slate-900">검증</b>으로 나눠서, 학습 구간에서
        고른 값이 <b className="text-slate-900">고를 때 쓰지 않은 검증 구간</b>에서도 통했는지까지 확인해요. 칸을 누르면 빌더에 적용돼요.
      </p>

      {error && <div className="t-small text-red-600">오류: {error}</div>}

      {data && (
        <>
          <div className="overflow-x-auto">
            <table className="border-collapse t-caption">
              <thead>
                <tr>
                  <th className="p-2 font-semibold text-slate-700 sticky left-0 bg-slate-50">손절＼익절</th>
                  {data.tp_values.map((tp) => (
                    <th key={tp} className="p-2 font-semibold text-slate-700 text-center num">
                      {tp}%
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.sl_values.map((sl) => (
                  <tr key={sl}>
                    <td className="p-2 font-semibold text-slate-700 sticky left-0 bg-slate-50 num">{sl}%</td>
                    {data.tp_values.map((tp) => {
                      const c = cellAt(tp, sl);
                      if (!c) return <td key={tp} />;
                      const best = isBest(tp, sl);
                      const cur = isCurrent(tp, sl);
                      return (
                        <td key={tp} className="p-1">
                          <button
                            onClick={() => applyCell(tp, sl)}
                            title={
                              `익절 ${tp}% · 손절 ${sl}%\n` +
                              `학습 ${c.final_return_pct.toFixed(2)}% · MDD -${c.mdd_pct.toFixed(1)}% · 샤프 ${c.sharpe ?? "—"} · 매매 ${c.total_trades}회\n` +
                              (c.oos_return_pct != null
                                ? `검증 ${c.oos_return_pct.toFixed(2)}% (매매 ${c.oos_trades}회)\n`
                                : "검증 구간 없음 (기간이 짧아요)\n") +
                              "클릭하면 빌더에 적용"
                            }
                            style={heatStyle(c.final_return_pct, extent)}
                            className={
                              "w-full min-w-[64px] rounded-md px-2 py-2 text-center font-bold num text-slate-800 transition " +
                              "hover:ring-2 hover:ring-slate-400 " +
                              (best ? "outline outline-2 outline-green-600 " : "") +
                              (cur ? "ring-2 ring-brand-line " : "")
                            }
                          >
                            {c.final_return_pct >= 0 ? "+" : ""}
                            {c.final_return_pct.toFixed(1)}%
                            {/* Held-out result under the fitted one: a cell that
                                only worked because it was fitted shows it here. */}
                            {c.oos_return_pct != null && (
                              <span className="block text-[11px] font-semibold opacity-80">
                                검증 {c.oos_return_pct >= 0 ? "+" : ""}
                                {c.oos_return_pct.toFixed(1)}%
                              </span>
                            )}
                            {best && <span className="block text-[11px] font-bold">★ 최적</span>}
                            {cur && !best && <span className="block text-[11px] font-semibold">현재</span>}
                          </button>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <HeatLegend extent={extent} />

          {data.best && <BestSummary best={data.best} v={data.validation} />}

          <div className="notice-warn t-small text-slate-700">
            <b className="text-slate-900">과최적화 주의:</b> 위 숫자는 <b className="text-slate-900">과거에 맞춰 고른</b> 값이라 미래 수익을 보장하지 않아요.
            한 칸만 튀는 조합보다 <b className="text-slate-900">주변까지 고르게 좋은 구간</b>이 더 믿을 만해요.
          </div>
        </>
      )}
    </section>
  );
}
