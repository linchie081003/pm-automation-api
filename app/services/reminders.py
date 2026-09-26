from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ClickUpTaskCache, Milestone, Project, ProjectSph


def project_reminders(db: Session, project: Project, horizon_days: int = 14) -> list[dict]:
    today = date.today()
    horizon = today + timedelta(days=horizon_days)
    items: list[dict] = []

    sph = db.get(ProjectSph, project.id)
    if sph and sph.payment_terms:
        for i, term in enumerate(sph.payment_terms):
            if not isinstance(term, dict):
                continue
            due_s = term.get("due_date")
            if not due_s:
                continue
            try:
                due = date.fromisoformat(str(due_s)[:10])
            except ValueError:
                continue
            if today <= due <= horizon:
                items.append(
                    {
                        "type": "payment_term",
                        "severity": "warning" if due <= today + timedelta(days=7) else "info",
                        "title": term.get("label") or f"Termin {i + 1}",
                        "due_date": due.isoformat(),
                        "message": f"Termin pembayaran mendekati: {due}",
                    }
                )

    milestones = db.scalars(select(Milestone).where(Milestone.project_id == project.id)).all()
    for m in milestones:
        if m.target_date and today <= m.target_date <= horizon:
            items.append(
                {
                    "type": "milestone_due",
                    "severity": "warning",
                    "title": m.name,
                    "due_date": m.target_date.isoformat(),
                    "message": f"Milestone due: {m.name} ({m.target_date})",
                }
            )

    if project.clickup_enabled:
        tasks = db.scalars(
            select(ClickUpTaskCache).where(ClickUpTaskCache.project_id == project.id)
        ).all()
        for t in tasks:
            if t.is_closed:
                continue
            if t.due_date and t.due_date < today:
                items.append(
                    {
                        "type": "task_overdue",
                        "severity": "critical",
                        "title": t.name,
                        "due_date": t.due_date.isoformat(),
                        "message": f"Task terlambat: {t.name}",
                    }
                )
            elif t.due_date and today <= t.due_date <= horizon:
                items.append(
                    {
                        "type": "task_due_soon",
                        "severity": "info",
                        "title": t.name,
                        "due_date": t.due_date.isoformat(),
                        "message": f"Task due soon: {t.name}",
                    }
                )

    return items
