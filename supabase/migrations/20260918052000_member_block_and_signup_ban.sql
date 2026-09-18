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
