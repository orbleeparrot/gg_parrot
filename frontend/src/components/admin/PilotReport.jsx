// 관리자 대시보드 '파일럿 성과' 탭 — 파일럿 테스터 실전 매크로 손익(15초마다 갱신).
// 카드 · 배지 없이 글자 굵기와 정렬로만 위계를 둔다: 맨 위 한 줄이 답(합계), 그다음 계정별 합계 표, 계정별 세션 표, 계산 기준.
// 색은 손익 부호에만 쓴다.
import CoinIcon from "../CoinIcon.jsx";
import {
  PILOT_EXCHANGES, PILOT_RULES, pilotAmount, pilotDuration, pilotLeftOut, pilotPct, pilotPeriod, pilotQty, pilotSpan, pilotTone,
  pilotWon, pilotWorstShare,
} from "../../lib/pilotReport.js";
import "./PilotReport.css";

export default function PilotReport({ data }) {
  const totals = data?.totals || {};
  const accounts = data?.accounts || [];
  const fee = data?.fee_rates || {};
  const worst = pilotWorstShare(accounts);
  const active = accounts.filter((a) => a.traded_sessions > 0);
  const detailed = accounts.filter((a) => a.traded_sessions > 0 || a.issues?.length);
  const ended = Boolean(data?.period?.ended);

  return (
    <div className="pilot">
      <header className="pilot-top">
        <p className="pilot-kicker">파일럿 테스트 · 실전 매크로 성과{ended ? " · 기간 종료" : ""}</p>
        <h2>테스터 {totals.testers ?? accounts.length}명 합계 <span className={pilotTone(totals.net)}>{pilotWon(totals.net)}</span></h2>
        <p className="pilot-lede">
          이익 {totals.winners ?? 0}명, 손실 {totals.losers ?? 0}명, 체결 없음 {totals.no_trades ?? 0}명.
          {worst ? <> 손실의 <span className="pilot-nw">{worst.share}%({pilotWon(worst.net)})</span>가 {worst.name} 계정에서 났어요.</> : null}
        </p>
        <p className="pilot-period">
          {pilotPeriod(data?.period?.start, data?.period?.end)} · 실제 거래소 주문만 · 수수료를 뺀 금액
          {totals.running_sessions ? ` · 실행 중 ${totals.running_sessions}개` : ""}
        </p>
      </header>

      <section className="pilot-sec">
        <h3 className="pilot-h">계정별 합계 <small>단위 원</small></h3>
        <div className="pilot-scroll">
          <table className="pilot-rank">
            <thead>
              <tr>
                <th className="pilot-rk" scope="col">순위</th>
                <th scope="col">계정</th>
                <th className="pilot-net" scope="col">합계</th>
                <th className="pilot-sec-col" scope="col">실현손익</th>
                <th className="pilot-sec-col" scope="col">보유 평가</th>
                <th className="pilot-sec-col" scope="col">체결 세션</th>
                <th className="pilot-sec-col" scope="col">수수료</th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((a) => {
                const traded = a.traded_sessions > 0;
                const rank = traded ? active.indexOf(a) + 1 : null;
                const anchor = traded || a.issues?.length;
                return (
                  <tr key={a.user_id} className={traded ? undefined : "is-idle"}>
                    <td className="pilot-rk">{rank ?? ""}</td>
                    <th scope="row">
                      {anchor ? <a href={`#pilot-u${a.user_id}`}>{a.name}</a> : a.name}
                      <span className="pilot-sub">{a.email}</span>
                      {traded ? <span className="pilot-m-only">실현 {pilotAmount(a.realized)} · 보유 {pilotAmount(a.unrealized)}</span> : null}
                    </th>
                    {traded ? (
                      <>
                        <td className={`pilot-net ${pilotTone(a.net)}`}>{pilotAmount(a.net)}</td>
                        <td className={`pilot-sec-col ${pilotTone(a.realized)}`}>{pilotAmount(a.realized)}</td>
                        <td className={`pilot-sec-col ${pilotTone(a.unrealized)}`}>{pilotAmount(a.unrealized)}</td>
                      </>
                    ) : (
                      <>
                        <td className="pilot-net is-zero"><span className="pilot-why">{a.live_sessions ? `실전 ${a.live_sessions}세션, 체결 0건` : "실행한 매크로 없음"}</span></td>
                        <td className="pilot-sec-col" />
                        <td className="pilot-sec-col" />
                      </>
                    )}
                    <td className="pilot-sec-col pilot-n">{a.traded_sessions}<span className="pilot-of">/{a.live_sessions}</span></td>
                    <td className="pilot-sec-col pilot-n">{traded ? Math.round(a.fees).toLocaleString("ko-KR") : ""}</td>
                  </tr>
                );
              })}
            </tbody>
            <tfoot>
              <tr>
                <td className="pilot-rk" />
                <th scope="row">{totals.testers ?? accounts.length}명 합계</th>
                <td className={`pilot-net ${pilotTone(totals.net)}`}>{pilotAmount(totals.net)}</td>
                <td className={`pilot-sec-col ${pilotTone(totals.realized)}`}>{pilotAmount(totals.realized)}</td>
                <td className={`pilot-sec-col ${pilotTone(totals.unrealized)}`}>{pilotAmount(totals.unrealized)}</td>
                <td className="pilot-sec-col pilot-n">{totals.traded_sessions ?? 0}<span className="pilot-of">/{totals.live_sessions ?? 0}</span></td>
                <td className="pilot-sec-col pilot-n">{Math.round(totals.fees || 0).toLocaleString("ko-KR")}</td>
              </tr>
            </tfoot>
          </table>
        </div>
        <p className="pilot-note">합계 = 실현손익 + 아직 팔지 않은 코인의 평가손익. 체결 세션은 체결이 한 번이라도 있었던 실전 세션 / 전체 실전 세션.</p>
      </section>

      <section className="pilot-sec">
        <h3 className="pilot-h">계정별 세션 <small>단위 원 · 수익률은 세션 투입금 대비</small></h3>
        {detailed.map((a) => <AccountBlock key={a.user_id} account={a} />)}
      </section>

      <section className="pilot-sec pilot-basis">
        <h3 className="pilot-h">계산 기준</h3>
        <ol>
          <li>
            실현손익은 실행기가 남긴 체결 기록(수량 × 체결가)으로 다시 계산하고 수수료(업비트 {pct(fee.upbit)}, 빗썸 {pct(fee.bithumb)},
            바이낸스 {pct(fee.binance)}, 매수 · 매도 각각)를 뺐어요. 실행기 화면의 손익은 수수료 전이라 이보다 조금 커요.
          </li>
          <li>보유 평가는 남은 코인을 세션이 마지막으로 받은 가격으로 계산했어요. '보유 중 종료' 세션의 코인은 거래소 계좌에 그대로 있을 수 있어요.</li>
          <li>같은 돈을 여러 세션에 차례로 쓴 경우가 많아 계정 합계에는 수익률을 붙이지 않았어요.</li>
          <li>기간 안에 시작한 실전 세션의, 기간 안에 난 체결만 셌어요. 모의 세션은 실제 돈이 아니라 뺐어요.</li>
          <li>거래소 계좌와는 대조하지 않았고, 사용자가 삭제한 세션은 남지 않아요. 체결 기록이 없는 옛 실행기(v18 이전) 세션은 실행기 보고값(수수료 전)을 썼어요.</li>
        </ol>
      </section>
    </div>
  );
}

