import { Link } from "react-router-dom";
import { api } from "../api.js";
import InfoTooltip from "./InfoTooltip.jsx";
import { baseOf, fmtMoney, fmtMoneyCompact, fmtKrw, fmtPrice, fmtQty, quoteOf } from "../lib/format.js";
import { useUsdKrw } from "../lib/usdkrw.js";
import usePaperSession from "../hooks/usePaperSession.js";

const SIDE_KO = { buy: "매수", sell: "매도", short: "숏 진입", cover: "숏 청산" };
const SIDE_COLOR = {
  buy: "text-green-600",
  short: "text-green-600",
  sell: "text-red-600",
  cover: "text-red-600",
};

function PaperBadge() {
  return (
    <span className="badge badge-mine">
      모의 트레이딩 · 실거래 아님
      <InfoTooltip term="paper_trading" />
    </span>
  );
}

export function PaperPanelView({ macro, valErr, onRegister, controller }) {
  const {
    session,
    status,
    mode,
    setMode,
    busy,
    error,
    setError,
    startedMacro,
    startedMode,
    running,
    start,
    stop,
    restart,
  } = controller;
  const { rate: krwRate } = useUsdKrw();

  async function downloadMacro() {
    setError("");
    try {
      await api.downloadMacroFile(macro);
    } catch (e) {
      setError(String(e.message || e));
    }
  }

  const ret = status?.current_return ?? 0;
  const up = ret >= 0;
  // The running session is locked to the macro it was started with; the builder
  // above can change independently. Flag the drift so the user knows the live
  // figures don't reflect their latest edits until they restart.
  const macroChanged =
    running && startedMacro && JSON.stringify(startedMacro) !== JSON.stringify(macro);

  return (
    // 도구 패널도 상자를 쓰지 않는다 — 구획은 괘선 하나로 충분하다(§1-3).
    <section className="pt-5 border-t border-slate-200 space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h3 className="t-h4 text-slate-900">페이퍼 트레이딩 (실시간 모의매매)</h3>
        <div className="flex items-center gap-2">
          {macro.leverage > 1 && (
            <span className="badge badge-risk">
              고위험 레버리지 <span className="num">{macro.leverage}</span>배
              <InfoTooltip term="leverage" />
            </span>
          )}
          <PaperBadge />
        </div>
      </div>

      {macro.leverage > 1 && (
        <div className="notice-risk t-small text-slate-700">
          레버리지 <span className="num">{macro.leverage}</span>배: 가격이 약{" "}
          <b className="num text-red-600">{(100 / macro.leverage).toFixed(macro.leverage >= 100 ? 2 : 1)}%</b> 반대로
          움직이면 청산(전액 손실)돼요. 모의(가짜 돈)로 위험을 체험하는 용도예요.
          <InfoTooltip term="liquidation" />
        </div>
      )}
      <p className="t-small text-slate-700">
        실제 주문 없이 실시간 시세로 "샀다·팔았다 치고" 기록만 해요. 거래소 계정·API 키가 필요 없어요.
      </p>

      {/* controls */}
      <div className="flex items-center gap-3 flex-wrap">
        <select
          value={mode}
          aria-label="페이퍼 트레이딩 방식"
          onChange={(e) => setMode(e.target.value)}
          disabled={busy || (!!session && running)}
          className="field field-sm w-auto"
        >
          <option value="live">실시간(live)</option>
          <option value="replay">데모 리플레이(최근 시세 빠르게 재생)</option>
        </select>

        {!running ? (
          <button onClick={start} disabled={busy || !!valErr} className="btn btn-l btn-secondary">
            {busy ? "시작 중…" : "페이퍼 트레이딩 시작"}
          </button>
        ) : (
          <button onClick={stop} disabled={busy} className="btn btn-l btn-danger">
            중지
          </button>
        )}
        <span className="t-caption text-slate-500">
          <span className="num">{macro.symbol}</span> · {(running ? startedMode : mode) === "replay" ? "리플레이" : "실시간"}
          {status && status.last_price > 0 && (
            <> · 현재가 <span className="num">{fmtPrice(status.last_price)}</span> {quoteOf(macro.symbol)}</>
          )}
        </span>
      </div>

      {/* settings-snapshot notice: makes it explicit that a running session is
          locked to the settings at start time, not the live builder values. */}
      {running ? (
        macroChanged ? (
          <div className="notice-warn t-small text-slate-700 flex items-center justify-between gap-3 flex-wrap">
            <span>
              빌더 설정을 바꿨지만, 지금 세션은 <b className="text-slate-900">시작 시점 설정</b>으로 계속 돌고 있어요. 아래 수익률에는 바뀐 설정이 반영되지 않아요.
            </span>
            <button onClick={restart} disabled={busy || !!valErr} className="btn btn-s btn-secondary shrink-0">
              바뀐 설정으로 재시작
            </button>
          </div>
        ) : (
          <div className="notice t-small text-slate-700">
            이 세션은 <b className="text-slate-900">시작 시점의 빌더 설정</b>으로 고정돼 돌고 있어요. 빌더를 바꾸면 재시작해야 적용돼요.
          </div>
        )
      ) : (
        <div className="t-caption text-slate-500">
          시작을 누르는 순간의 빌더 설정으로 실행돼요. (실행 중 변경은 재시작 전까지 반영되지 않아요)
        </div>
      )}

      <p className="t-caption text-slate-500">
        금액 단위는 <b className="text-slate-700">{quoteOf(macro.symbol)}</b>(미국 달러 기준) · 원화(≈)는 참고용 근사치 · 수량 단위는 코인 개수({baseOf(macro.symbol)})예요.
      </p>

      {valErr && <div className="t-small text-amber-700" role="alert">{valErr}</div>}
      {error && <div className="t-small text-red-600" role="alert">오류: {error}</div>}

      {/* live figures */}
      {status && (
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
          <div className="col-span-2 sm:col-span-1 min-w-0">
            <div className="stat-label">현재 평가금액 ({quoteOf(macro.symbol)})</div>
            <div className="t-h2 truncate num text-slate-900" title={fmtMoney(status.current_equity, macro.symbol)}>
              {fmtMoneyCompact(status.current_equity, macro.symbol)}
            </div>
            {fmtKrw(status.current_equity, krwRate) && (
              <div className="t-caption text-slate-500 truncate num">{fmtKrw(status.current_equity, krwRate)}</div>
            )}
          </div>
          <div className="min-w-0">
            <div className="stat-label">현재 수익률</div>
            <div className={"t-h2 num " + (up ? "text-green-600" : "text-red-600")}>
              {up ? "+" : ""}
              {ret.toFixed(2)}%
            </div>
          </div>
          <div className="min-w-0">
            <div className="stat-label">상태</div>
            <div className="t-h2 text-slate-900">
              {running ? "실행 중" : "중지됨"}
            </div>
          </div>
        </div>
      )}

      {/* register to leaderboard — surfaces right where the paper return shows,
          so a good run can go straight to the board without leaving the builder. */}
      {onRegister && (
        <div className={(status && ret > 0 ? "notice-good" : "notice") + " flex items-center justify-between gap-3 flex-wrap"}>
          <div className="t-small text-slate-700">
            {status && ret > 0 ? (
              <span>
                지금 <b className="num text-green-600">+{ret.toFixed(2)}%</b> — 이 매크로를 오늘의 리더보드에 올려봐요.
              </span>
            ) : (
              <span>이 매크로를 <b className="text-slate-900">오늘의 리더보드</b>에 등록해 다른 사람과 겨뤄봐요.</span>
            )}
          </div>
          <button onClick={() => onRegister(mode)} disabled={!!valErr} className="btn btn-m btn-secondary shrink-0">
            리더보드에 등록
          </button>
        </div>
      )}

      {/* liquidation alert (leverage) — 끼어드는 사건이라 상자 유지(§1-3 예외) */}
      {status && (status.liquidations || 0) > 0 && (
        <div className="alert alert-risk">
          <div className="t-title"><span className="num">{status.liquidations}</span>번 청산됐어요 (전액 손실)</div>
          <div className="t-small mt-2">
            청산으로 잃은 금액 <b className="num">{fmtMoney(status.liquidated_loss || 0, macro.symbol)}</b>
            {fmtKrw(status.liquidated_loss || 0, krwRate) && <span className="num"> ({fmtKrw(status.liquidated_loss || 0, krwRate)})</span>}
            {" "}· 레버리지 <span className="num">{macro.leverage}</span>배의 위험을 모의로 확인했어요.
          </div>
        </div>
      )}

      {/* live trade log */}
      {status && (
        <div>
          <div className="t-title text-slate-900 mb-2">실시간 매매 로그 (최신이 위)</div>
          {/* Five fixed columns don't fit a phone, so the log scrolls sideways
              rather than stretching the page. 스크롤 컨테이너일 뿐 카드가 아니라
              테두리는 두지 않고, 행 구분은 괘선만 쓴다(§6 table-row). */}
          <div className="max-h-72 overflow-auto border-t border-slate-200">
            <div className="min-w-[460px] divide-y divide-slate-200">
              <div className="flex items-center px-1 py-2 t-caption text-slate-700 bg-slate-50 sticky top-0">
                <span className="w-20">시각</span>
                <span className="w-16">구분</span>
                <span className="flex-1 text-right">체결가 ({quoteOf(macro.symbol)})</span>
                <span className="w-28 sm:w-44 text-right">수량 ({baseOf(macro.symbol)})</span>
                <span className="w-20 text-right">누적수익</span>
              </div>
              {(status.trades || []).length === 0 && (
                <div className="px-1 py-8 text-center t-small text-slate-500">
                  아직 체결이 없어요. 조건을 낮추거나(익절·손절 0.3~1%) 변동성 큰 종목·리플레이를 써봐요.
                </div>
              )}
              {(status.trades || []).map((t, i) => (
                <div
                  key={t.id}
                  className={"flex items-center px-1 py-3 t-label " + (i === 0 ? "bg-slate-100" : "")}
                >
                  <span className="text-slate-500 w-20 num">{t.ts.slice(11, 19)}</span>
                  <span className={"font-semibold w-16 " + (SIDE_COLOR[t.side] || "")}>
                    {SIDE_KO[t.side] || t.side}
                  </span>
                  <span className="text-slate-900 font-semibold flex-1 text-right num">
                    {fmtPrice(t.price)}
                  </span>
                  <span className="text-slate-500 w-28 sm:w-44 text-right num">
                    {fmtQty(t.qty)}
                  </span>
                  <span
                    className={
                      "w-20 text-right font-semibold num " +
                      (t.return_at_trade >= 0 ? "text-green-600" : "text-red-600")
                    }
                  >
                    {t.return_at_trade >= 0 ? "+" : ""}
                    {Number(t.return_at_trade).toFixed(2)}%
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* real-trade: 매크로 파일(.ggm.json)만 내려받아 '껄무새 매크로 실행기'에 넣는다.
          실행기가 실제 주문을 실행하므로(기본 테스트넷) 아래 문구는 그 위험을 축소하지 않는다. */}
      <div className="alert alert-warn space-y-3">
        <div className="t-title">동작 검증 완료 → 매크로 실행기로 실거래</div>
        <p className="t-small">
          터미널·파이썬 설치 없이 <b>껄무새 매크로 실행기</b>(프로그램)에 이 매크로 파일을 넣고 돌려요.
          실행 현황과 원격 종료는 <b>마이페이지</b>에서 확인해요.
        </p>
        <div className="pt-3 border-t border-amber-700/30 space-y-2">
          <p className="t-small font-bold">진행 방법</p>
          <ol className="t-small list-decimal pl-4 space-y-1">
            <li>아래 버튼으로 <b>매크로 파일(.ggm.json)</b>을 내려받아요.</li>
            <li>마이페이지에서 <b>껄무새 회원 키</b>를 복사해요(계정당 1개).</li>
            <li>매크로 실행기를 열어 ①파일 ②실거래 여부 ③API 키 ④회원 키를 넣고 시작해요.</li>
          </ol>
          <p className="t-small font-bold pt-1">
            주의: 실행기는 <u>실제로 주문을 실행해요</u> (기본값: 바이낸스 테스트넷 = 가짜 자금)
          </p>
          <ul className="t-small list-disc pl-4 space-y-1">
            <li>
              {macro.position_side === "short" || macro.leverage > 1
                ? "숏·레버리지 매크로라 USDT-M 선물로 실행돼요."
                : "롱·1배 매크로라 현물(spot)로 실행돼요."}
            </li>
            <li>익절·손절·일일 최대손실·최대 보유시간·재진입 금지가 함께 적용돼요.</li>
            <li>실제 자금은 실행기에서 <b>실거래(메인넷) 체크</b>를 켜야 움직여요(경고 확인 단계 있음).</li>
            <li>API 키는 실행기 로컬에서만 쓰고 서버로 전송·저장하지 않아요. 출금 기능은 없어요.</li>
          </ul>
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          <button onClick={downloadMacro} disabled={!!valErr} className="btn btn-m btn-secondary">
            매크로 파일 내려받기 (.ggm.json)
          </button>
          <Link to="/?run=1&step=1" className="t-small font-semibold text-slate-900 underline underline-offset-4 decoration-slate-300 hover:decoration-slate-900">
            빠른 실행 열기·사용법 →
          </Link>
        </div>
      </div>
    </section>
  );
}

export default function PaperPanel(props) {
  const controller = usePaperSession({
    macro: props.macro,
    valErr: props.valErr,
    onStarted: props.onStarted,
  });
  return <PaperPanelView {...props} controller={controller} />;
}
