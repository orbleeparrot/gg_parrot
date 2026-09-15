-- 포지션 뉴스 기사 보강(enrichment) 재시도의 지수 백오프와, 저장 전 비교용 해시.
-- 9/14~15 Supabase egress 급증의 원인: 보강이 끝나지 않는 기사 1,700여 건을 5초 스캔·30초 리스마다
-- item_json 통째로 다시 읽고(1.1GB/일), 저장할 때마다 기존 행을 통째로 다시 읽었다(0.6GB/일).
-- enrichment_attempts / enrichment_next_ms — 재시도 횟수와 다음 재시도 시각(30초부터 두 배씩, 최대 6시간,
--   12회 뒤에는 enrichment_pending 을 끈다). 대기 행이 없으면 스캔은 EXISTS 한 번으로 끝난다.
-- source_hash / content_hash — 원본 항목과 저장된 item_json 의 해시. 같은 항목이 다시 오면 본문을 읽지 않고 건너뛴다.
alter table public.newsarticle
  add column if not exists source_hash varchar not null default '',
  add column if not exists content_hash varchar not null default '',
  add column if not exists enrichment_attempts integer not null default 0,
  add column if not exists enrichment_next_ms bigint not null default 0;
create index if not exists ix_newsarticle_enrichment_due
  on public.newsarticle (enrichment_pending, enrichment_next_ms);
