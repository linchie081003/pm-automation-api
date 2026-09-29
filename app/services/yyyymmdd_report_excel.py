"""Fill yyyymmdd-Template.xlsx (S-curve + milestone bar chart sheets) from PDC data."""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    IntegrationSettings,
    Milestone,
    MilestoneStatus,
    ProgressSnapshot,
    ProgressSnapshotSource,
    Project,
    ProjectHealthConfig,
    ProjectPo,
    ProjectSph,
    TimelineItemType,
    WeeklyReport,
)
from app.services.report_calendar import (
    extend_anchors_for_project_end,
    period_for_report_date,
    weekly_report_dates,
)
from app.services.schedule import compute_spi, snapshot_key_for_report_date
from app.services.schedule_window import (
    kickoff_milestones,
    project_report_end_date,
    project_report_start_date,
    schedule_bounds,
)
from app.services.templates.loader import copy_template
from app.services.timeline_display import timeline_display_rows_for_project

_SCURVE_ROMAN = (
    "I",
    "II",
    "III",
    "IV",
    "V",
    "VI",
    "VII",
    "VIII",
    "IX",
    "X",
    "XI",
    "XII",
)

_SHEETS_TO_DROP = ("Catatan Perbaikan", "Info Mingguan", "SCurve D1")

_PHASE_FONT = Font(bold=True, size=13)
_TASK_FONT = Font(size=11)

_SCURVE_FIRST_WEEK_COL = 16  # P
_SCURVE_LAST_WEEK_COL = 63  # BK
_SCURVE_TYPE_COL = 14  # N — label tipe (phase / milestone / task / subtask)
_TASK_FORMULA_TEMPLATE_ROW = 21
_LOG_MINGGUAN_FIRST_WEEK_ROW = 8
_LOG_MINGGUAN_LAST_WEEK_ROW = 55
_SCURVE_CLEAR_B74_E112 = (74, 112, 2, 5)  # rows, cols B–E


def _as_date(d: date | datetime | None) -> datetime | None:
    if d is None:
        return None
    if isinstance(d, datetime):
        return d.replace(hour=0, minute=0, second=0, microsecond=0)
    return datetime(d.year, d.month, d.day)


def _parse_row_date(val: object) -> date | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    try:
        return date.fromisoformat(str(val)[:10])
    except ValueError:
        return None


def _milestone_actual_fraction(m: Milestone) -> float:
    if m.status == MilestoneStatus.done:
        return 1.0
    return 0.0


def _row_actual_fraction(row: dict, ms_by_id: dict[int, Milestone]) -> float:
    pct = row.get("clickup_progress_pct")
    if pct is not None:
        return min(1.0, max(0.0, float(pct) / 100.0))
    rid = row.get("id")
    if isinstance(rid, int) and rid in ms_by_id:
        return _milestone_actual_fraction(ms_by_id[rid])
    return 0.0


def _row_dates(row: dict) -> tuple[date | None, date | None]:
    start = _parse_row_date(row.get("display_start") or row.get("start_date"))
    end = _parse_row_date(row.get("display_end") or row.get("target_date"))
    return start, end


def _row_display_name(row: dict) -> str:
    return (row.get("name") or row.get("clickup_name") or "").strip()


def _excel_timeline_rows(display: list[dict]) -> list[tuple[str | None, dict, bool]]:
    """(Roman numeral, row, is_phase) in timeline order; skips unnamed rows."""
    out: list[tuple[str | None, dict, bool]] = []
    phase_i = 0
    for row in display:
        if not _row_display_name(row):
            continue
        it = row.get("item_type") or ""
        if it == TimelineItemType.phase.value:
            roman = _SCURVE_ROMAN[phase_i] if phase_i < len(_SCURVE_ROMAN) else str(phase_i + 1)
            phase_i += 1
            out.append((roman, row, True))
        else:
            out.append((None, row, False))
    return out


def _row_key(row: dict) -> int | str | None:
    rid = row.get("id")
    if rid is not None:
        return rid
    cu = row.get("clickup_task_id")
    return f"cu:{cu}" if cu else None


