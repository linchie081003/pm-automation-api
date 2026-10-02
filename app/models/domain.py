import enum
from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ProjectPhase(str, enum.Enum):
    po_received = "po_received"
    pre_kickoff = "pre_kickoff"
    kickoff = "kickoff"
    in_delivery = "in_delivery"
    bast = "bast"
    closed = "closed"


class ProjectStatus(str, enum.Enum):
    active = "active"
    on_hold = "on_hold"
    closed = "closed"


class MilestoneStatus(str, enum.Enum):
    open = "open"
    done = "done"


class ProjectMethodology(str, enum.Enum):
    agile = "agile"
    hybrid = "hybrid"
    waterfall = "waterfall"


class TimelineItemType(str, enum.Enum):
    phase = "phase"
    milestone = "milestone"
    task = "task"
    subtask = "subtask"


class ProgressSnapshotSource(str, enum.Enum):
    weekly_report = "weekly_report"
    manual_save = "manual_save"
    planned_target = "planned_target"


class ApprovalStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class DocumentType(str, enum.Enum):
    po = "po"
    sph = "sph"
    pre_kickoff_deck = "pre_kickoff_deck"
    kickoff_deck = "kickoff_deck"
    mom = "mom"
    progress_report = "progress_report"
    draft_bast = "draft_bast"
    other = "other"


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    client_name: Mapped[str] = mapped_column(String(255), default="")
    project_manager: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    project_brief: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    contract_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    po_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    po_due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    document_repo_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    current_phase: Mapped[ProjectPhase] = mapped_column(
        Enum(ProjectPhase), default=ProjectPhase.po_received
    )
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(ProjectStatus), default=ProjectStatus.active
    )
    delivery_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    planned_start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    planned_end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    weekly_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    weekly_report_anchor_weekday: Mapped[int] = mapped_column(Integer, default=4)
    weekly_report_cutoff_offset_days: Mapped[int] = mapped_column(Integer, default=6)
    weekly_report_first_anchor_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    bast_checklist: Mapped[dict] = mapped_column(JSONB, default=dict)
    bast_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    clickup_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    clickup_list_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    clickup_list_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    clickup_space_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    clickup_folder_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    clickup_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    clickup_provision_status: Mapped[str] = mapped_column(String(32), default="not_requested")
    kickoff_timeline_confirmed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    methodology: Mapped[ProjectMethodology] = mapped_column(
        Enum(ProjectMethodology), default=ProjectMethodology.waterfall
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    milestones: Mapped[list["Milestone"]] = relationship(back_populates="project")
    baselines: Mapped[list["ScheduleBaseline"]] = relationship(back_populates="project")
    progress_snapshots: Mapped[list["ProgressSnapshot"]] = relationship(
        back_populates="project"
    )


class Milestone(Base):
    __tablename__ = "milestones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    target_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    weight_pct: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[MilestoneStatus] = mapped_column(
        Enum(MilestoneStatus), default=MilestoneStatus.open
    )
    actual_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    is_payment_milestone: Mapped[bool] = mapped_column(Boolean, default=False)
    module: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    duration_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    item_type: Mapped[TimelineItemType] = mapped_column(
        Enum(TimelineItemType), default=TimelineItemType.milestone
    )
    parent_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("milestones.id"), nullable=True, index=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    clickup_list_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    clickup_task_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    project: Mapped["Project"] = relationship(back_populates="milestones")


class ScheduleBaseline(Base):
    __tablename__ = "schedule_baselines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    effective_from: Mapped[date] = mapped_column(Date)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    is_draft: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    project: Mapped["Project"] = relationship(back_populates="baselines")
    milestone_rows: Mapped[list["ScheduleBaselineMilestone"]] = relationship(
        back_populates="baseline", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_project_baseline_version"),
    )


