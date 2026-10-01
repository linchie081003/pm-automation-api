"""Duplikasi proyek — hanya fase SPH (po_received)."""
from __future__ import annotations

import copy
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Project,
    ProjectHealthConfig,
    ProjectMember,
    ProjectMemberRole,
    ProjectPhase,
    ProjectPo,
    ProjectSph,
    ScheduleBaseline,
    ScheduleBaselineMilestone,
    User,
)
from app.services.project_code import next_project_code
from app.services.workflow import ensure_phase_row
from app.services.bast import default_bast_checklist


def validate_project_duplication(project: Project) -> None:
    if project.current_phase != ProjectPhase.po_received:
        raise ValueError(
            "Duplikasi hanya untuk proyek yang masih di fase SPH — belum lanjut ke Kick Off"
        )


def _copy_sph_payment_terms(terms: list, id_map: dict[int, int]) -> list:
    out = []
    for raw in terms or []:
        if not isinstance(raw, dict):
            continue
        t = copy.deepcopy(raw)
        old_id = t.get("draft_milestone_id")
        if old_id is not None:
            try:
                mapped = id_map.get(int(old_id))
                if mapped is not None:
                    t["draft_milestone_id"] = mapped
                else:
                    t.pop("draft_milestone_id", None)
            except (TypeError, ValueError):
                t.pop("draft_milestone_id", None)
        out.append(t)
    return out


def duplicate_project(db: Session, source: Project, user: User) -> Project:
    validate_project_duplication(source)
    src_sph = db.get(ProjectSph, source.id)
    src_po = db.get(ProjectPo, source.id)

    suffix = " (salinan)"
    base_name = (source.name or "").strip() or "Proyek"
    new_name = base_name if base_name.endswith(suffix.strip()) else f"{base_name}{suffix}"

    p = Project(
        code=next_project_code(db),
        name=new_name,
        client_name=source.client_name or "",
        project_manager=source.project_manager,
        project_brief=source.project_brief,
        contract_value=source.contract_value,
        po_date=source.po_date,
        po_due_date=source.po_due_date,
        document_repo_url=source.document_repo_url,
        owner_id=user.id,
        current_phase=ProjectPhase.po_received,
        methodology=source.methodology,
        weekly_report_anchor_weekday=source.weekly_report_anchor_weekday,
        weekly_report_cutoff_offset_days=source.weekly_report_cutoff_offset_days,
        weekly_report_first_anchor_date=source.weekly_report_first_anchor_date,
        bast_checklist=copy.deepcopy(source.bast_checklist)
        if source.bast_checklist
        else default_bast_checklist(),
    )
    db.add(p)
    db.flush()

    db.add(ProjectHealthConfig(project_id=p.id))
    db.add(
        ProjectMember(
            project_id=p.id,
            user_id=user.id,
            member_role=ProjectMemberRole.owner,
            assigned_by_id=user.id,
        )
    )
    ensure_phase_row(db, p.id, ProjectPhase.po_received)

    if src_po:
        db.add(
            ProjectPo(
                project_id=p.id,
                po_no=src_po.po_no,
                po_name=src_po.po_name,
                buyer_name=src_po.buyer_name,
                contract_number=src_po.contract_number,
                quotation_reference=src_po.quotation_reference,
                po_due_date=src_po.po_due_date,
                po_payment_terms=copy.deepcopy(src_po.po_payment_terms or []),
                service_items=copy.deepcopy(src_po.service_items or []),
                po_sub_total=src_po.po_sub_total,
            )
        )

    draft_row_id_map: dict[int, int] = {}
    if src_sph:
        new_sph = ProjectSph(
            project_id=p.id,
            sph_name=new_name,
            sph_client=src_sph.sph_client,
            sph_no=None,
            sales_pic=src_sph.sales_pic,
            estimated_start_date=src_sph.estimated_start_date,
            target_delivery_days=src_sph.target_delivery_days,
            scope_text=src_sph.scope_text,
            non_scope_text=src_sph.non_scope_text,
            scope_items=copy.deepcopy(src_sph.scope_items or []),
            non_scope_items=copy.deepcopy(src_sph.non_scope_items or []),
            delivery_items=copy.deepcopy(src_sph.delivery_items or []),
            sph_total_rupiah=src_sph.sph_total_rupiah,
            payment_terms=[],
            delivery_method=src_sph.delivery_method,
            pic_user_name=src_sph.pic_user_name or source.project_manager,
            pic_user_contact=src_sph.pic_user_contact,
            planned_md=src_sph.planned_md,
            draft_baseline_generated_at=src_sph.draft_baseline_generated_at,
            timeline_template_id=src_sph.timeline_template_id,
        )
        db.add(new_sph)
        db.flush()

        draft = db.scalar(
            select(ScheduleBaseline).where(
                ScheduleBaseline.project_id == source.id,
                ScheduleBaseline.is_draft.is_(True),
            )
        )
        if draft:
            new_baseline = ScheduleBaseline(
                project_id=p.id,
                version=1,
                effective_from=draft.effective_from,
                reason="Duplikat dari proyek SPH",
                is_current=True,
                is_draft=True,
                created_by_id=user.id,
            )
            db.add(new_baseline)
            db.flush()

            src_rows = list(
                db.scalars(
                    select(ScheduleBaselineMilestone)
                    .where(ScheduleBaselineMilestone.baseline_id == draft.id)
                    .order_by(ScheduleBaselineMilestone.sort_order, ScheduleBaselineMilestone.id)
                ).all()
            )
            old_to_new_parent: dict[int, int] = {}
            for row in src_rows:
                nr = ScheduleBaselineMilestone(
                    baseline_id=new_baseline.id,
                    milestone_id=None,
                    row_key=row.row_key,
                    name=row.name,
                    start_date=row.start_date,
                    target_date=row.target_date,
                    duration_days=row.duration_days,
                    weight_pct=row.weight_pct,
                    is_payment_milestone=row.is_payment_milestone,
                    item_type=row.item_type,
                    parent_id=None,
                    sort_order=row.sort_order,
                    predecessor_ref=row.predecessor_ref,
                    predecessor_link_type=row.predecessor_link_type,
                )
                db.add(nr)
                db.flush()
                draft_row_id_map[row.id] = nr.id
                old_to_new_parent[row.id] = nr.id

            for row in src_rows:
                if row.parent_id and row.id in old_to_new_parent:
                    new_id = old_to_new_parent[row.id]
                    new_parent = old_to_new_parent.get(row.parent_id)
                    if new_parent:
                        child = db.get(ScheduleBaselineMilestone, new_id)
                        if child:
                            child.parent_id = new_parent

            new_sph.payment_terms = _copy_sph_payment_terms(
                src_sph.payment_terms or [], draft_row_id_map
            )
        else:
            new_sph.payment_terms = copy.deepcopy(src_sph.payment_terms or [])
    else:
        db.add(
            ProjectSph(
                project_id=p.id,
                sph_name=new_name,
                sph_client=source.client_name or None,
                pic_user_name=source.project_manager,
            )
        )

    db.flush()
    return p