def _scurve_type_label(row: dict) -> str:
    it = row.get("item_type") or ""
    if it == TimelineItemType.phase.value:
        return "Phase"
    if it == TimelineItemType.milestone.value:
        return "Milestone"
    if it == TimelineItemType.subtask.value:
        return "Subtask"
    if it == TimelineItemType.task.value:
        return "Task"
    return str(it).capitalize() if it else ""


def _distributed_weight_fractions(rows: list[dict]) -> list[float]:
    """
    Phase: weight_pct.
    Task: bobot phase / jumlah task langsung di bawah phase.
    Subtask: bobot task induk / jumlah subtask langsung.
    Milestone: weight_pct (biasanya 0).
    """
    by_parent: dict[int | str, list[dict]] = defaultdict(list)
    frac: dict[int | str, float] = {}

    for row in rows:
        it = row.get("item_type") or ""
        if it == TimelineItemType.phase.value:
            key = _row_key(row)
            if key is not None:
                frac[key] = float(row.get("weight_pct") or 0) / 100.0
            continue
        pid = row.get("parent_id")
        if pid is None:
            pid = row.get("phase_id")
        if pid is not None:
            by_parent[pid].append(row)

    for row in rows:
        if row.get("item_type") != TimelineItemType.phase.value:
            continue
        phase_id = row.get("id")
        phase_key = _row_key(row)
        if phase_key is None:
            continue
        phase_w = frac.get(phase_key, 0.0)
        tasks = [
            t
            for t in by_parent.get(phase_id, [])
            if t.get("item_type") == TimelineItemType.task.value
        ]
        if not tasks:
            continue
        task_share = phase_w / len(tasks)
        for t in tasks:
            tkey = _row_key(t)
            if tkey is None:
                continue
            frac[tkey] = task_share
            tid = t.get("id")
            subs = [
                s
                for s in by_parent.get(tid, [])
                if s.get("item_type") == TimelineItemType.subtask.value
            ]
            if not subs:
                continue
            sub_share = task_share / len(subs)
            for s in subs:
                skey = _row_key(s)
                if skey is not None:
                    frac[skey] = sub_share

    for row in rows:
        it = row.get("item_type") or ""
        key = _row_key(row)
        if key is None:
            continue
        if it == TimelineItemType.milestone.value:
            frac[key] = float(row.get("weight_pct") or 0) / 100.0
        elif it not in (TimelineItemType.phase.value,) and key not in frac:
            frac[key] = 0.0

    return [frac.get(_row_key(row), 0.0) for row in rows]


def _formula_row(formula: str, template_row: int, target_row: int) -> str:
    return re.sub(rf"(?<![0-9]){template_row}(?![0-9])", str(target_row), formula)


def _snapshot_task_formulas(ws, template_row: int) -> dict[int, str]:
    out: dict[int, str] = {}
    for col in range(6, _SCURVE_LAST_WEEK_COL + 1):
        val = ws.cell(template_row, col).value
        if isinstance(val, str) and val.startswith("="):
            out[col] = val
    return out


def _apply_task_formulas(ws, r: int, templates: dict[int, str], template_row: int) -> None:
    for col, formula in templates.items():
        ws.cell(r, col).value = _formula_row(formula, template_row, r)


def _week_allocation_formula(col: int, row: int) -> str:
    cl = get_column_letter(col)
    return (
        f"=IF(AND($D{row}<={cl}$17,$E{row}>={cl}$16),"
        f"$C{row}*NETWORKDAYS(MAX($D{row},{cl}$16),MIN($E{row},{cl}$17),Hari_Libur)/"
        f"NETWORKDAYS($D{row},$E{row},Hari_Libur),0)"
    )


def _apply_phase_calculation_row(
    ws,
    r: int,
    *,
    task_formulas: dict[int, str],
    template_row: int,
    week_cols: int,
    progress_k: float,
) -> None:
    """Linear target, actual, deviation, and weekly columns — phase rows only."""
    ws.cell(r, 11).value = progress_k
    _apply_task_formulas(ws, r, task_formulas, template_row)
    for col in range(_SCURVE_FIRST_WEEK_COL, _SCURVE_FIRST_WEEK_COL + week_cols):
        ws.cell(r, col).value = _week_allocation_formula(col, r)