class ScheduleBaselineMilestone(Base):
    __tablename__ = "schedule_baseline_milestones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    baseline_id: Mapped[int] = mapped_column(ForeignKey("schedule_baselines.id"), index=True)
    milestone_id: Mapped[Optional[int]] = mapped_column(ForeignKey("milestones.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    target_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    weight_pct: Mapped[float] = mapped_column(Float, default=0.0)
    is_payment_milestone: Mapped[bool] = mapped_column(Boolean, default=False)
    duration_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    item_type: Mapped[TimelineItemType] = mapped_column(
        Enum(TimelineItemType), default=TimelineItemType.milestone
    )
    parent_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("schedule_baseline_milestones.id"), nullable=True, index=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    row_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    predecessor_ref: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    predecessor_link_type: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)

    baseline: Mapped["ScheduleBaseline"] = relationship(back_populates="milestone_rows")


class ProgressSnapshot(Base):
    __tablename__ = "progress_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    week_start: Mapped[date] = mapped_column(Date)
    week_end: Mapped[date] = mapped_column(Date)
    baseline_version: Mapped[int] = mapped_column(Integer)
    planned_cumulative_pct: Mapped[float] = mapped_column(Float)
    actual_cumulative_pct: Mapped[float] = mapped_column(Float)
    spi_at_week: Mapped[float] = mapped_column(Float)
    source: Mapped[ProgressSnapshotSource] = mapped_column(Enum(ProgressSnapshotSource))
    weekly_report_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("weekly_reports.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    project: Mapped["Project"] = relationship(back_populates="progress_snapshots")

    __table_args__ = (
        UniqueConstraint("project_id", "week_start", name="uq_project_week_snapshot"),
    )


class WeeklyReport(Base):
    __tablename__ = "weekly_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    week_start: Mapped[date] = mapped_column(Date)
    week_end: Mapped[date] = mapped_column(Date)
    baseline_version: Mapped[int] = mapped_column(Integer)
    summary: Mapped[dict] = mapped_column(JSONB, default=dict)
    frozen_metrics: Mapped[dict] = mapped_column(JSONB, default=dict)
    generated_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    xlsx_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    pptx_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    __table_args__ = (
        UniqueConstraint("project_id", "week_start", name="uq_project_week_report"),
    )


class TimelineTemplate(Base):
    __tablename__ = "timeline_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    methodology: Mapped[ProjectMethodology] = mapped_column(Enum(ProjectMethodology))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    items: Mapped[list] = mapped_column(JSONB, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ProjectRosterEntry(Base):
    __tablename__ = "project_roster_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    full_name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255), default="")
    role_label: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ProjectHealthConfig(Base):
    __tablename__ = "project_health_config"

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    spi_green_min: Mapped[float] = mapped_column(Float, default=1.0)
    spi_yellow_min: Mapped[float] = mapped_column(Float, default=0.9)
    progress_gap_green_max: Mapped[float] = mapped_column(Float, default=5.0)
    progress_gap_yellow_max: Mapped[float] = mapped_column(Float, default=15.0)
    rag_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class ProjectPhaseRecord(Base):
    __tablename__ = "project_phases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    phase: Mapped[ProjectPhase] = mapped_column(Enum(ProjectPhase))
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    from_phase: Mapped[ProjectPhase] = mapped_column(Enum(ProjectPhase))
    to_phase: Mapped[ProjectPhase] = mapped_column(Enum(ProjectPhase))
    requested_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus), default=ApprovalStatus.pending
    )
    reviewer_role: Mapped[str] = mapped_column(String(32), default="management")
    decided_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[Optional[int]] = mapped_column(ForeignKey("projects.id"), nullable=True)
    phase: Mapped[Optional[ProjectPhase]] = mapped_column(Enum(ProjectPhase), nullable=True)
    doc_type: Mapped[DocumentType] = mapped_column(Enum(DocumentType))
    filename: Mapped[str] = mapped_column(String(512))
    storage_path: Mapped[str] = mapped_column(String(1024))
    external_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    is_template: Mapped[bool] = mapped_column(Boolean, default=False)
    uploaded_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ActivityLog(Base):
    __tablename__ = "activity_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[Optional[int]] = mapped_column(ForeignKey("projects.id"), nullable=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(128))
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ProjectHealthSnapshot(Base):
    __tablename__ = "project_health_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    as_of_date: Mapped[date] = mapped_column(Date)
    planned_progress_pct: Mapped[float] = mapped_column(Float)
    actual_progress_pct: Mapped[float] = mapped_column(Float)
    spi: Mapped[float] = mapped_column(Float)
    rag_schedule: Mapped[str] = mapped_column(String(16))
    rag_gap: Mapped[str] = mapped_column(String(16))
    rag_overall: Mapped[str] = mapped_column(String(16))
    baseline_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    weekly_report_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("weekly_reports.id"), nullable=True
    )


