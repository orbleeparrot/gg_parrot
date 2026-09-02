-- gg_parrot is a server-side FastAPI application. Its browser client does not
-- query these tables through the Supabase Data API, so no anon/authenticated
-- policies are required. The backend and Prefect worker use the postgres role,
-- which bypasses RLS.

alter table public.boardcomment enable row level security;
alter table public.boardpost enable row level security;
alter table public.chatmessage enable row level security;
alter table public.dailychallenge enable row level security;
alter table public.leaderboardentry enable row level security;
alter table public.leaderboardvote enable row level security;
alter table public.macrorow enable row level security;
alter table public.macrounlock enable row level security;
alter table public.papersession enable row level security;
alter table public.papertrade enable row level security;
alter table public.pointledger enable row level security;
alter table public.runnerkey enable row level security;
alter table public.runnerlaunchticket enable row level security;
alter table public.runsession enable row level security;
alter table public.tickernewsaibudget enable row level security;
alter table public.tickernewssnapshot enable row level security;
alter table public.tickernewsstate enable row level security;
alter table public."user" enable row level security;
alter table public.usermacro enable row level security;
alter table public.whaleholderbalance enable row level security;
alter table public.whaleobservation enable row level security;

revoke all privileges on table public.boardcomment from public, anon, authenticated;
revoke all privileges on table public.boardpost from public, anon, authenticated;
revoke all privileges on table public.chatmessage from public, anon, authenticated;
revoke all privileges on table public.dailychallenge from public, anon, authenticated;
revoke all privileges on table public.leaderboardentry from public, anon, authenticated;
revoke all privileges on table public.leaderboardvote from public, anon, authenticated;
revoke all privileges on table public.macrorow from public, anon, authenticated;
revoke all privileges on table public.macrounlock from public, anon, authenticated;
revoke all privileges on table public.papersession from public, anon, authenticated;
revoke all privileges on table public.papertrade from public, anon, authenticated;
revoke all privileges on table public.pointledger from public, anon, authenticated;
revoke all privileges on table public.runnerkey from public, anon, authenticated;
revoke all privileges on table public.runnerlaunchticket from public, anon, authenticated;
revoke all privileges on table public.runsession from public, anon, authenticated;
revoke all privileges on table public.tickernewsaibudget from public, anon, authenticated;
revoke all privileges on table public.tickernewssnapshot from public, anon, authenticated;
revoke all privileges on table public.tickernewsstate from public, anon, authenticated;
revoke all privileges on table public."user" from public, anon, authenticated;
revoke all privileges on table public.usermacro from public, anon, authenticated;
revoke all privileges on table public.whaleholderbalance from public, anon, authenticated;
revoke all privileges on table public.whaleobservation from public, anon, authenticated;

-- Remove two permissive profile inserts that allowed a caller to create a row
-- for another user. Auth-trigger inserts keep their dedicated
-- supabase_auth_admin policy.
drop policy if exists profiles_insert_auth_new_user on public.profiles;
drop policy if exists profiles_insert_own on public.profiles;
create policy profiles_insert_own
on public.profiles
for insert
to authenticated
with check ((select auth.uid()) = id);

drop policy if exists profiles_select_own on public.profiles;
create policy profiles_select_own
on public.profiles
for select
to authenticated
using ((select auth.uid()) = id);

drop policy if exists profiles_update_own on public.profiles;
create policy profiles_update_own
on public.profiles
for update
to authenticated
using ((select auth.uid()) = id)
with check ((select auth.uid()) = id);

-- Pin SECURITY DEFINER search paths. Conditional blocks preserve the migration
-- replay for installations that do not contain tables owned by the other apps
-- sharing this Supabase project.
do $migration$
begin
  if to_regprocedure('public.is_admin()') is not null then
    execute 'alter function public.is_admin() set search_path = ''''';
  end if;

  if to_regprocedure('public.decrement_stock(uuid,integer)') is not null then
    execute 'alter function public.decrement_stock(uuid, integer) set search_path = ''''';
  end if;

  if to_regprocedure('public.increment_download(uuid)') is not null
    and to_regclass('public.skills') is not null
  then
    execute $function$
      create or replace function public.increment_download(skill_id uuid)
      returns void
      language sql
      set search_path = ''
      as $body$
        update public.skills
        set download_count = download_count + 1
        where id = skill_id;
      $body$
    $function$;
  end if;
end
$migration$;
