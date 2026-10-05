-- Verifikasi setelah migrasi data local → production.

SELECT 'users' AS entity, COUNT(*)::bigint AS row_count FROM public.users
UNION ALL SELECT 'roles', COUNT(*) FROM public.roles
UNION ALL SELECT 'permissions', COUNT(*) FROM public.permissions
UNION ALL SELECT 'user_roles', COUNT(*) FROM public.user_roles
UNION ALL SELECT 'role_permissions', COUNT(*) FROM public.role_permissions
UNION ALL SELECT 'projects', COUNT(*) FROM public.projects
UNION ALL SELECT 'project_members', COUNT(*) FROM public.project_members
UNION ALL SELECT 'milestones', COUNT(*) FROM public.milestones
UNION ALL SELECT 'schedule_baselines', COUNT(*) FROM public.schedule_baselines
UNION ALL SELECT 'schedule_baseline_milestones', COUNT(*) FROM public.schedule_baseline_milestones
UNION ALL SELECT 'progress_snapshots', COUNT(*) FROM public.progress_snapshots
UNION ALL SELECT 'weekly_reports', COUNT(*) FROM public.weekly_reports
UNION ALL SELECT 'timeline_templates', COUNT(*) FROM public.timeline_templates
UNION ALL SELECT 'project_sph', COUNT(*) FROM public.project_sph
UNION ALL SELECT 'project_po', COUNT(*) FROM public.project_po
UNION ALL SELECT 'pre_kickoff_packs', COUNT(*) FROM public.pre_kickoff_packs
UNION ALL SELECT 'clickup_task_cache', COUNT(*) FROM public.clickup_task_cache
UNION ALL SELECT 'integration_settings', COUNT(*) FROM public.integration_settings
UNION ALL SELECT 'rebaseline_requests', COUNT(*) FROM public.rebaseline_requests
UNION ALL SELECT 'project_change_requests', COUNT(*) FROM public.project_change_requests
ORDER BY entity;

-- FK sanity: milestone tanpa proyek
SELECT m.id, m.project_id, m.name
FROM public.milestones m
LEFT JOIN public.projects p ON p.id = m.project_id
WHERE p.id IS NULL
LIMIT 20;

-- Proyek + jumlah milestone (bandingkan dengan local)
SELECT p.id, p.code, p.name, COUNT(m.id) AS milestone_count
FROM public.projects p
LEFT JOIN public.milestones m ON m.project_id = p.id
GROUP BY p.id, p.code, p.name
ORDER BY p.id;
