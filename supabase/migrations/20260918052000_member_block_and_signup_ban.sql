-- 회원 관리 (2026-09-18)
-- 관리자 대시보드의 '회원 관리' 탭이 쓰는 컬럼 셋. 표는 새로 만들지 않는다.
--
-- ① is_blocked / blocked_at / blocked_reason
--    차단 = 채팅·게시글·댓글을 쓸 수 없는 상태(auth.assert_can_write). 로그인·열람·백테스트는 그대로 두고
--    발언만 막는다. 되돌릴 수 있어야 하므로 지우지 않고 플래그로 둔다.
--
-- ② banned_email_hash
--    관리자 탈퇴로 지운 계정의 이메일을 서버 비밀과 섞은 해시(auth.email_fingerprint). 주소 자체는 남기지 않고
--    '이 주소로 다시 가입하려는가'만 판정한다(비밀번호 가입·구글 가입 양쪽). 본인이 스스로 하는 탈퇴는 이 값을
--    채우지 않는다 — 스스로 떠난 회원은 돌아올 수 있어야 한다.
--    인덱스는 가입할 때마다 한 번 조회하므로 필요하다. 빈 문자열이 대부분이라 부분 인덱스로 둔다.

alter table public."user" add column if not exists is_blocked boolean not null default false;
alter table public."user" add column if not exists blocked_at varchar not null default '';
alter table public."user" add column if not exists blocked_reason varchar not null default '';
alter table public."user" add column if not exists banned_email_hash varchar not null default '';

create index if not exists ix_user_banned_email_hash
  on public."user" (banned_email_hash)
  where banned_email_hash <> '';

-- ③ deleted_at / signup_method (관리자 대시보드 가입 지표, 2026-09-18 점검 후 추가)
--    deleted_at: 탈퇴 시각(ISO). 없으면 '탈퇴 수'가 누적값으로 오늘 행에 몰려 찍힌다(탈퇴한 날을 모른다).
--    signup_method: 'google' | 'email'. 가입 방법을 password_hash 유무로 추정하면 탈퇴(해시 삭제)·비밀번호
--    재설정(구글 계정에 해시 생김) 때 영구 재분류되므로 가입 시점에 한 번 적는다. 옛 행은 '' 로 남고 보고서가
--    살아 있는 행에 한해 password_hash 로 추정한다.

alter table public."user" add column if not exists deleted_at varchar not null default '';
alter table public."user" add column if not exists signup_method varchar not null default '';

-- ④ visit.view_key 유니크
--    같은 페이지뷰 비콘이 두 번 도착하면(재시도·StrictMode) 지금은 SELECT 로 먼저 확인하는데, 동시에 들어오면
--    둘 다 통과해 뷰가 두 배로 센다. 빈 값(옛 행·이벤트)은 많으므로 부분 인덱스. record_visit 은 중복 오류를
--    삼킨다(중복 = 무시).
--    인덱스를 만들기 전에 이미 있는 중복(같은 view_key 의 나중 행)을 지운다 — 사전 점검과 이 마이그레이션 사이에
--    경쟁으로 한 쌍만 들어와도 CREATE UNIQUE INDEX 전체가 실패하므로, 같은 트랜잭션 안에서 먼저 치운다.
--    먼저 온 행(id 가 작은 쪽)을 남긴다: 나중 행은 같은 비콘의 재전송이다.
delete from public.visit a using public.visit b
  where a.view_key = b.view_key and a.view_key <> '' and a.id > b.id;
create unique index if not exists ux_visit_view_key
  on public.visit (view_key)
  where view_key <> '';
