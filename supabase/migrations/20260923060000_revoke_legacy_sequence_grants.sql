-- 옛 표의 시퀀스 권한 회수 (2026-09-23)
--
-- public 스키마의 기본 권한(default ACL)이 anon·authenticated 에 전부를 주기 때문에, 앱의 create_all 로 생긴
-- 옛 표의 시퀀스에는 USAGE·SELECT·UPDATE 가 남아 있었다. 표 자체는 RLS 로 막혀 있어 데이터는 새지 않지만,
-- 시퀀스는 RLS 의 보호를 받지 않아 `last_value` 를 읽을 수 있었다(= 누적 가입자 수 같은 규모가 드러난다).
-- UPDATE 는 `setval` 로 번호를 되돌릴 수 있어 기본키 충돌을 낼 수 있는, 유일하게 실질적인 위험이다.
-- 2026-09-18 이후 새로 쓴 마이그레이션은 이미 시퀀스를 회수하고 있었다 — 옛 표만 예외로 남아 있던 것을 맞춘다.
--
-- **우리 표에만 적용한다.** 이 Supabase 프로젝트는 다른 팀 앱과 공유하므로(hecto_promo_influencers ·
-- raw_events · agg_* · funnel_definition · holder_alerts), 앱의 SQLModel 메타데이터에 있는 표 목록으로 범위를 가둔다.
-- 시퀀스 이름을 직접 적지 않고 pg_depend 로 '그 표가 소유한 시퀀스'를 찾는다 — 이름 규칙(<표>_<열>_seq)에
-- 기대지 않고, 나중에 열이 늘어 시퀀스가 생겨도 같은 규칙이 적용된다.

do $$
declare
  ours text[] := array[
    'apiusagedaily','askextracredit','askmacrosession','boardcomment','boardimage','boardpost','boardpostvote',
    'boardreport','browsernewspagecache','chatmessage','chatreadstate','chatroom','chatroommember','collectorrun',
    'collectorsourcedaily','communitypostsummary','dailychallenge','dailyquestclaim','leaderboardcarryover',
    'leaderboardchallengebot','leaderboardentry','leaderboardentrystats','leaderboardsnapshotcontrol',
    'leaderboardsnapshotitem','leaderboardsnapshotversion','leaderboardvote','macroeventdaily','macrorow',
    'macrounlock','marketnewssummary','message','newsarticle','newsarticlefeed','newsmaintenancelease',
    'newstitletranslation','onchainholderstate','papersession','papertrade','pointledger','publicnewslease',
    'receipt','runnercommand','runnerkey','runnerlaunchticket','runsession','runsessionevent','tickernewsaibudget',
    'tickernewssnapshot','tickernewsstate','user','useravatar','usermacro','visit','whaleholderbalance',
    'whaleobservation','whaletradestate'
  ];
  seq text;
begin
  for seq in
    select s.relname
    from pg_class s
    join pg_depend d on d.objid = s.oid and d.deptype = 'a' and d.classid = 'pg_class'::regclass
    join pg_class t on t.oid = d.refobjid
    where s.relkind = 'S'
      and s.relnamespace = 'public'::regnamespace
      and t.relname = any(ours)
  loop
    execute format('revoke all privileges on sequence public.%I from public, anon, authenticated', seq);
  end loop;
end $$;
