"""Human-readable labels for activity_log.action codes."""

ACTION_LABELS: dict[str, str] = {
    "phase.advanced": "Fase proyek dilanjutkan",
    "phase.transition.requested": "Permintaan transisi fase (approval)",
    "phase.transition.approved": "Transisi fase disetujui",
    "phase.transition.rejected": "Transisi fase ditolak",
    "project.closed": "Proyek ditutup (Closing)",
    "document.uploaded": "Dokumen diunggah",
    "document.deleted": "Dokumen dihapus",
    "sph.updated": "Data SPH diperbarui",
    "sph.draft_timeline.saved": "Draft timeline SPH disimpan",
    "sph.draft_timeline.generated": "Draft timeline SPH digenerate",
    "sph.advance_kickoff": "Lanjut ke fase Kick Off (timeline SPH)",
    "po.updated": "Data PO diperbarui",
    "pre_kickoff.updated": "Materi Kick Off diperbarui",
    "pre_kickoff.timeline_confirmed": "Timeline Kick Off dikonfirmasi",
    "milestone.updated": "Milestone diperbarui",
    "timeline.project_start": "Project Start (tanggal mulai proyek)",
    "weekly_report.generated": "Weekly report digenerate",
    "progress.week.saved": "Progress mingguan disimpan",
    "project.updated": "Metadata proyek diperbarui",
    "bast.updated": "Checklist BAST / Closing diperbarui",
    "clickup.project_start.partial": "ClickUp (sebagian) saat Project Start",
    "clickup.synced": "Sync progress dari ClickUp",
    "milestone.created": "Milestone/timeline item ditambah",
    "milestone.deleted": "Milestone/timeline item dihapus",
}


def action_label(action: str) -> str:
    return ACTION_LABELS.get(action, action.replace(".", " — ").replace("_", " "))
