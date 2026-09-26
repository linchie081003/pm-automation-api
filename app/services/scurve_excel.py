"""Planned S-curve series (aligned with milestone weights; Excel template audit optional)."""
from datetime import date
from pathlib import Path

from app.models import Milestone
from app.services.schedule import planned_pct_as_of
from app.services.templates.loader import copy_template
from app.services.templates.placeholders import build_mapping, replace_in_xlsx


def build_planned_series(
    milestones: list[Milestone],
    anchor_dates: list[date],
    cutoff_offset_days: int,
    db=None,
) -> list[dict]:
    from app.services.report_calendar import period_for_anchor

    if not milestones or not anchor_dates:
        return []
    out: list[dict] = []
    for anchor in anchor_dates:
        _, cut_off = period_for_anchor(anchor, cutoff_offset_days)
        planned = planned_pct_as_of(milestones, cut_off, db=db)
        out.append({"date": anchor.isoformat(), "planned_pct": planned, "cut_off": cut_off.isoformat()})
    return out


def export_scurve_workbook(
    dest: Path,
    *,
    project_code: str,
    project_name: str,
    series: list[dict],
    db=None,
    project=None,
    milestones=None,
) -> Path:
    from openpyxl import Workbook

    if db is not None and project is not None and milestones is not None:
        try:
            from app.services.yyyymmdd_report_excel import export_scurve_from_project

            return export_scurve_from_project(dest, db, project, milestones, series)
        except FileNotFoundError:
            pass

    try:
        copy_template("yyyymmdd-Template.xlsx", dest)
        last = series[-1] if series else {}
        mapping = build_mapping(
            PROJECT_CODE=project_code,
            PROJECT_NAME=project_name,
            PLANNED_PCT=str(last.get("planned_pct", "")),
            SCURVE_POINTS=str(len(series)),
        )
        replace_in_xlsx(dest, mapping)
    except FileNotFoundError:
        wb = Workbook()
        ws = wb.active
        ws.title = "S-Curve"
        ws.append(["Project", project_name, project_code])
        ws.append(["Anchor", "Cut-off", "Planned %"])
        for row in series:
            ws.append([row.get("date"), row.get("cut_off"), row.get("planned_pct")])
        wb.save(dest)
    return dest