def _pct_as_excel_fraction(value: float) -> float:
    if value > 1.0:
        return round(value / 100.0, 6)
    return round(float(value), 6)


def _snapshot_has_saved_actual(snap: ProgressSnapshot) -> bool:
    if snap.weekly_report_id is not None:
        return True
    return snap.source in (
        ProgressSnapshotSource.weekly_report,
        ProgressSnapshotSource.manual_save,
    )


def _weekly_report_actual_map(
    db: Session,
    project: Project,
    project_start: date | None,
) -> dict[date, float]:
    """
    Actual kumulatif per minggu laporan — hanya dari WeeklyReport yang sudah di-generate
    (frozen_metrics), keyed by week_start / normalized report_date.
    """
    reports = db.scalars(
        select(WeeklyReport)
        .where(WeeklyReport.project_id == project.id)
        .order_by(WeeklyReport.week_start)
    ).all()
    out: dict[date, float] = {}
    for wr in reports:
        fm = wr.frozen_metrics or {}
        raw = fm.get("actual_pct")
        if raw is None:
            continue
        frac = _pct_as_excel_fraction(float(raw))
        out[wr.week_start] = frac
        norm, _ = snapshot_key_for_report_date(
            project, wr.week_start, project_start_date=project_start
        )
        if norm not in out:
            out[norm] = frac
    return out


def _snapshot_actual_map_exact(
    db: Session,
    project: Project,
    project_start: date | None,
) -> dict[date, float]:
    """Fallback: snapshot dengan weekly report / manual save, exact week_start only."""
    snaps = db.scalars(
        select(ProgressSnapshot).where(ProgressSnapshot.project_id == project.id)
    ).all()
    out: dict[date, float] = {}
    for s in snaps:
        if not _snapshot_has_saved_actual(s):
            continue
        frac = _pct_as_excel_fraction(s.actual_cumulative_pct)
        out[s.week_start] = frac
        norm, _ = snapshot_key_for_report_date(
            project, s.week_start, project_start_date=project_start
        )
        if norm not in out:
            out[norm] = frac
    return out


def _actual_kumulatif_for_week(
    report_actuals: dict[date, float],
    snap_actuals: dict[date, float],
    project: Project,
    anchor: date,
    project_start: date | None,
) -> float | None:
    norm, _ = snapshot_key_for_report_date(
        project, anchor, project_start_date=project_start
    )
    for key in (norm, anchor):
        if key in report_actuals:
            return report_actuals[key]
    for key in (norm, anchor):
        if key in snap_actuals:
            return snap_actuals[key]
    return None


def _week_index_for_status(
    status_date: date,
    anchors: list[date],
    project_start: date | None,
) -> int:
    if not anchors:
        return 1
    for i, end in enumerate(anchors):
        if i == 0:
            start = project_start or end
        else:
            start = anchors[i - 1] + timedelta(days=1)
        if start <= status_date <= end:
            return i + 1
    if project_start and status_date < project_start:
        return 1
    return len(anchors)


def _clear_scurve_cells_b74_e112(ws) -> None:
    r0, r1, c0, c1 = _SCURVE_CLEAR_B74_E112
    for r in range(r0, r1 + 1):
        for c in range(c0, c1 + 1):
            ws.cell(r, c).value = None


def _remove_sheet_table_styles(ws) -> None:
    if ws is None:
        return
    for name in list(getattr(ws, "tables", {}) or {}):
        del ws.tables[name]
    ws.auto_filter.ref = None
    max_r = max(ws.max_row or 1, 500)
    for row in ws.iter_rows(min_row=1, max_row=max_r, min_col=1, max_col=20):
        for cell in row:
            cell.fill = PatternFill(fill_type=None)
            cell.border = Border()
            cell.style = "Normal"


def _trim_workbook_sheets(wb) -> None:
    for name in _SHEETS_TO_DROP:
        if name in wb.sheetnames:
            del wb[name]
    if "Sheet2" in wb.sheetnames:
        wb["Sheet2"].title = "Task"
    elif "Task" not in wb.sheetnames:
        wb.create_sheet("Task")


