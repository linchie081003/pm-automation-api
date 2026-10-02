from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Project
from app.core.timezone import today_jakarta


def next_project_code(db: Session) -> str:
    year = today_jakarta().year
    prefix = f"PDC-{year}-"
    rows = db.scalars(select(Project.code).where(Project.code.like(f"{prefix}%"))).all()
    max_seq = 0
    for code in rows:
        tail = code[len(prefix) :]
        if tail.isdigit():
            max_seq = max(max_seq, int(tail))
    return f"{prefix}{max_seq + 1:04d}"
