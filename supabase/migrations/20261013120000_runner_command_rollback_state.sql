-- 진입 실패 되돌리기 (2026-10-13)
-- runnercommand.rollback_state: 진입 명령을 만들 때 그 신호 직전의 서버 전략 상태(JSON).
-- 실행기가 주문을 내지 못하면(실패 · 만료) 서버 전략을 이 상태로 되돌려 '안 샀는데 보유 중' 을 없앤다.
-- 되돌린 뒤에는 'rolled_back' 표시로 바뀐다. 서버 전용 테이블 — RLS · 권한 회수는 20260922120000 에서 이미 했다.
alter table public.runnercommand
  add column if not exists rollback_state text not null default '';