def _weekly_report_anchor_list(
    db: Session,
    project: Project,
) -> tuple[list[date], date | None]:
    pstart = project_report_start_date(db, project)
    pend, _ = project_report_end_date(db, project.id)
    if not pend:
        sch_start, sch_end = schedule_bounds(db, project.id)
        pend = sch_end or sch_start
    if not pend:
        return [], pstart
    anchors, _ = weekly_report_dates(
        pstart,
        pend,
        project.weekly_report_anchor_weekday,
        project.weekly_report_first_anchor_date,
        cap_at=pend,
    )
    anchors = extend_anchors_for_project_end(
        anchors,
        pend,
        project.weekly_report_cutoff_offset_days,
    )
    return anchors, pstart


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
    _trim_workbook_sheets(wb)

    display = timeline_display_rows_for_project(db, project.id)
    ms_by_id = {m.id: m for m in kickoff_milestones(db, project.id) or milestones}
    excel_rows = _excel_timeline_rows(display)
    row_payloads = [row for _, row, _ in excel_rows]
    weight_fractions = _distributed_weight_fractions(row_payloads)

    sph = db.get(ProjectSph, project.id)
    po = db.get(ProjectPo, project.id)

    anchors, project_start = _weekly_report_anchor_list(db, project)
    if not anchors:
        anchors = [anchor]
    if not project_start:
        project_start = project_report_start_date(db, project)

    spi_val = spi
    if spi_val is None and planned_pct > 0:
        spi_val = compute_spi(actual_pct, planned_pct)
    deviation_pp = actual_pct - planned_pct

    scurve_data_start = 21
    phase_scurve_rows = _fill_scurve_sheet(
        wb["SCurve"],
        project=project,
        sph=sph,
        po=po,
        excel_rows=excel_rows,
        weight_fractions=weight_fractions,
        ms_by_id=ms_by_id,
        anchor=anchor,
        cut_off=cut_off,
        anchors=anchors,
        project_start=project_start,
        data_start=scurve_data_start,
    )
    health_cfg = db.get(ProjectHealthConfig, project.id)
    _fill_log_mingguan(
        wb["Log_Mingguan"] if "Log_Mingguan" in wb.sheetnames else None,
        db=db,
        project=project,
        cut_off=cut_off,
        planned_pct=planned_pct,
        actual_pct=actual_pct,
        deviation_pp=deviation_pp,
        spi=spi_val,
        spi_green_min=health_cfg.spi_green_min if health_cfg else 1.0,
        spi_yellow_min=health_cfg.spi_yellow_min if health_cfg else 0.9,
        anchors=anchors,
        project_start=project_start,
        week_col_count=min(
            len(anchors),
            _SCURVE_LAST_WEEK_COL - _SCURVE_FIRST_WEEK_COL + 1,
        ),
    )
    _fill_milestone_progress_sheet(
        wb["milestone progress"] if "milestone progress" in wb.sheetnames else None,
        phase_scurve_rows=phase_scurve_rows,
    )
    _fill_libur_sheet(wb["Libur"] if "Libur" in wb.sheetnames else None, db)
    _fill_task_sheet(
        wb["Task"] if "Task" in wb.sheetnames else None,
        excel_rows,
        weight_fractions,
        ms_by_id,
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
    excel_rows: list[tuple[str | None, dict, bool]],
    weight_fractions: list[float],
    ms_by_id: dict[int, Milestone],
    anchor: date,
    cut_off: date,
    anchors: list[date],
    project_start: date | None,
    data_start: int,
) -> list[tuple[str, dict, int]]:
    """Returns phase rows as (roman, row_dict, scurve_excel_row)."""
    task_formulas = _snapshot_task_formulas(ws, _TASK_FORMULA_TEMPLATE_ROW)
    ws.cell(18, _SCURVE_TYPE_COL).value = "Tipe"

    ws["A20"].value = None
    ws["B20"].value = None
    for col in range(3, 14):
        ws.cell(20, col).value = None

    period_len = int(project.weekly_report_cutoff_offset_days or 0)
    ws["C2"] = project.name
    ws["C3"] = project.client_name or (sph.sph_client if sph else "")
    ws["C4"] = (sph.pic_user_name if sph and sph.pic_user_name else "") or ""
    ws["C5"] = _as_date(project.planned_end_date)
    ws["C6"] = _as_date(po.po_due_date if po and po.po_due_date else project.po_due_date)
    ws["C7"] = _as_date(anchor)
    ws["C8"] = _as_date(cut_off)

    week_cols = min(len(anchors), _SCURVE_LAST_WEEK_COL - _SCURVE_FIRST_WEEK_COL + 1)
    for i in range(week_cols):
        col = _SCURVE_FIRST_WEEK_COL + i
        report_date = anchors[i]
        if i == 0:
            row16 = project_start or report_date
        else:
            row16 = anchors[i - 1] + timedelta(days=1)
        _, period_end = period_for_report_date(report_date, period_len, project_start)
        row17 = period_end or report_date
        ws.cell(16, col).value = _as_date(row16)
        ws.cell(17, col).value = _as_date(row17)
        ws.cell(18, col).value = f"Week-{i + 1:02d}"

    max_row = 60
    for r in range(data_start, max_row + 1):
        for col in range(1, _SCURVE_LAST_WEEK_COL + 1):
            ws.cell(r, col).value = None

    phase_row_nums: list[int] = []
    phase_meta: list[tuple[str, dict, int]] = []
    r = data_start
    idx = 0

    def _write_detail_row(cr: int, child_idx: int) -> None:
        """Task / subtask / milestone — tampil saja, tanpa formula mingguan."""
        _, child, _ = excel_rows[child_idx]
        w_child = weight_fractions[child_idx] if child_idx < len(weight_fractions) else 0.0
        cname = _row_display_name(child)
        cstart, cend = _row_dates(child)
        depth = int(child.get("depth") or 0)
        ws.cell(cr, 2).value = f"{'    ' * max(1, depth)}{cname}"
        ws.cell(cr, 2).font = _TASK_FONT
        ws.cell(cr, 2).alignment = Alignment(indent=max(0, depth))
        ws.cell(cr, 3).value = round(w_child, 6)
        ws.cell(cr, 4).value = _as_date(cstart)
        ws.cell(cr, 5).value = _as_date(cend)
        ws.cell(cr, _SCURVE_TYPE_COL).value = _scurve_type_label(child)
        it = child.get("item_type") or ""
        if it != TimelineItemType.milestone.value:
            ws.cell(cr, 11).value = _row_actual_fraction(child, ms_by_id)

    while idx < len(excel_rows) and r <= max_row:
        roman, row, is_phase = excel_rows[idx]
        weight_frac = weight_fractions[idx] if idx < len(weight_fractions) else 0.0
        name = _row_display_name(row)
        start, end = _row_dates(row)

        if is_phase:
            child_indices: list[int] = []
            j = idx + 1
            while j < len(excel_rows) and not excel_rows[j][2]:
                child_indices.append(j)
                j += 1

            phase_r = r
            ws.cell(phase_r, 1).value = roman
            phase_cell = ws.cell(phase_r, 2)
            phase_cell.value = name
            phase_cell.font = _PHASE_FONT
            ws.cell(phase_r, 3).value = round(float(row.get("weight_pct") or 0) / 100.0, 6)
            ws.cell(phase_r, 4).value = _as_date(start)
            ws.cell(phase_r, 5).value = _as_date(end)
            ws.cell(phase_r, _SCURVE_TYPE_COL).value = "Phase"
            _apply_phase_calculation_row(
                ws,
                phase_r,
                task_formulas=task_formulas,
                template_row=_TASK_FORMULA_TEMPLATE_ROW,
                week_cols=week_cols,
                progress_k=_row_actual_fraction(row, ms_by_id),
            )

            first_child_r = phase_r + 1
            for k, child_idx in enumerate(child_indices):
                cr = first_child_r + k
                if cr > max_row:
                    break
                _write_detail_row(cr, child_idx)

            phase_row_nums.append(phase_r)
            phase_meta.append((roman or "", row, phase_r))
            r = first_child_r + len(child_indices)
            idx = j
            continue

        ws.cell(r, 2).value = name
        ws.cell(r, 2).font = _TASK_FONT
        ws.cell(r, 3).value = round(weight_frac, 6)
        ws.cell(r, 4).value = _as_date(start)
        ws.cell(r, 5).value = _as_date(end)
        ws.cell(r, _SCURVE_TYPE_COL).value = _scurve_type_label(row)
        if (row.get("item_type") or "") != TimelineItemType.milestone.value:
            ws.cell(r, 11).value = _row_actual_fraction(row, ms_by_id)
        r += 1
        idx += 1

    if phase_row_nums:
        parts = ",".join(f"C{n}" for n in phase_row_nums)
        ws.cell(62, 3).value = f"=SUM({parts})"
        jparts = ",".join(f"J{n}" for n in phase_row_nums)
        lparts = ",".join(f"L{n}" for n in phase_row_nums)
        mparts = ",".join(f"M{n}" for n in phase_row_nums)
        oparts = ",".join(f"O{n}" for n in phase_row_nums)
        ws.cell(62, 10).value = f"=SUM({jparts})"
        ws.cell(62, 12).value = f"=SUM({lparts})"
        ws.cell(62, 13).value = f"=SUM({mparts})"
        ws.cell(62, 15).value = f"=SUM({oparts})"
        for wi in range(week_cols):
            col = _SCURVE_FIRST_WEEK_COL + wi
            wparts = ",".join(f"{get_column_letter(col)}{n}" for n in phase_row_nums)
            ws.cell(62, col).value = f"=SUM({wparts})"

    _clear_scurve_cells_b74_e112(ws)
    return phase_meta


