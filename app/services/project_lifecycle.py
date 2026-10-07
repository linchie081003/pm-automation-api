"""Timeline edit rules by project phase."""
from app.models import Project, ProjectPhase, ProjectSph, ProjectStatus


SPH_TIMELINE_LOCKED_PHASES = frozenset(
    {
        ProjectPhase.kickoff,
        ProjectPhase.in_delivery,
        ProjectPhase.bast,
        ProjectPhase.closed,
    }
)


def sph_timeline_editable(project: Project, *, sph: ProjectSph | None = None) -> bool:
    """Edit draft timeline di tab SPH (sebelum SPH diselesaikan / lanjut Kick Off)."""
    if project.kickoff_timeline_confirmed_at:
        return False
    if project.current_phase in SPH_TIMELINE_LOCKED_PHASES:
        return False
    if sph is not None and sph.draft_baseline_generated_at:
        return False
    return True


def kickoff_draft_timeline_editable(project: Project, *, sph: ProjectSph | None = None) -> bool:
    """Edit draft timeline di tab Kick Off setelah SPH selesai, sebelum konfirmasi timeline."""
    if prior_phase_data_locked(project):
        return False
    if project.kickoff_timeline_confirmed_at:
        return False
    if project.delivery_started_at:
        return False
    if sph is None or not sph.draft_baseline_generated_at:
        return False
    return project.current_phase in (
        ProjectPhase.po_received,
        ProjectPhase.pre_kickoff,
        ProjectPhase.kickoff,
    )


def draft_timeline_editable(project: Project, *, sph: ProjectSph | None = None) -> bool:
    return sph_timeline_editable(project, sph=sph) or kickoff_draft_timeline_editable(
        project, sph=sph
    )


def sph_form_editable(project: Project, sph: ProjectSph | None) -> bool:
    if prior_phase_data_locked(project):
        return False
    return sph_timeline_editable(project, sph=sph)


DELIVERY_AND_LATER = frozenset(
    {ProjectPhase.in_delivery, ProjectPhase.bast, ProjectPhase.closed}
)


def prior_phase_data_locked(project: Project) -> bool:
    """SPH / Kick Off — read-only setelah masuk delivery."""
    return project.current_phase in DELIVERY_AND_LATER


def po_form_editable(project: Project) -> bool:
    """PO dapat diisi/diubah sampai proyek closed (wajib lengkap sebelum BAST/closing)."""
    if project.status == ProjectStatus.closed:
        return False
    return project.current_phase != ProjectPhase.closed


def kickoff_milestones_editable(project: Project) -> bool:
    """Ubah struktur timeline (nama, tanggal rencana, hierarki) — hanya sebelum delivery."""
    if project.delivery_started_at:
        return False
    return project.current_phase in (
        ProjectPhase.pre_kickoff,
        ProjectPhase.kickoff,
    )


def milestone_progress_editable(project: Project) -> bool:
    """Update status / actual saat delivery; baseline tetap locked."""
    if kickoff_milestones_editable(project):
        return True
    return project.current_phase in (
        ProjectPhase.in_delivery,
        ProjectPhase.bast,
    )


def timeline_project_start_editable(project: Project) -> bool:
    """Ubah tanggal start proyek di tab Timeline (geser jadwal delivery, draft SPH/KO tetap)."""
    if not project.kickoff_timeline_confirmed_at:
        return False
    return project.current_phase != ProjectPhase.closed
