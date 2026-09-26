"""Fase proyek: label UI dan visibilitas dashboard."""
from app.models import Project, ProjectPhase, ProjectStatus

PHASE_LABEL: dict[str, str] = {
    ProjectPhase.po_received.value: "SPH",
    ProjectPhase.pre_kickoff.value: "Kick Off",
    ProjectPhase.kickoff.value: "Kick Off",
    ProjectPhase.in_delivery.value: "In delivery",
    ProjectPhase.bast.value: "BAST",
    ProjectPhase.closed.value: "Closed",
}


def phase_label(phase: ProjectPhase | str) -> str:
    raw = phase.value if isinstance(phase, ProjectPhase) else str(phase)
    return PHASE_LABEL.get(raw, raw.replace("_", " "))


def visible_on_dashboard(p: Project) -> bool:
    """Dashboard hanya proyek sedang berjalan (bukan SPH awal / closed)."""
    if p.current_phase == ProjectPhase.po_received:
        return False
    if p.status == ProjectStatus.closed or p.current_phase == ProjectPhase.closed:
        return False
    return True
