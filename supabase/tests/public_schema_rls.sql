-- Every base table exposed through the public schema must have RLS enabled.
-- Legacy tables without an auth ownership model are server-only.
do $$
declare
  table_name text;
  disabled_tables text;
  server_only_tables constant text[] := array[
    'agg_funnel_daily',
    'agg_heatmap',
    'agg_page_events',
    'butt_comments',
    'funnel_definition',
    'hecto_promo_influencers',
    'holder_last_balance',
    'holder_max',
    'mcps',
    'raw_events',
    'skills',
    'tracker_config',
    'tracker_settings_latest',
    'tracker_settings_versions',
    'tracker_user'
  ];
begin
  select string_agg(format('public.%I', c.relname), ', ' order by c.relname)
  into disabled_tables
  from pg_catalog.pg_class as c
  join pg_catalog.pg_namespace as n on n.oid = c.relnamespace
  where n.nspname = 'public'
    and c.relkind in ('r', 'p')
    and not c.relrowsecurity;

  if disabled_tables is not null then
    raise exception 'RLS is disabled on: %', disabled_tables;
  end if;

  foreach table_name in array server_only_tables loop
    if has_table_privilege('anon', format('public.%I', table_name), 'SELECT')
      or has_table_privilege('anon', format('public.%I', table_name), 'INSERT')
      or has_table_privilege('anon', format('public.%I', table_name), 'UPDATE')
      or has_table_privilege('anon', format('public.%I', table_name), 'DELETE')
      or has_table_privilege('authenticated', format('public.%I', table_name), 'SELECT')
      or has_table_privilege('authenticated', format('public.%I', table_name), 'INSERT')
      or has_table_privilege('authenticated', format('public.%I', table_name), 'UPDATE')
      or has_table_privilege('authenticated', format('public.%I', table_name), 'DELETE')
    then
      raise exception 'Data API roles can access server-only table public.%', table_name;
    end if;
  end loop;
end
$$;
