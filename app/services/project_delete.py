"""Validate and delete projects."""
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import (
    ActivityLog,
    ApprovalRequest,
    ApprovalStatus,
    ClickUpTaskCache,
    Document,
    Milestone,
    PreKickoffPack,
    ProgressSnapshot,
    Project,
    ProjectEvaluation,
    ProjectHealthConfig,
    ProjectHealthSnapshot,
    ProjectMember,
    ProjectPhase,
    ProjectPhaseRecord,
    ProjectPo,
    ProjectSph,
    RebaselineRequest,
    ScheduleBaseline,
    ScheduleBaselineMilestone,
    WeeklyReport,
)


def validate_project_deletion(db: Session, project: Project) -> None:
    if project.current_phase == ProjectPhase.closed:
        raise ValueError("Proyek sudah ditutup — tidak dapat dihapus")
    if project.delivery_started_at or project.current_phase in (
        ProjectPhase.in_delivery,
        ProjectPhase.bast,
    ):
        raise ValueError("Proyek sudah delivery — tidak dapat dihapus")
    pending = db.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.project_id == project.id,
            ApprovalRequest.status == ApprovalStatus.pending,
        )
    )
    if pending:
        raise ValueError("Masih ada approval fase pending — selesaikan atau batalkan dulu")


def delete_project(db: Session, project: Project) -> None:
    validate_project_deletion(db, project)
    pid = project.id
    db.execute(delete(ProgressSnapshot).where(ProgressSnapshot.project_id == pid))
    db.execute(delete(ProjectHealthSnapshot).where(ProjectHealthSnapshot.project_id == pid))
    db.execute(delete(WeeklyReport).where(WeeklyReport.project_id == pid))
    db.execute(delete(ClickUpTaskCache).where(ClickUpTaskCache.project_id == pid))
    db.execute(delete(RebaselineRequest).where(RebaselineRequest.project_id == pid))
    db.execute(delete(ProjectEvaluation).where(ProjectEvaluation.project_id == pid))
    db.execute(delete(ApprovalRequest).where(ApprovalRequest.project_id == pid))
    db.execute(delete(ActivityLog).where(ActivityLog.project_id == pid))
    db.execute(delete(Document).where(Document.project_id == pid))
    baseline_ids = list(
        db.scalars(select(ScheduleBaseline.id).where(ScheduleBaseline.project_id == pid)).all()
    )
    if baseline_ids:
        db.execute(
            delete(ScheduleBaselineMilestone).where(
                ScheduleBaselineMilestone.baseline_id.in_(baseline_ids)
            )
        )
        db.execute(delete(ScheduleBaseline).where(ScheduleBaseline.project_id == pid))
    db.execute(delete(Milestone).where(Milestone.project_id == pid))
    db.execute(delete(ProjectPhaseRecord).where(ProjectPhaseRecord.project_id == pid))
    db.execute(delete(ProjectMember).where(ProjectMember.project_id == pid))
    db.execute(delete(ProjectHealthConfig).where(ProjectHealthConfig.project_id == pid))
    db.execute(delete(PreKickoffPack).where(PreKickoffPack.project_id == pid))
    db.execute(delete(ProjectSph).where(ProjectSph.project_id == pid))
    db.execute(delete(ProjectPo).where(ProjectPo.project_id == pid))
    db.delete(project)
