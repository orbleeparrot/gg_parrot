import EquityChart from "./EquityChart.jsx";
import SimBadge from "./SimBadge.jsx";
import InfoTooltip from "./InfoTooltip.jsx";
import { fmtMoney, fmtMoneyCompact, fmtKrw } from "../lib/format.js";
import { useUsdKrw } from "../lib/usdkrw.js";

const AI_EXPLAIN_MASCOT = "/brand/navigation/ggparrot-nav-agent.png";

// mascot mood -> accent color for the AI analysis card (neutral fallback).
const MOOD_ACCENT = {
  idle: "text-slate-700",
  liquidated: "text-red-700",
  crash: "text-red-700",
  loss: "text-amber-800",
  lost_to_hold: "text-amber-800",
  win: "text-green-700",
  big_win: "text-green-700",
};

// 껄무새 AI 원인 분석 카드. 규칙기반 장문 멘트는 쓰지 않고, '분석하기'를 누르면
// 서버(Anthropic)가 결과 원인을 5줄 이내로 간결하게 분석한다.
function ParrotExplain({ explanation, onAiExplain, aiBusy, aiError }) {
  const isAi = explanation && explanation.source === "ai";

  // AI 분석 결과가 있을 때: 간결한 원인 분석만 렌더.
  // 카드가 아니라 왼쪽 규칙(§6 notice) — 해설은 본문이지 끼어드는 알림이 아니다.
  if (isAi) {
    const accent = MOOD_ACCENT[explanation.mood] || MOOD_ACCENT.idle;
    return (
      <div className="notice py-2">
        <div className="flex items-start gap-3">
          <img
            src={AI_EXPLAIN_MASCOT}
            alt=""
            width="256"
            height="256"
            className="h-10 w-10 shrink-0 object-contain"
            aria-hidden="true"
            draggable="false"
          />
          <div className="min-w-0 flex-1">
            <div className="t-caption text-slate-500 mb-1">껄무새 AI 해설</div>
            <div className={"t-title " + accent}>{explanation.headline}</div>
            {explanation.points?.length > 0 && (
              <ul className="mt-3 space-y-2 t-small text-slate-700 list-disc pl-5">
                {explanation.points.map((p, i) => (
                  <li key={i}>{p}</li>
                ))}
              </ul>
            )}
            {explanation.lesson && (
              <div className="mt-3 t-small text-slate-700">
                <span className="font-bold text-slate-900">이 매크로를 쓴다면 · </span>
                {explanation.lesson}
              </div>
            )}
            <div className="mt-3 flex items-center justify-between gap-2 flex-wrap">
              <div className="t-caption text-slate-500">{explanation.disclaimer}</div>
              <button
                type="button"
                onClick={onAiExplain}
                disabled={aiBusy}
                className="t-caption text-slate-900 underline underline-offset-4 decoration-slate-300 hover:decoration-slate-900 disabled:opacity-40"
              >
                {aiBusy ? "분석 중…" : "다시 분석"}
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // 아직 AI 분석 전: 분석 버튼만. 서버 Anthropic 키로 동작(입력 불필요).
  return (
    <div className="pt-4 border-t border-slate-200">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex min-w-0 items-center gap-3">
          <img
            src={AI_EXPLAIN_MASCOT}
            alt=""
            width="256"
            height="256"
            className="h-12 w-12 shrink-0 object-contain"
            aria-hidden="true"
            draggable="false"
          />
          <div className="min-w-0">
            <div className="t-title text-slate-900">껄무새 AI 해설</div>
            <div className="mt-1 t-small text-slate-500">이 결과가 왜 이렇게 나왔는지 쉽게 정리해줘요</div>
          </div>
        </div>
        <button
          type="button"
          onClick={onAiExplain}
          disabled={aiBusy}
          className="btn btn-m btn-secondary shrink-0"
        >
          {aiBusy ? "분석 중…" : "분석하기"}
        </button>
      </div>
      {aiError && <div className="mt-2 t-caption text-red-600">{aiError}</div>}
    </div>
  );
}

// table-row: 상자 대신 라벨/값 2열 + 괘선. 수치는 num(고정폭)이라 세로로 맞는다.
function Stat({ label, value, term, color = "text-slate-900", title, sub }) {
  return (
    <div className="table-row min-w-0">
      <div className="row-label flex items-center shrink-0">
        {label}
        {term && <InfoTooltip term={term} />}
      </div>
      <div className="min-w-0 text-right">
        <div className={"row-value truncate " + color} title={title}>{value}</div>
        {sub && <div className="t-caption text-slate-500 truncate num" title={sub}>{sub}</div>}
      </div>
    </div>
  );
}

function PerSymbolTable({ rows }) {
  if (!rows || rows.length === 0) return null;
  const coin = (s) => (s || "").replace(/USDT$|BUSD$|USDC$/, "");
  return (
    <div className="pt-2">
      <div className="t-title text-slate-900 mb-3">
        종목별 성과 (포트폴리오 · 자금 균등 분할)
      </div>
      {/* 표는 캔버스 위 괘선만 — 감싸는 상자를 두지 않는다(§6 table-row). */}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[420px]">
          <thead>
            <tr className="border-b border-slate-200 t-caption text-slate-700">
              <th className="text-left py-2">종목</th>
              <th className="text-right">수익률</th>
              <th className="text-right">MDD</th>
              <th className="text-right">승률</th>
              <th className="text-right">매매</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const up = (r.final_return_pct ?? 0) >= 0;
              return (
                <tr key={r.symbol} className="border-b border-slate-200 last:border-0 t-label">
                  <td className="py-3 font-semibold text-slate-900">{coin(r.symbol)}</td>
                  <td className={"text-right font-bold num " + (up ? "text-green-600" : "text-red-600")}>
                    {up ? "+" : ""}{(r.final_return_pct ?? 0).toFixed(2)}%
                  </td>
                  <td className="text-right num text-red-600">-{(r.mdd_pct ?? 0).toFixed(1)}%</td>
                  <td className="text-right num text-slate-700">{(r.win_rate_pct ?? 0).toFixed(0)}%</td>
                  <td className="text-right num text-slate-700">{r.total_trades ?? 0}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="mt-2 t-caption text-slate-500">위 큰 수치는 종목별을 합산한 포트폴리오 전체 결과예요.</div>
    </div>
  );
}

export default function ResultView({ result, perSymbol, explanation, onAiExplain, aiBusy, aiError, summary, dataSource, periodLabel, symbol, leverage = 1 }) {
  const { rate: krwRate } = useUsdKrw();
  if (!result) return null;
  const r = result;
  const up = r.final_return_pct >= 0;
  const retColor = up ? "text-green-600" : "text-red-600";
  const sign = up ? "+" : "";
  const levered = leverage > 1;
  const liq = r.liquidation_count || 0;
  // Buy&Hold baseline comparison (null when the engine couldn't define it).
  const bh = r.buy_hold_return_pct != null ? r.buy_hold_return_pct : null;
  const vsHold = bh !== null ? r.final_return_pct - bh : 0;
  const beatHold = vsHold >= 0;

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="t-small text-slate-700">{summary}</div>
        <div className="flex items-center gap-2">
          {levered && (
            <span className="badge badge-risk">
              고위험 레버리지 <span className="num">{leverage}</span>배
              <InfoTooltip term="leverage" />
            </span>
          )}
          <SimBadge />
        </div>
      </div>

      {/* 청산은 진짜로 끼어드는 사건이라 상자를 유지한다(§1-3 예외). */}
      {liq > 0 && (
        <div className="alert alert-risk">
          <div className="t-title">
            기간 중 <span className="num">{liq}</span>번 청산됐어요 (전액 손실)
          </div>
          <div className="mt-2 t-small">
            레버리지 <span className="num">{leverage}</span>배라 청산으로 잃은 금액{" "}
            <b className="num">{fmtMoney(r.liquidated_loss || 0, symbol)}</b>
            {fmtKrw(r.liquidated_loss || 0, krwRate) && (
              <span className="font-normal num"> ({fmtKrw(r.liquidated_loss || 0, krwRate)})</span>
            )}
            . 레버리지는 가격이 조금만 반대로 움직여도 투입 증거금을 전부 잃게 만들어요.
            <InfoTooltip term="liquidation" />
          </div>
        </div>
      )}

      {/* stat — 상자 없는 수치: 캡션 위, 값 아래, 여백으로만 구분(§6). */}
      <div className="pt-1">
        <div className="flex items-center t-caption text-slate-700 mb-1">
          백테스트 수익률 {periodLabel ? `· ${periodLabel}` : ""}
          <InfoTooltip term="backtest" />
        </div>
        <div className={"t-metric num " + retColor}>
          {sign}
          {r.final_return_pct.toFixed(2)}%
        </div>
        {bh !== null && (
          <div className="mt-3 flex items-center flex-wrap gap-x-3 gap-y-1 t-small">
            <span className="text-slate-500">
              그냥 홀딩(HODL)했다면{" "}
              <b className={"num " + (bh >= 0 ? "text-green-600" : "text-red-600")}>
                {bh >= 0 ? "+" : ""}{bh.toFixed(2)}%
              </b>
            </span>
            {/* 등락은 채움이 아니라 글자 색으로만 말한다(§2-1). */}
            <span className={"font-bold " + (beatHold ? "text-green-600" : "text-red-600")}>
              {beatHold ? "▲" : "▼"} 홀딩보다 <span className="num">{vsHold >= 0 ? "+" : ""}{vsHold.toFixed(2)}%p</span>{" "}
              {beatHold ? "초과" : "미달"}
            </span>
          </div>
        )}
      </div>

      <div className="border-t border-slate-200">
        <Stat label="승률" term="win_rate" value={`${r.win_rate_pct.toFixed(1)}%`} />
        <Stat label="MDD (최대낙폭)" term="mdd" value={`-${r.mdd_pct.toFixed(1)}%`} color="text-red-600" />
        <Stat label="총 매매 횟수" value={r.total_trades} />
        <Stat label="최종 평가금액" value={fmtMoneyCompact(r.final_equity, symbol)} title={fmtMoney(r.final_equity, symbol)} sub={fmtKrw(r.final_equity, krwRate)} />
        <Stat
          label="샤프지수"
          term="sharpe"
          value={r.sharpe != null ? r.sharpe.toFixed(2) : "—"}
          color={r.sharpe != null && r.sharpe >= 1 ? "text-green-600" : "text-slate-900"}
        />
        <Stat
          label="손익비 (PF)"
          term="profit_factor"
          value={r.profit_factor != null ? r.profit_factor.toFixed(2) : "—"}
          color={r.profit_factor != null && r.profit_factor >= 1 ? "text-green-600" : "text-slate-900"}
        />
        <Stat
          label="최대 연속손절"
          value={`${r.max_consecutive_losses || 0}회`}
          color={(r.max_consecutive_losses || 0) >= 5 ? "text-red-600" : "text-slate-900"}
        />
      </div>

      <PerSymbolTable rows={perSymbol} />

      <ParrotExplain
        explanation={explanation}
        onAiExplain={onAiExplain}
        aiBusy={aiBusy}
        aiError={aiError}
      />

      <div className="pt-2">
        <div className="t-title text-slate-900 mb-3">자산곡선 (equity curve)</div>
        <EquityChart curve={r.equity_curve} />
      </div>

      {r.same_bar_sl_bars > 0 && (
        <div className="t-caption text-amber-700">
          한 봉에서 익절·손절이 같이 닿은 봉 <span className="num">{r.same_bar_sl_bars}</span>개 — 보수적으로 <b>손절 우선</b>으로 처리했어요.
        </div>
      )}

      {dataSource && (
        <div className="t-caption text-slate-500">
          데이터 소스: {dataSource === "binance-futures" ? "바이낸스 선물(USDT-M)" : dataSource}
          {dataSource === "binance-futures" && " · 실제 선물 캔들"}
          {dataSource === "synthetic" && " (오프라인 폴백 · 합성 데이터)"}
        </div>
      )}
    </div>
  );
}
