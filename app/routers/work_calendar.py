from fastapi import APIRouter, Depends, File, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import PermissionChecker
from app.database import get_db
from app.models import IntegrationSettings

router = APIRouter(prefix="/integrations/work-calendar", tags=["work-calendar"])


class HolidayEntry(BaseModel):
    date: str
    label: str = ""


class WorkCalendarOut(BaseModel):
    work_weekdays: list[int]
    holiday_dates: list[HolidayEntry]


class WorkCalendarPatch(BaseModel):
    work_weekdays: list[int] | None = Field(default=None, min_length=1, max_length=7)
    holiday_dates: list[HolidayEntry] | None = None


def _normalize_holidays(raw: list) -> list[dict]:
    out: list[dict] = []
    for x in raw or []:
        if isinstance(x, str) and x.strip():
            out.append({"date": x.strip()[:10], "label": ""})
        elif isinstance(x, dict) and x.get("date"):
            out.append({"date": str(x["date"])[:10], "label": str(x.get("label") or "")})
    return out


def _row(db: Session) -> IntegrationSettings:
    row = db.scalar(select(IntegrationSettings).limit(1))
    if not row:
        row = IntegrationSettings(work_weekdays=[0, 1, 2, 3, 4], holiday_dates=[])
        db.add(row)
        db.flush()
    if not row.work_weekdays:
        row.work_weekdays = [0, 1, 2, 3, 4]
    if row.holiday_dates is None:
        row.holiday_dates = []
    return row


@router.get("")
def get_work_calendar(
    db: Session = Depends(get_db),
    _: set[str] = Depends(
        PermissionChecker("health.config.write", "projects.read.all", "projects.read.own")
    ),
):
    row = _row(db)
    db.commit()
    return WorkCalendarOut(
        work_weekdays=list(row.work_weekdays or [0, 1, 2, 3, 4]),
        holiday_dates=_normalize_holidays(list(row.holiday_dates or [])),
    )


@router.patch("")
def patch_work_calendar(
    body: WorkCalendarPatch,
    db: Session = Depends(get_db),
    _: set[str] = Depends(PermissionChecker("health.config.write", "projects.write")),
):
    row = _row(db)
    if body.work_weekdays is not None:
        row.work_weekdays = sorted(set(int(x) for x in body.work_weekdays if 0 <= int(x) <= 6))
    if body.holiday_dates is not None:
        row.holiday_dates = _normalize_holidays(
            [{"date": h.date, "label": h.label} for h in body.holiday_dates if h.date.strip()]
        )
    db.commit()
    return {"updated": True}


@router.post("/import-holidays")
def import_holidays(
    file: UploadFile = File(...),
    mode: str = Query("merge"),
    db: Session = Depends(get_db),
    _: set[str] = Depends(PermissionChecker("health.config.write", "projects.write")),
):
    from app.services.holiday_import import parse_holidays_xlsx

    content = file.file.read()
    parsed = parse_holidays_xlsx(content)
    if not parsed:
        return {"imported": 0, "skipped": 0, "total": 0}
    row = _row(db)
    existing = _normalize_holidays(list(row.holiday_dates or []))
    by_date = {e["date"]: e for e in existing}
    imported = 0
    for p in parsed:
        if p["date"] not in by_date:
            imported += 1
        by_date[p["date"]] = p
    if mode == "replace":
        by_date = {p["date"]: p for p in parsed}
        imported = len(by_date)
    row.holiday_dates = sorted(by_date.values(), key=lambda x: x["date"])
    db.commit()
    return {"imported": imported, "skipped": len(parsed) - imported, "total": len(row.holiday_dates)}
