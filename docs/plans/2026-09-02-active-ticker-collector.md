# Active Ticker Collector Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Collect and analyze news only for canonical tickers with a live macro session, while sharing one snapshot across all users and securing gg_parrot's Supabase tables from Data API roles.

**Architecture:** `RunSession` heartbeat rows are the only collection leases. Repository discovery deduplicates live market symbols into canonical assets; the existing Prefect cycle, snapshot unique key, and shared state perform one fetch/analysis per asset. A Supabase migration enables RLS and revokes client-role access only on repository-owned tables, preserving server access through the verified `postgres` bypass role.

**Tech Stack:** Python 3.12, SQLModel/Postgres, Prefect 3, pytest, Supabase CLI 2.109.1, PostgreSQL RLS.

---

### Task 1: Make live sessions the only discovery source

**Files:**
- Modify: `backend/tests/test_position_news_repository.py`
- Modify: `backend/app/agent_features/position_news/repository.py:202-240`
- Modify: `backend/PREFECT_POSITION_NEWS.md`

**Step 1: Write the failing tests**

Replace the fixed-universe contract with tests that assert:

```python
def test_discovery_is_empty_without_live_sessions(db):
    assert repository.discover_tracked_symbols(db) == []

def test_discovery_deduplicates_live_sessions_by_canonical_asset(db, monkeypatch):
    # Two users run BMT quote variants and one user runs ETH.
    # Expect exactly ["BMT", "ETH"].
```

Keep explicit stale, stopped, and fair-order cases.

**Step 2: Run tests to verify RED**

Run: `pytest -q backend/tests/test_position_news_repository.py -k discovery`

Expected: the no-session test fails because the built-in universe is returned.

**Step 3: Implement the minimal discovery change**

Initialize `assets` as an empty set, add only recent `running` sessions after canonicalization, and update the docstring. Do not add a new activation table.

**Step 4: Run tests to verify GREEN**

Run: `pytest -q backend/tests/test_position_news_repository.py -k discovery`

Expected: all discovery tests pass.

**Step 5: Update the operational document**

Document that zero active sessions produce zero source/AI calls and same-ticker sessions share one snapshot.

### Task 2: Add an executable RLS contract

**Files:**
- Create: `supabase/tests/gg_parrot_rls.sql`
- Create via CLI: `supabase/migrations/<timestamp>_secure_gg_parrot_tables.sql`

**Step 1: Write and run the failing remote contract**

The SQL must raise when any owned table has RLS disabled or when `anon`/`authenticated` retains a table privilege. Run it using:

`supabase db query --db-url "$db_url" --file supabase/tests/gg_parrot_rls.sql`

Expected: FAIL before the migration because all 21 owned tables are exposed.

**Step 2: Generate the migration with the CLI**

Run: `supabase migration new secure_gg_parrot_tables`

For each owned table, use:

```sql
alter table if exists public.<table> enable row level security;
revoke all privileges on table public.<table> from anon, authenticated;
```

Also replace unsafe `profiles` policies with explicit `TO authenticated` ownership checks and pin the three warned functions' `search_path` without changing their intended behavior.

**Step 3: Verify in a rollback transaction**

Execute `begin`, the migration, the contract, and `rollback` against the remote DB. Expected: contract passes and the remote schema remains unchanged.

### Task 3: Apply and verify the Supabase migration

**Files:**
- Modify: `supabase/migrations/<timestamp>_secure_gg_parrot_tables.sql`

**Step 1: Dry-run migration history**

Run: `supabase db push --db-url "$db_url" --dry-run`

Expected: only `secure_gg_parrot_tables` is pending.

**Step 2: Apply the migration**

Run: `supabase db push --db-url "$db_url" --yes`.

**Step 3: Verify security and server access**

Run both RLS contracts, `supabase db advisors --type security`, and a read/write-safe FastAPI/Prefect DB smoke test. Confirm every base table in the exposed `public` schema has RLS enabled, Data API roles cannot access server-only tables, and `postgres`/`service_role` access remains available.

### Task 4: Regression verification and delivery

**Files:**
- Test: `backend/tests/test_position_news_repository.py`
- Test: `backend/tests/test_position_news_collector.py`
- Test: `backend/tests/test_agent_position_news.py`
- Test: `backend/tests/test_position_news_router.py`

**Step 1: Run focused tests**

Run the position-news repository, collector, service, router, workflow, and Render blueprint suites in the project Docker image with `DATABASE_URL=`.

**Step 2: Run live discovery smoke checks**

Confirm active duplicate market symbols return one canonical asset and stopped/stale sessions do not trigger collection.

**Step 3: Commit and open a PR**

Commit with a conventional message, push `feat/active-ticker-collector`, create a PR to `main`, verify checks, and merge using the repository's existing merge-commit workflow.
