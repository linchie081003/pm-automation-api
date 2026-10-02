"""Seed projects with yellow/red RAG and visible S-curve planned %.

Run from backend dir: python -m scripts.seed_rag_scurve
"""
from datetime import date, timedelta

from sqlalchemy import select

from app.database import SessionLocal, Base, engine
from app.models import (
    Milestone,
    MilestoneStatus,
    ProgressSnapshotSource,
    Project,
    ProjectHealthConfig,
    ProjectMember,
    ProjectMemberRole,
    User,
)
from app.services.bootstrap import seed_rbac
from app.services.health import compute_health
from app.services.schedule import create_initial_baseline, monday_of, save_weekly_progress


def _ensure_project(db, user, code: str, name: str, client: str) -> Project:
    p = db.query(Project).filter(Project.code == code).first()
    if p:
        return p
    p = Project(code=code, name=name, client_name=client, owner_id=user.id, contract_value=1_500_000_000)
    db.add(p)
    db.flush()
    db.add(ProjectHealthConfig(project_id=p.id))
    db.add(
        ProjectMember(
            project_id=p.id,
            user_id=user.id,
            member_role=ProjectMemberRole.owner,
        )
    )
    return p


def _milestones(db, project_id: int, today: date) -> None:
    if db.scalars(select(Milestone).where(Milestone.project_id == project_id)).first():
        return
    ms = [
        ("Analisis & desain", today + timedelta(days=21), 25),
        ("Implementasi", today + timedelta(days=56), 45),
        ("UAT & go-live", today + timedelta(days=84), 30),
    ]
    for name, td, w in ms:
        db.add(
            Milestone(
                project_id=project_id,
                name=name,
                start_date=today,
                target_date=td,
                weight_pct=w,
                status=MilestoneStatus.open,
            )
        )
    db.flush()


def main():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_rbac(db)
        user = db.scalar(select(User).where(User.email == "pm@example.com"))
        if not user:
            user = db.scalar(select(User).where(User.email == "admin@example.com"))
        if not user:
            print("No user; start app once to seed users.")
            return

        from app.core.timezone import today_jakarta

        today = today_jakarta()
        week0 = monday_of(today - timedelta(days=28))

        # Yellow RAG: planned ahead of actual (one milestone done, plan already higher)
        yellow = _ensure_project(db, user, "RAG-YEL-2026", "Integrasi ERP (lagging)", "Telkom")
        _milestones(db, yellow.id, today - timedelta(days=50))
        yms = list(db.scalars(select(Milestone).where(Milestone.project_id == yellow.id)).all())
        if yms:
            yms[0].status = MilestoneStatus.done
            yms[0].actual_date = today - timedelta(days=35)
            if len(yms) > 1:
                yms[1].target_date = today - timedelta(days=7)
        create_initial_baseline(db, yellow.id, week0, user.id, "Seed yellow RAG")
        for i in range(5):
            ws = week0 + timedelta(days=7 * i)
            save_weekly_progress(db, yellow.id, ws, ProgressSnapshotSource.manual_save)
        h = compute_health(db, yellow.id, today)
        print(f"RAG-YEL-2026 rag={h.get('rag_overall')} planned={h.get('planned_progress_pct')} actual={h.get('actual_progress_pct')}")

        # Red RAG: plan well ahead, zero actual
        red = _ensure_project(db, user, "RAG-RED-2026", "Data Center rollout (critical)", "Bank ABC")
        _milestones(db, red.id, today - timedelta(days=70))
        rms = list(db.scalars(select(Milestone).where(Milestone.project_id == red.id)).all())
        for m in rms[:2]:
            m.target_date = today - timedelta(days=14)
        create_initial_baseline(db, red.id, week0, user.id, "Seed red RAG")
        for i in range(6):
            ws = week0 + timedelta(days=7 * i)
            save_weekly_progress(db, red.id, ws, ProgressSnapshotSource.manual_save)
        h2 = compute_health(db, red.id, today)
        print(f"RAG-RED-2026 rag={h2.get('rag_overall')} planned={h2.get('planned_progress_pct')} actual={h2.get('actual_progress_pct')}")

        # S-curve showcase: BOD-2026 or dedicated
        sc = _ensure_project(db, user, "SCURVE-DEMO", "S-Curve demo project", "Demo Client")
        _milestones(db, sc.id, today - timedelta(days=60))
        create_initial_baseline(db, sc.id, monday_of(today - timedelta(days=90)), user.id, "S-curve demo")
        for i in range(14):
            ws = monday_of(today - timedelta(days=90)) + timedelta(days=7 * i)
            if ws <= monday_of(today):
                save_weekly_progress(db, sc.id, ws, ProgressSnapshotSource.manual_save)

        db.commit()
        print("Seeded RAG-YEL-2026, RAG-RED-2026, SCURVE-DEMO")
    finally:
        db.close()


if __name__ == "__main__":
    main()