class IntegrationSettings(Base):
    __tablename__ = "integration_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    clickup_api_token_enc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    clickup_team_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    clickup_default_space_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    clickup_default_folder_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    clickup_naming_pattern: Mapped[str] = mapped_column(
        String(255), default="{project_code} - {client_name}"
    )
    clickup_offer_on_kickoff: Mapped[bool] = mapped_column(Boolean, default=True)
    clickup_configured_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    google_drive_service_account_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    google_drive_service_account_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    google_drive_configured_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    clickup_status_mappings: Mapped[list] = mapped_column(JSONB, default=list)
    holiday_dates: Mapped[list] = mapped_column(JSONB, default=list)
    work_weekdays: Mapped[list] = mapped_column(JSONB, default=lambda: [0, 1, 2, 3, 4])


class ClickUpStructureTemplate(Base):
    __tablename__ = "clickup_structure_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    definition: Mapped[dict] = mapped_column(JSONB, default=dict)


class ClickUpTaskCache(Base):
    __tablename__ = "clickup_task_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    clickup_task_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(128), default="")
    assignees: Mapped[dict] = mapped_column(JSONB, default=list)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    priority: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    time_spent_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    time_estimate_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    percent_complete: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_task_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    clickup_list_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    milestone_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    raw_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "clickup_task_id", name="uq_clickup_task"),
    )


class ProjectSph(Base):
    __tablename__ = "project_sph"

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    sph_no: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    sph_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    sph_client: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    sales_pic: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    estimated_start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    target_delivery_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    scope_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    non_scope_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scope_items: Mapped[list] = mapped_column(JSONB, default=list)
    non_scope_items: Mapped[list] = mapped_column(JSONB, default=list)
    delivery_items: Mapped[list] = mapped_column(JSONB, default=list)
    sph_total_rupiah: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    payment_terms: Mapped[list] = mapped_column(JSONB, default=list)
    delivery_method: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    pic_user_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    pic_user_contact: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    planned_md: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    draft_baseline_generated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    timeline_template_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("timeline_templates.id"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ProjectPo(Base):
    __tablename__ = "project_po"

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    po_no: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    po_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    buyer_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    contract_number: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    quotation_reference: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    po_due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    po_payment_terms: Mapped[list] = mapped_column(JSONB, default=list)
    service_items: Mapped[list] = mapped_column(JSONB, default=list)
    po_sub_total: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PreKickoffPack(Base):
    __tablename__ = "pre_kickoff_packs"

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    background: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scope: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    non_scope: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    timeline_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    org_structure: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    deliverables: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    deliverables_items: Mapped[list] = mapped_column(JSONB, default=list)
    org_vendor: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    org_client: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    next_activities: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    deck_generated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ChangeRequestStatus(str, enum.Enum):
    draft = "draft"
    submitted = "submitted"
    approved = "approved"
    rejected = "rejected"
    implemented = "implemented"


class ProjectChangeRequest(Base):
    __tablename__ = "project_change_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    cr_no: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(255))
    background: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scope_change: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    schedule_impact_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cost_impact_rupiah: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    priority: Mapped[str] = mapped_column(String(16), default="medium")
    status: Mapped[ChangeRequestStatus] = mapped_column(
        Enum(ChangeRequestStatus), default=ChangeRequestStatus.draft
    )
    requested_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    decided_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    decision_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    implemented_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RebaselineRequest(Base):
    __tablename__ = "rebaseline_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    reason: Mapped[str] = mapped_column(Text)
    proposed_changes: Mapped[dict] = mapped_column(JSONB, default=dict)
    client_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    client_ack_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    client_ack_document_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("documents.id"), nullable=True
    )
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus), default=ApprovalStatus.pending
    )
    requested_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    decided_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resulting_baseline_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ResourceRate(Base):
    __tablename__ = "resource_rates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[Optional[int]] = mapped_column(ForeignKey("projects.id"), nullable=True)
    role_or_email: Mapped[str] = mapped_column(String(255))
    md_rate: Mapped[float] = mapped_column(Float, default=0.0)


class ProjectEvaluation(Base):
    __tablename__ = "project_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    sph_planned_md: Mapped[float] = mapped_column(Float, default=0.0)
    actual_md: Mapped[float] = mapped_column(Float, default=0.0)
    variance_md: Mapped[float] = mapped_column(Float, default=0.0)
    variance_cost: Mapped[float] = mapped_column(Float, default=0.0)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
