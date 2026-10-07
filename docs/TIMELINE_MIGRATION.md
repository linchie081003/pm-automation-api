# Migrasi Timeline Engine (v1 → v2)

## Feature flag

Set di `.env` backend:

```env
TIMELINE_ENGINE=v2
```

Default `v1` memakai `schedule_draft_milestone_rows`. `v2` memakai `recalc_timeline_editor_rows` (multi-predecessor, validasi siklus).

Rollback: set `TIMELINE_ENGINE=v1` dan restart API — tidak perlu restore DB kecuali data sudah disimpan dengan engine v2.

## Backup sebelum cutover

Dari folder `backend`:

```powershell
python -m scripts.backup_project_timeline --project-id 123 --out-dir timeline_backups
python -m scripts.backup_project_timeline --all-active --out-dir timeline_backups
```

Compare setelah migrasi:

```powershell
python -m scripts.backup_project_timeline --project-id 123 --compare timeline_backups/timeline_backup_XXX_123.json
```

## pg_dump (full DB)

```powershell
pg_dump -h 127.0.0.1 -U pdc -d pdc -F c -f pdc_pre_timeline_v2.dump
```

## UAT checklist

- Generate draft SPH, recalc, simpan multi-predecessor + lag
- Kick Off konfirmasi → live milestones + predecessor live
- Rebaseline preview/apply
- Apply workspace beta → draft SPH
- Error siklus predecessor → HTTP 400 dengan pesan jelas

## Cutover runbook (production)

1. **Backup massal** (semua proyek aktif): `python -m scripts.backup_project_timeline --all-active --out-dir timeline_backups`
2. **Staging**: set `TIMELINE_ENGINE=v2`, jalankan checklist UAT di atas pada 1–2 proyek pilot; bandingkan JSON backup before/after.
3. **Production enable**: `TIMELINE_ENGINE=v2`, restart API; pantau log HTTP 400/409 pada `/sph/draft-timeline/*` dan `/timeline-editor/*`.
4. **Rollback**: `TIMELINE_ENGINE=v1` + restart; restore JSON per proyek hanya jika data v2 sudah ditulis dan perlu revert manual.
5. **UI beta tab** (opsional setelah paritas): frontend `VITE_HIDE_TIMELINE_BETA_TAB=true` — editor resmi ada di tab SPH/Kick Off; beta disembunyikan.
