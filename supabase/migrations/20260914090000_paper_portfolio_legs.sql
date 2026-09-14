-- 페이퍼 트레이딩이 멀티종목(포트폴리오) 매크로를 백테스트처럼 종목별로 돌린다.
-- 세션에는 종목별 현황(JSON 배열)을, 체결 행에는 어느 종목의 체결인지 남긴다.
alter table public.papersession
  add column if not exists legs_json text not null default '';
alter table public.papertrade
  add column if not exists symbol text not null default '';