def _fill_libur_sheet(ws, db: Session) -> None:
    if ws is None:
        return
    settings = db.scalars(select(IntegrationSettings).limit(1)).first()
    holidays = sorted(
        list(settings.holiday_dates or []) if settings else [],
        key=lambda h: str(h.get("date") or ""),
    )
    max_r = max(ws.max_row or 1, 500)
    for r in range(1, max_r + 1):
        for c in range(1, 16):
            ws.cell(r, c).value = None
    ws.cell(1, 1).value = "Tanggal Libur"
    for i, h in enumerate(holidays):
        r = 2 + i
        d = _parse_row_date(h.get("date"))
        ws.cell(r, 1).value = _as_date(d) if d else None


def _fill_task_sheet(
    ws,
    excel_rows: list[tuple[str | None, dict, bool]],
    weight_fractions: list[float],
    ms_by_id: dict[int, Milestone],
) -> None:
    if ws is None:
        return
    headers = (
        "No",
        "Task",
        "Bobot",
        "Start Date",
        "Finish Date",
        "Tipe",
        "Phase",
        "Status",
        "Progress %",
    )
    for c, h in enumerate(headers, start=1):
        ws.cell(1, c).value = h
    for r in range(2, (ws.max_row or 2) + 200):
        for c in range(1, len(headers) + 1):
            ws.cell(r, c).value = None

    for idx, (roman, row, is_phase) in enumerate(excel_rows):
        r = 2 + idx
        name = _row_display_name(row)
        depth = int(row.get("depth") or 0)
        if is_phase:
            task_label = name
        else:
            task_label = f"{'    ' * max(1, depth)}{name}"
        start, end = _row_dates(row)
        pct = row.get("clickup_progress_pct")
        ws.cell(r, 1).value = roman
        ws.cell(r, 2).value = task_label
        w_frac = weight_fractions[idx] if idx < len(weight_fractions) else 0.0
        if is_phase:
            ws.cell(r, 3).value = round(float(row.get("weight_pct") or 0) / 100.0, 6)
        else:
            ws.cell(r, 3).value = round(w_frac, 6)
        ws.cell(r, 4).value = _as_date(start)
        ws.cell(r, 5).value = _as_date(end)
        ws.cell(r, 6).value = _scurve_type_label(row)
        ws.cell(r, 7).value = row.get("phase_name") or ""
        ws.cell(r, 8).value = row.get("clickup_status") or row.get("status") or ""
        ws.cell(r, 9).value = (
            round(float(pct) / 100.0, 4) if pct is not None else _row_actual_fraction(row, ms_by_id)
        )
        if is_phase:
            ws.cell(r, 2).font = _PHASE_FONT
        else:
            ws.cell(r, 2).font = _TASK_FONT

    _remove_sheet_table_styles(ws)


