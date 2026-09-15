-- 내 에이전트: 평가손익 급변 알림의 기준값과, 청산 직전 포지션(평단·수량·마지막 평가손익)을 세션에 남긴다.
-- pnl_alert_pct — 마지막으로 알림을 보낸 시점의 평가손익(%). 여기서 2%p 이상 움직이거나 한 heartbeat 사이
--                 1%p 이상 급변하면 에이전트 알림을 보낸다(runner.heartbeat). 포지션이 닫히면 0 으로.
-- final_* — 종료 보고가 오면 마지막 heartbeat 의 포지션을 복사해 둔다. 청산 후 종료는 현재 값을 0 으로 지우므로
--           결과 화면이 '청산 직전 평단·수량·평가손익'을 보여 주려면 따로 남겨야 한다.
alter table public.runsession
  add column if not exists pnl_alert_pct double precision not null default 0,
  add column if not exists final_entry_price double precision not null default 0,
  add column if not exists final_position_qty double precision not null default 0,
  add column if not exists final_unrealized_pct double precision not null default 0;
