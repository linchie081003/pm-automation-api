from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ApprovalRequest,
    ApprovalStatus,
    PreKickoffPack,
    Project,
    ProjectPhase,
    ProjectPhaseRecord,
    ProjectPo,
    ProjectSph,
    ProjectStatus,
    ProgressSnapshot,
    ProgressSnapshotSource,
)
from app.core.timezone import now_jakarta, today_jakarta
from app.services.activity import log_activity
from app.services.pre_kickoff import check_pack_complete
from app.services.progress import resolve_actual_progress
from app.services.schedule import monday_of, promote_draft_baseline

PHASE_TRANSITIONS: list[tuple[ProjectPhase, ProjectPhase]] = [
    (ProjectPhase.po_received, ProjectPhase.kickoff),
    (ProjectPhase.pre_kickoff, ProjectPhase.kickoff),
    (ProjectPhase.kickoff, ProjectPhase.in_delivery),
    (ProjectPhase.in_delivery, ProjectPhase.bast),
    (ProjectPhase.bast, ProjectPhase.closed),
]


def next_phase(current: ProjectPhase) -> ProjectPhase | None:
    for f, t in PHASE_TRANSITIONS:
        if f == current:
            return t
    return None


def validate_phase_transition(db: Session, project: Project, target: ProjectPhase) -> None:
    from app.services.draft_timeline import list_draft_rows

    if target == ProjectPhase.kickoff:
        sph = db.get(ProjectSph, project.id)
        if not sph or not sph.draft_baseline_generated_at:
            raise ValueError(
                "Selesaikan SPH (draft + termin) lalu «Lanjut ke fase berikutnya Kick Off»"
            )
        if not list_draft_rows(db, project.id):
            raise ValueError("Draft timeline SPH belum ada")
    if target == ProjectPhase.in_delivery:
        sph = db.get(ProjectSph, project.id)
        if not sph or not sph.draft_baseline_generated_at:
            raise ValueError(
                "Generate timeline (SPH lengkap + draft + termin) dari tab SPH terlebih dahulu"
            )
        pack = db.get(PreKickoffPack, project.id)
        if not check_pack_complete(pack):
            raise ValueError("Materi kick off belum lengkap")
        if not project.kickoff_timeline_confirmed_at:
            raise ValueError("Konfirmasi timeline kick off (Kick off OK) terlebih dahulu")
    if target == ProjectPhase.bast:
        from app.services.po_validation import po_closing_error_message, po_complete_for_closing

        if not po_complete_for_closing(db, project.id):
            raise ValueError(po_closing_error_message())
        progress = resolve_actual_progress(db, project, today_jakarta())
        if progress < 100:
            raise ValueError("Progress proyek harus 100% sebelum fase BAST")
        snap = db.scalar(
            select(ProgressSnapshot)
            .where(
                ProgressSnapshot.project_id == project.id,
                ProgressSnapshot.source == ProgressSnapshotSource.weekly_report,
            )
            .limit(1)
        )
        if not snap:
            raise ValueError("Generate minimal satu weekly report sebelum fase BAST")
    if target == ProjectPhase.closed:
        from app.services.po_validation import po_closing_error_message, po_complete_for_closing

        if not po_complete_for_closing(db, project.id):
            raise ValueError(po_closing_error_message())
        progress = resolve_actual_progress(db, project, today_jakarta())
        if progress < 100:
            raise ValueError("Progress harus 100% sebelum closing")
        checklist = project.bast_checklist or {}
        items = checklist.get("items")
        if isinstance(items, list) and items:
            if not all(isinstance(i, dict) and i.get("done") for i in items):
                raise ValueError("BAST checklist belum lengkap (centang semua kriteria)")
        elif not checklist.get("complete"):
            raise ValueError("BAST checklist belum lengkap")


def ensure_phase_row(db: Session, project_id: int, phase: ProjectPhase) -> None:
    row = db.scalar(
        select(ProjectPhaseRecord).where(
            ProjectPhaseRecord.project_id == project_id,
            ProjectPhaseRecord.phase == phase,
        )
    )
    if not row:
        db.add(
            ProjectPhaseRecord(
                project_id=project_id,
                phase=phase,
                started_at=now_jakarta(),
            )
        )


def complete_phase_row(db: Session, project_id: int, phase: ProjectPhase) -> None:
    row = db.scalar(
        select(ProjectPhaseRecord).where(
            ProjectPhaseRecord.project_id == project_id,
            ProjectPhaseRecord.phase == phase,
        )
    )
    if row and not row.completed_at:
        row.completed_at = now_jakarta()


def request_phase_transition(
    db: Session, project: Project, user_id: int
) -> ApprovalRequest:
    pending = db.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.project_id == project.id,
            ApprovalRequest.status == ApprovalStatus.pending,
        )
    )
    if pending:
        raise ValueError("Approval already pending")

    target = next_phase(project.current_phase)
    if not target:
        raise ValueError("No further phase transition")

    validate_phase_transition(db, project, target)

    req = ApprovalRequest(
        project_id=project.id,
        from_phase=project.current_phase,
        to_phase=target,
        requested_by_id=user_id,
        status=ApprovalStatus.pending,
        reviewer_role="management",
    )
    db.add(req)
    log_activity(
        db,
        project.id,
        user_id,
        "phase.transition.requested",
        {"from": project.current_phase.value, "to": target.value},
    )
    return req


def apply_phase_transition(db: Session, project: Project, to_phase: ProjectPhase) -> None:
    validate_phase_transition(db, project, to_phase)
    from_phase = project.current_phase
    complete_phase_row(db, project.id, from_phase)
    project.current_phase = to_phase
    ensure_phase_row(db, project.id, to_phase)

    if to_phase == ProjectPhase.in_delivery:
        project.delivery_started_at = now_jakarta()
        if not project.po_due_date and project.planned_end_date:
            project.po_due_date = project.planned_end_date
        promote_draft_baseline(
            db,
            project.id,
            monday_of(today_jakarta()),
            None,
        )
        if project.clickup_enabled and project.kickoff_timeline_confirmed_at:
            from app.services.clickup import ClickUpSyncError, sync_project_tasks
            from app.services.clickup_hierarchy import provision_folder_structure

            try:
                provision_folder_structure(db, project, force=False)
                sync_project_tasks(db, project)
            except (ValueError, ClickUpSyncError) as exc:
                log_activity(
                    db,
                    project.id,
                    None,
                    "clickup.project_start.partial",
                    {"error": str(exc)},
                )
    if to_phase == ProjectPhase.closed:
        project.status = ProjectStatus.closed
        project.bast_completed_at = now_jakarta()


def decide_approval(
    db: Session,
    approval: ApprovalRequest,
    approve: bool,
    decider_id: int,
    comment: str | None,
) -> Project:
    project = db.get(Project, approval.project_id)
    if not project:
        raise ValueError("Project not found")
    if approval.status != ApprovalStatus.pending:
        raise ValueError("Already decided")

    approval.decided_by_id = decider_id
    approval.decided_at = now_jakarta()
    approval.comment = comment
    if approve:
        approval.status = ApprovalStatus.approved
        apply_phase_transition(db, project, approval.to_phase)
        log_activity(
            db,
            project.id,
            decider_id,
            "phase.transition.approved",
            {"to": approval.to_phase.value},
        )
    else:
        approval.status = ApprovalStatus.rejected
        log_activity(
            db,
            project.id,
            decider_id,
            "phase.transition.rejected",
            {"to": approval.to_phase.value},
        )
    return project
