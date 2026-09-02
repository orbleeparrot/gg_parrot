-- These legacy tables have no auth.uid()-compatible ownership column and were
-- fully exposed to anon/authenticated. Keep them server-only: postgres and
-- service_role retain their direct privileges and bypass behavior.

alter table public.agg_funnel_daily enable row level security;
alter table public.agg_heatmap enable row level security;
alter table public.agg_page_events enable row level security;
alter table public.butt_comments enable row level security;
alter table public.funnel_definition enable row level security;
alter table public.hecto_promo_influencers enable row level security;
alter table public.holder_last_balance enable row level security;
alter table public.holder_max enable row level security;
alter table public.mcps enable row level security;
alter table public.raw_events enable row level security;
alter table public.skills enable row level security;
alter table public.tracker_config enable row level security;
alter table public.tracker_settings_latest enable row level security;
alter table public.tracker_settings_versions enable row level security;
alter table public.tracker_user enable row level security;

revoke all privileges on table public.agg_funnel_daily from public, anon, authenticated;
revoke all privileges on table public.agg_heatmap from public, anon, authenticated;
revoke all privileges on table public.agg_page_events from public, anon, authenticated;
revoke all privileges on table public.butt_comments from public, anon, authenticated;
revoke all privileges on table public.funnel_definition from public, anon, authenticated;
revoke all privileges on table public.hecto_promo_influencers from public, anon, authenticated;
revoke all privileges on table public.holder_last_balance from public, anon, authenticated;
revoke all privileges on table public.holder_max from public, anon, authenticated;
revoke all privileges on table public.mcps from public, anon, authenticated;
revoke all privileges on table public.raw_events from public, anon, authenticated;
revoke all privileges on table public.skills from public, anon, authenticated;
revoke all privileges on table public.tracker_config from public, anon, authenticated;
revoke all privileges on table public.tracker_settings_latest from public, anon, authenticated;
revoke all privileges on table public.tracker_settings_versions from public, anon, authenticated;
revoke all privileges on table public.tracker_user from public, anon, authenticated;
