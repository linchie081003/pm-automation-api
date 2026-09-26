"""Run: python -m scripts.seed_demo (from backend dir, DATABASE_URL set)."""
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
from app.services.schedule import create_initial_baseline, monday_of, save_weekly_progress


def main():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_rbac(db)
        user = db.scalar(select(User).where(User.email == "pm@example.com"))
        if not user:
            user = db.scalar(select(User).where(User.email == "admin@example.com"))
        if not user:
            print("No seed user found; run app startup seed first.")
            return

        project = db.query(Project).filter(Project.code == "BOD-2026").first()
        if not project:
            project = Project(
                code="BOD-2026",
                name="Bandwidth on Demand",
                client_name="Ultima",
                owner_id=user.id,
            )
            db.add(project)
            db.flush()
            db.add(ProjectHealthConfig(project_id=project.id))
            db.add(
                ProjectMember(
                    project_id=project.id,
                    user_id=user.id,
                    member_role=ProjectMemberRole.owner,
                )
            )

            today = date.today()
            ms = [
                ("Requirement freeze", today + timedelta(days=14), 20),
                ("Development", today + timedelta(days=45), 40),
                ("UAT", today + timedelta(days=60), 25),
                ("Go-live", today + timedelta(days=75), 15),
            ]
            for name, td, w in ms:
                db.add(
                    Milestone(
                        project_id=project.id,
                        name=name,
                        target_date=td,
                        weight_pct=w,
                        status=MilestoneStatus.open,
                    )
                )
            db.flush()
            create_initial_baseline(
                db,
                project.id,
                monday_of(today),
                user.id,
                "Baseline at seed",
            )

            week0 = monday_of(today - timedelta(days=7))
            save_weekly_progress(
                db, project.id, week0, ProgressSnapshotSource.manual_save
            )
            db.commit()
            print("Seeded project BOD-2026 (login: pm@example.com / pm12345)")
        else:
            print("Demo data already exists.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
