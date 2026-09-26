from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ClickUpTaskCache, Project, ProjectEvaluation, ProjectSph, ResourceRate

MS_PER_MD = 8 * 60 * 60 * 1000


def compute_evaluation(db: Session, project_id: int) -> ProjectEvaluation:
    sph = db.get(ProjectSph, project_id)
    sph_md = (sph.planned_md if sph and sph.planned_md else 0.0) or 0.0

    tasks = db.scalars(
        select(ClickUpTaskCache).where(ClickUpTaskCache.project_id == project_id)
    ).all()
    spent_ms = sum(t.time_spent_ms or 0 for t in tasks)
    actual_md = round(spent_ms / MS_PER_MD, 2) if spent_ms else 0.0
    variance_md = round(actual_md - sph_md, 2)

    rates = db.scalars(
        select(ResourceRate).where(
            (ResourceRate.project_id == project_id) | (ResourceRate.project_id.is_(None))
        )
    ).all()
    default_rate = rates[0].md_rate if rates else 0.0
    variance_cost = round(variance_md * default_rate, 2)

    ev = ProjectEvaluation(
        project_id=project_id,
        sph_planned_md=sph_md,
        actual_md=actual_md,
        variance_md=variance_md,
        variance_cost=variance_cost,
        computed_at=datetime.utcnow(),
    )
    db.add(ev)
    db.flush()
    return ev
