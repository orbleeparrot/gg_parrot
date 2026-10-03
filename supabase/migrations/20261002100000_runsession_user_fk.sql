-- 실행 세션은 반드시 실제 회원에 속한다 — runsession.user_id → user.id 외래키.
-- 회원은 소프트 삭제(탈퇴 처리)만 하므로 ON DELETE 는 기본(NO ACTION): 실행 기록이 남은 회원 행을 실수로 지우지 못하게 막는다.
-- 운영의 고아 행은 적용 전 0건 확인(2026-10-02). NOT VALID 로 붙인 뒤 VALIDATE 해 큰 잠금을 피한다.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'runsession_user_id_fkey') THEN
    ALTER TABLE public.runsession
      ADD CONSTRAINT runsession_user_id_fkey FOREIGN KEY (user_id) REFERENCES public."user"(id) NOT VALID;
  END IF;
END $$;
ALTER TABLE public.runsession VALIDATE CONSTRAINT runsession_user_id_fkey;
