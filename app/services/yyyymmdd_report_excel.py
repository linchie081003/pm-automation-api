"""Fill yyyymmdd-Template.xlsx (S-curve + milestone bar chart sheets) from PDC data."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook
from sqlalchemy.orm import Session

from app.models import Milestone, MilestoneStatus, Project, ProjectPo, ProjectSph
from app.services.report_calendar import anchor_dates_between, period_for_anchor
from app.services.schedule import compute_spi
from app.services.schedule_window import schedule_bounds
from app.services.templates.loader import copy_template


def _as_date(d: date | datetime | None) -> datetime | None:
    if d is None:
        return None
    if isinstance(d, datetime):
        return d.replace(hour=0, minute=0, second=0, microsecond=0)
    return datetime(d.year, d.month, d.day)


def _milestone_actual_fraction(m: Milestone) -> float:
    if m.status == MilestoneStatus.done:
        return 1.0
    return 0.0


def _sorted_milestones(milestones: list[Milestone]) -> list[Milestone]:
    return sorted(milestones, key=lambda m: (m.sort_order or 0, m.id or 0))


def export_yyyymmdd_workbook(
    dest: Path,
    *,
    db: Session,
    project: Project,
    milestones: list[Milestone],
    anchor: date,
    cut_off: date,
    planned_pct: float,
    actual_pct: float,
    spi: float | None = None,
) -> Path:
    copy_template("yyyymmdd-Template.xlsx", dest)
    wb = load_workbook(dest)
    ms = _sorted_milestones([m for m in milestones if m.target_date or m.start_date])
    if not ms:
        ms = _sorted_milestones(milestones)

    sph = db.get(ProjectSph, project.id)
    po = db.get(ProjectPo, project.id)
    sch_start, sch_end = schedule_bounds(db, project.id)
    anchors = []
    if sch_start and sch_end:
        anchors = anchor_dates_between(
            sch_start,
            sch_end,
            project.weekly_report_anchor_weekday,
            cap_at=sch_end,
        )
    if not anchors:
        anchors = [anchor]

    spi_val = spi
    if spi_val is None and planned_pct > 0:
        spi_val = compute_spi(actual_pct, planned_pct)
    deviation_pp = actual_pct - planned_pct

    _fill_scurve_sheet(
        wb["SCurve"],
        project=project,
        sph=sph,
        po=po,
        ms=ms,
        anchor=anchor,
        cut_off=cut_off,
        anchors=anchors,
        planned_pct=planned_pct,
        actual_pct=actual_pct,
    )
    _fill_log_mingguan(
        wb["Log_Mingguan"] if "Log_Mingguan" in wb.sheetnames else None,
        cut_off=cut_off,
        planned_pct=planned_pct,
        actual_pct=actual_pct,
        deviation_pp=deviation_pp,
        spi=spi_val,
    )
    _fill_milestone_progress_sheet(
        wb["milestone progress"] if "milestone progress" in wb.sheetnames else None,
        ms=ms,
        scurve_data_start_row=21,
    )
    wb.save(dest)
    wb.close()
    return dest


def _fill_scurve_sheet(
    ws,
    *,
    project: Project,
    sph: ProjectSph | None,
    po: ProjectPo | None,
    ms: list[Milestone],
    anchor: date,
    cut_off: date,
    anchors: list[date],
    planned_pct: float,
    actual_pct: float,
) -> None:
    ws["C2"] = project.name
    ws["C3"] = project.client_name or (sph.sph_client if sph else "")
    ws["C4"] = (sph.pic_user_name if sph and sph.pic_user_name else "") or ""
    ws["C5"] = _as_date(project.planned_end_date)
    ws["C6"] = _as_date(po.po_due_date if po and po.po_due_date else project.po_due_date)
    ws["C7"] = _as_date(anchor)
    ws["C8"] = _as_date(cut_off)

    first_col = 16  # P
    for i, anc in enumerate(anchors[:24]):
        col = first_col + i
        period_start, period_end = period_for_anchor(anc, 0)
        ws.cell(16, col).value = _as_date(period_start)
        ws.cell(17, col).value = _as_date(period_end)
        if i == 0:
            ws.cell(18, col).value = f"Week-{i + 1:02d}"

    data_start = 21
    max_row = 60
    for r in range(data_start, max_row + 1):
        for col in (2, 3, 4, 5, 11):  # B,C,D,E,K
            ws.cell(r, col).value = None

    last_row = data_start - 1
    for idx, m in enumerate(ms[: max_row - data_start + 1]):
        r = data_start + idx
        last_row = r
        ws.cell(r, 2).value = m.name
        ws.cell(r, 3).value = round(float(m.weight_pct or 0) / 100.0, 6)
        ws.cell(r, 4).value = _as_date(m.start_date)
        ws.cell(r, 5).value = _as_date(m.target_date)
        ws.cell(r, 11).value = _milestone_actual_fraction(m)

    if last_row >= data_start:
        ws.cell(62, 3).value = f"=SUM(C{data_start}:C{last_row})"
        ws.cell(62, 10).value = f"=SUM(J{data_start}:J{last_row})"
        ws.cell(62, 12).value = f"=SUM(L{data_start}:L{last_row})"
        ws.cell(62, 13).value = f"=SUM(M{data_start}:M{last_row})"

def _fill_log_mingguan(
    ws,
    *,
    cut_off: date,
    planned_pct: float,
    actual_pct: float,
    deviation_pp: float,
    spi: float | None,
) -> None:
    if ws is None:
        return
    ws["A4"] = _as_date(cut_off)
    ws["C4"] = planned_pct / 100.0 if planned_pct > 1 else planned_pct
    ws["D4"] = actual_pct / 100.0 if actual_pct > 1 else actual_pct
    ws["E4"] = deviation_pp / 100.0 if abs(deviation_pp) > 1 else deviation_pp
    ws["F4"] = spi if spi is not None else ""


def _fill_milestone_progress_sheet(
    ws,
    *,
    ms: list[Milestone],
    scurve_data_start_row: int,
) -> None:
    if ws is None:
        return
    max_rows = 15
    for r in range(2, 2 + max_rows):
        for c in range(1, 14):
            ws.cell(r, c).value = None

    for idx, m in enumerate(ms[:max_rows]):
        r = 2 + idx
        sc_row = scurve_data_start_row + idx
        ws.cell(r, 1).value = idx + 1
        ws.cell(r, 2).value = m.name
        ws.cell(r, 3).value = round(float(m.weight_pct or 0) / 100.0, 6)
        ws.cell(r, 4).value = _as_date(m.start_date)
        ws.cell(r, 5).value = _as_date(m.target_date)
        ws.cell(r, 10).value = f"=SCurve!I{sc_row}"
        ws.cell(r, 12).value = f"=SCurve!K{sc_row}"
        ws.cell(r, 13).value = f"=L{r}-J{r}"


def export_scurve_from_project(
    dest: Path,
    db: Session,
    project: Project,
    milestones: list[Milestone],
    series: list[dict],
) -> Path:
    """S-curve export endpoint — same template as weekly report snapshot."""
    last = series[-1] if series else {}
    cut_str = last.get("cut_off")
    cut_off = date.fromisoformat(cut_str) if cut_str else date.today()
    anchor_str = last.get("date")
    anchor = date.fromisoformat(anchor_str) if anchor_str else cut_off
    planned = float(last.get("planned_pct") or 0)
    return export_yyyymmdd_workbook(
        dest,
        db=db,
        project=project,
        milestones=milestones,
        anchor=anchor,
        cut_off=cut_off,
        planned_pct=planned,
        actual_pct=0.0,
    )
