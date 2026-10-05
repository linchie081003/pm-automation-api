-- Kosongkan semua data aplikasi PDC di production sebelum import dump local.
-- JANGAN jalankan jika belum backup (lihat 00_petunjuk.sql).

BEGIN;

SET lock_timeout = '30s';
SET statement_timeout = '10min';

TRUNCATE TABLE
  public.activity_log,
  public.approval_requests,
  public.clickup_task_cache,
  public.clickup_structure_templates,
  public.documents,
  public.integration_settings,
  public.milestones,
  public.pre_kickoff_packs,
  public.progress_snapshots,
  public.project_change_requests,
  public.project_evaluations,
  public.project_health_config,
  public.project_health_snapshots,
  public.project_members,
  public.project_phases,
  public.project_po,
  public.project_roster_entries,
  public.project_sph,
  public.projects,
  public.rebaseline_requests,
  public.resource_rates,
  public.role_permissions,
  public.schedule_baseline_milestones,
  public.schedule_baselines,
  public.timeline_templates,
  public.user_roles,
  public.users,
  public.roles,
  public.permissions,
  public.weekly_reports
RESTART IDENTITY CASCADE;

COMMIT;

-- Verifikasi cepat: semua tabel di atas harus 0 baris sebelum import.
SELECT relname AS table_name, n_live_tup AS approx_rows
FROM pg_stat_user_tables
WHERE schemaname = 'public'
  AND relname IN (
    'users', 'projects', 'milestones', 'weekly_reports', 'integration_settings'
  )
ORDER BY relname;
