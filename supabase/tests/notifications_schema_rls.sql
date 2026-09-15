-- 알림 스키마(notifications)는 백엔드 전용이다: 모든 테이블에 RLS 가 켜져 있고
-- anon/authenticated 는 스키마 사용 권한도, 테이블 권한도 없어야 한다.
do $$
declare
  table_name text;
  disabled_tables text;
  role_name text;
  owned_tables constant text[] := array['message', 'receipt'];
begin
  if not exists (select 1 from pg_namespace where nspname = 'notifications') then
    raise exception 'schema notifications is missing';
  end if;

  select string_agg(format('notifications.%I', c.relname), ', ' order by c.relname)
  into disabled_tables
  from pg_catalog.pg_class as c
  join pg_catalog.pg_namespace as n on n.oid = c.relnamespace
  where n.nspname = 'notifications'
    and c.relkind in ('r', 'p')
    and not c.relrowsecurity;

  if disabled_tables is not null then
    raise exception 'RLS is disabled on: %', disabled_tables;
  end if;

  foreach role_name in array array['anon', 'authenticated'] loop
    if has_schema_privilege(role_name, 'notifications', 'USAGE') then
      raise exception 'role % may use schema notifications', role_name;
    end if;
    foreach table_name in array owned_tables loop
      if has_table_privilege(role_name, format('notifications.%I', table_name), 'SELECT')
        or has_table_privilege(role_name, format('notifications.%I', table_name), 'INSERT')
        or has_table_privilege(role_name, format('notifications.%I', table_name), 'UPDATE')
        or has_table_privilege(role_name, format('notifications.%I', table_name), 'DELETE') then
        raise exception 'role % has privileges on notifications.%', role_name, table_name;
      end if;
    end loop;
  end loop;
end
$$;
