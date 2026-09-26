"""Drop all PDC tables and recreate schema + RBAC seed.

Run from backend folder:
  python -m scripts.reset_db
"""

import app.models  # noqa: F401 — register metadata
from app.database import Base, SessionLocal, engine
from app.services.bootstrap import backfill_project_members, ensure_org_defaults, seed_rbac


def main() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_rbac(db)
        ensure_org_defaults(db)
        backfill_project_members(db)
        db.commit()
    finally:
        db.close()
    print("Database reset complete (tables recreated, RBAC seeded).")


if __name__ == "__main__":
    main()
