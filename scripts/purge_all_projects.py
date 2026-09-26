"""Delete all projects and related data. Keeps users, roles, org settings, timeline templates."""
import sys
from pathlib import Path

backend = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(backend))

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Project
from app.services import project_delete


def main() -> None:
    db = SessionLocal()
    force = "--force" in sys.argv
    try:
        ids = list(db.scalars(select(Project.id)).all())
        if not ids:
            print("No projects to delete.")
            return
        if "--yes" not in sys.argv:
            print(f"Will delete {len(ids)} project(s). Re-run with --yes to confirm.")
            if not force:
                print("Add --force to wipe proyek in delivery/closed too.")
            return

        orig_validate = project_delete.validate_project_deletion
        if force:
            project_delete.validate_project_deletion = lambda _db, _p: None

        deleted = 0
        skipped: list[str] = []
        try:
            for pid in ids:
                p = db.get(Project, pid)
                if not p:
                    continue
                try:
                    project_delete.delete_project(db, p)
                    db.flush()
                    deleted += 1
                except ValueError as e:
                    skipped.append(f"{p.code}: {e}")
        finally:
            project_delete.validate_project_deletion = orig_validate

        db.commit()
        print(f"Deleted {deleted} project(s).")
        for s in skipped:
            print(f"  skipped: {s}")
        if skipped and not force:
            print("Re-run with --yes --force to wipe all.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