def _fill_log_mingguan(
    ws,
    *,
    db: Session,
    project: Project,
    cut_off: date,
    planned_pct: float,
    actual_pct: float,
    deviation_pp: float,
    spi: float | None,
    spi_green_min: float,
    spi_yellow_min: float,
    anchors: list[date],
    project_start: date | None,
    week_col_count: int,
) -> None:
    if ws is None:
        return
    ws["A4"] = _as_date(cut_off)
    if week_col_count > 0:
        c0 = get_column_letter(_SCURVE_FIRST_WEEK_COL)
        c1 = get_column_letter(_SCURVE_FIRST_WEEK_COL + week_col_count - 1)
        ws["B4"] = (
            f"=IF(SUMPRODUCT((SCurve!$C$8>={c0}$16:{c1}$16)"
            f"*(SCurve!$C$8<={c0}$17:{c1}$17))=0,"
            f"{_week_index_for_status(cut_off, anchors, project_start)},"
            f"SUMPRODUCT((SCurve!$C$8>={c0}$16:{c1}$16)"
            f"*(SCurve!$C$8<={c0}$17:{c1}$17)))"
        )
    else:
        ws["B4"] = _week_index_for_status(cut_off, anchors, project_start)
    ws["C4"] = planned_pct / 100.0 if planned_pct > 1 else planned_pct
    ws["D4"] = actual_pct / 100.0 if actual_pct > 1 else actual_pct
    ws["E4"] = deviation_pp / 100.0 if abs(deviation_pp) > 1 else deviation_pp
    ws["F4"] = spi if spi is not None else ""
    ws["K4"] = spi_green_min
    ws["L4"] = spi_yellow_min

    report_actuals = _weekly_report_actual_map(db, project, project_start)
    snap_actuals = _snapshot_actual_map_exact(db, project, project_start)
    for r in range(_LOG_MINGGUAN_FIRST_WEEK_ROW, _LOG_MINGGUAN_LAST_WEEK_ROW + 1):
        ws.cell(r, 4).value = None
        ws.cell(r, 9).value = None
    for i, anchor_date in enumerate(anchors):
        r = _LOG_MINGGUAN_FIRST_WEEK_ROW + i
        if r > _LOG_MINGGUAN_LAST_WEEK_ROW:
            break
        # Minggu setelah cut-off laporan ini: Actual Kumulatif kosong (meski ada data di DB).
        if anchor_date > cut_off:
            continue
        actual_frac = _actual_kumulatif_for_week(
            report_actuals,
            snap_actuals,
            project,
            anchor_date,
            project_start,
        )
        if actual_frac is not None:
            ws.cell(r, 4).value = actual_frac


def _fill_milestone_progress_sheet(
    ws,
    *,
    phase_scurve_rows: list[tuple[str, dict, int]],
) -> None:
    if ws is None:
        return
    max_rows = 40
    for r in range(2, 2 + max_rows):
        for c in range(1, 14):
            ws.cell(r, c).value = None

    for idx, (roman, row, sc_row) in enumerate(phase_scurve_rows[:max_rows]):
        r = 2 + idx
        name = _row_display_name(row)
        start, end = _row_dates(row)
        ws.cell(r, 1).value = roman
        name_cell = ws.cell(r, 2)
        name_cell.value = name
        name_cell.font = _PHASE_FONT
        ws.cell(r, 3).value = round(float(row.get("weight_pct") or 0) / 100.0, 6)
        ws.cell(r, 4).value = _as_date(start)
        ws.cell(r, 5).value = _as_date(end)
        ws.cell(r, 8).value = f"=NETWORKDAYS(D{r},E{r},Hari_Libur)"
        ws.cell(r, 9).value = f"=SCurve!I{sc_row}"
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
