-- Security contract for tables owned by the gg_parrot backend.
-- The browser never accesses these tables through Supabase Data API roles.
do $$
declare
  table_name text;
  function_signature text;
  owned_tables constant text[] := array[
    'boardcomment',
    'boardpost',
    'chatmessage',
    'dailychallenge',
    'leaderboardcarryover',
    'leaderboardentry',
    'leaderboardvote',
    'macrorow',
    'macrounlock',
    'marketnewssummary',
    'newstitletranslation',
    'papersession',
    'papertrade',
    'pointledger',
    'runnerkey',
    'runnerlaunchticket',
    'runsession',
    'tickernewsaibudget',
    'tickernewssnapshot',
    'tickernewsstate',
    'user',
    'usermacro',
    'whaleholderbalance',
    'whaleobservation'
  ];
begin
  foreach table_name in array owned_tables loop
    if to_regclass(format('public.%I', table_name)) is null then
      raise exception 'expected gg_parrot table public.% is missing', table_name;
    end if;

    if not exists (
      select 1
      from pg_catalog.pg_class as c
      join pg_catalog.pg_namespace as n on n.oid = c.relnamespace
      where n.nspname = 'public'
        and c.relname = table_name
        and c.relrowsecurity
    ) then
      raise exception 'RLS is disabled on public.%', table_name;
    end if;

    if has_table_privilege('anon', format('public.%I', table_name), 'SELECT')
      or has_table_privilege('anon', format('public.%I', table_name), 'INSERT')
      or has_table_privilege('anon', format('public.%I', table_name), 'UPDATE')
      or has_table_privilege('anon', format('public.%I', table_name), 'DELETE')
      or has_table_privilege('authenticated', format('public.%I', table_name), 'SELECT')
      or has_table_privilege('authenticated', format('public.%I', table_name), 'INSERT')
      or has_table_privilege('authenticated', format('public.%I', table_name), 'UPDATE')
      or has_table_privilege('authenticated', format('public.%I', table_name), 'DELETE')
    then
      raise exception 'Supabase Data API roles can access public.%', table_name;
    end if;
  end loop;

  if exists (
    select 1
    from pg_catalog.pg_policies
    where schemaname = 'public'
      and tablename = 'profiles'
      and policyname = 'profiles_insert_auth_new_user'
  ) then
    raise exception 'unsafe profiles_insert_auth_new_user policy still exists';
  end if;

  if not exists (
    select 1
    from pg_catalog.pg_policies
    where schemaname = 'public'
      and tablename = 'profiles'
      and policyname = 'profiles_insert_own'
      and cmd = 'INSERT'
      and roles = array['authenticated']::name[]
      and with_check is not null
      and with_check <> 'true'
      and with_check like '%auth.uid()%'
      and with_check like '%id%'
  ) then
    raise exception 'profiles_insert_own is not restricted to the authenticated owner';
  end if;

  if not exists (
    select 1
    from pg_catalog.pg_policies
    where schemaname = 'public'
      and tablename = 'profiles'
      and policyname = 'profiles_select_own'
      and cmd = 'SELECT'
      and roles = array['authenticated']::name[]
      and qual is not null
      and qual like '%auth.uid()%'
      and qual like '%id%'
  ) then
    raise exception 'profiles_select_own is not restricted to the authenticated owner';
  end if;

  if not exists (
    select 1
    from pg_catalog.pg_policies
    where schemaname = 'public'
      and tablename = 'profiles'
      and policyname = 'profiles_update_own'
      and cmd = 'UPDATE'
      and roles = array['authenticated']::name[]
      and qual is not null
      and with_check is not null
      and qual like '%auth.uid()%'
      and qual like '%id%'
      and with_check like '%auth.uid()%'
      and with_check like '%id%'
  ) then
    raise exception 'profiles_update_own lacks owner USING/WITH CHECK restrictions';
  end if;

  foreach function_signature in array array[
    'public.decrement_stock(uuid,integer)',
    'public.increment_download(uuid)',
    'public.is_admin()'
  ] loop
    if to_regprocedure(function_signature) is null then
      raise exception 'expected function % is missing', function_signature;
    end if;

    if not exists (
      select 1
      from pg_catalog.pg_proc
      where oid = to_regprocedure(function_signature)
        and coalesce(proconfig, array[]::text[])
          @> array['search_path=""']::text[]
    ) then
      raise exception 'function % does not pin an empty search_path', function_signature;
    end if;
  end loop;

  if pg_get_functiondef(to_regprocedure('public.increment_download(uuid)'))
    not like '%update public.skills%'
  then
    raise exception 'increment_download does not schema-qualify public.skills';
  end if;
end
$$;