function pct(rate) {
  const n = Number(rate);
  return Number.isFinite(n) ? `${+(n * 100).toFixed(3)}%` : "—";
}

function AccountBlock({ account: a }) {
  const traded = a.traded_sessions > 0;
  const leftOut = pilotLeftOut(a);
  const fallback = (a.sessions || []).some((s) => s.fallback);
  return (
    <section className="pilot-acct" id={`pilot-u${a.user_id}`}>
      <h4>
        <span>{a.name}</span>
        <span className={traded ? pilotTone(a.net) : "is-zero"}>{traded ? pilotWon(a.net) : "체결 없음"}</span>
      </h4>
      <p className="pilot-meta">
        <span className="pilot-nw">{a.email}</span>
        {traded ? (
          <>
            {" · "}<span className="pilot-nw">실현 {pilotWon(a.realized)}</span>
            {" · "}<span className="pilot-nw">보유 평가 {pilotWon(a.unrealized)}</span>
            {" · "}<span className="pilot-nw">수수료 {pilotWon(a.fees, { sign: false })}</span>
          </>
        ) : <>{" · "}실전 세션 {a.live_sessions}개, 모두 체결 0건</>}
      </p>
      {a.sessions?.length ? (
        <div className="pilot-scroll">
          <table className="pilot-sess">
            <thead>
              <tr>
                <th scope="col">코인</th>
                <th scope="col">기간</th>
                <th className="pilot-sec-col" scope="col">매수 / 매도</th>
                <th className="pilot-sec-col" scope="col">실현</th>
                <th className="pilot-sec-col" scope="col">보유 평가</th>
                <th className="pilot-net" scope="col">합계 · 수익률</th>
              </tr>
            </thead>
            <tbody>{a.sessions.map((s) => <SessionRow key={s.id} s={s} />)}</tbody>
          </table>
        </div>
      ) : null}
      {fallback ? <p className="pilot-note">* 체결 기록 전(실행기 v18 이전) 세션이라 실행기 보고값(수수료 전)을 썼어요.</p> : null}
      {leftOut ? <p className="pilot-note">{leftOut}</p> : null}
      {a.issues?.length ? (
        <div className="pilot-check">
          <b>확인 필요</b>
          <ul>{a.issues.map((t) => <li key={t}>{t}</li>)}</ul>
        </div>
      ) : null}
    </section>
  );
}

function SessionRow({ s }) {
  const end = s.running ? null : s.ended_at;
  const state = s.running ? "실행 중" : s.open_qty > 0 ? "보유 중 종료" : "";
  return (
    <tr>
      <th scope="row">
        <span className="pilot-coin">
          <CoinIcon symbol={s.symbol} size={18} proxy alt="" />
          <b>{s.base}</b>
        </span>
        <span className="pilot-sub">{PILOT_EXCHANGES[s.exchange] || s.exchange} · {PILOT_RULES[s.rule] || s.rule}</span>
      </th>
      <td className="pilot-when">
        {pilotSpan(s.started_at, end, s.running)}
        <span className="pilot-sub">{pilotDuration(s.started_at, end || new Date().toISOString())}{state ? ` · ${state}` : ""}</span>
      </td>
      <td className="pilot-sec-col pilot-n">
        {s.fallback ? "—" : `${s.buys} / ${s.sells}`}
        {s.open_qty > 0 ? <span className="pilot-sub">{pilotQty(s.open_qty)}개 보유</span> : null}
      </td>
      <td className={`pilot-sec-col ${pilotTone(s.realized)}`}>{pilotAmount(s.realized)}{s.fallback ? <sup>*</sup> : null}</td>
      <td className={`pilot-sec-col ${pilotTone(s.unrealized)}`}>{pilotAmount(s.unrealized)}</td>
      <td className={`pilot-net ${pilotTone(s.net)}`}>
        {pilotAmount(s.net)}
        <span className="pilot-sub pilot-pct">{pilotPct(s.return_pct)}</span>
      </td>
    </tr>
  );
}
