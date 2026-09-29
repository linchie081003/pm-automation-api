"""Build 4-slide weekly report deck (title, overview, progress+S-curve, timeline)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from pptx import Presentation
from pptx.chart.data import ChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Project, ProjectPo, ProjectSph, WeeklyReport
from app.services.report_calendar import resolve_report_period
from app.services.schedule import scurve_points
from app.services.schedule_window import project_report_start_date, schedule_bounds
from app.services.timeline_display import timeline_display_rows_for_project

COLOR_ORANGE = RGBColor(0xF5, 0x82, 0x20)
COLOR_NAVY = RGBColor(0x1E, 0x3A, 0x5F)
COLOR_GREEN = RGBColor(0x00, 0xA6, 0x51)
COLOR_WHITE = RGBColor(0xFF, 0xFF, 0xFF)
COLOR_DARK = RGBColor(0x1E, 0x29, 0x3B)
COLOR_MUTED = RGBColor(0x64, 0x74, 0x8B)
COLOR_PEACH = RGBColor(0xFF, 0xF0, 0xE6)
COLOR_PEACH_LIGHT = RGBColor(0xFF, 0xF7, 0xF0)

WEEKDAY_EN = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
MONTH_EN = (
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def _templates_root() -> Path:
    return Path(settings.templates_path)


def _title_bg_path() -> Path | None:
    for name in ("slide1_bg.png", "slide1_bg.jpg"):
        p = _templates_root() / "weekly_report" / name
        if p.is_file():
            return p
    return None


def _fmt_date_en_long(d: date | None) -> str:
    if not d:
        return "—"
    return f"{WEEKDAY_EN[d.weekday()]}, {d.day:02d} {MONTH_EN[d.month]} {d.year}"


def _fmt_date_slash(d: date | None) -> str:
    if not d:
        return "—"
    return d.strftime("%d/%m/%Y")


def _parse_iso(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _resolve_brief(project: Project, sph: ProjectSph | None) -> str:
    if project.project_brief and project.project_brief.strip():
        return project.project_brief.strip()
    if sph and sph.scope_text and sph.scope_text.strip():
        return sph.scope_text.strip()
    if sph and sph.scope_items:
        lines = []
        for item in sph.scope_items:
            if isinstance(item, dict):
                text = str(item.get("text", "")).strip()
                if text:
                    mod = str(item.get("module", "")).strip()
                    lines.append(f"• {text}" + (f" ({mod})" if mod else ""))
        if lines:
            return "\n".join(lines)
    return "—"


def _resolve_po_due(project: Project, po: ProjectPo | None) -> date | None:
    if po and po.po_due_date:
        return po.po_due_date
    return project.po_due_date or project.planned_end_date


def _project_duration_label(db: Session, project_id: int, sph: ProjectSph | None) -> str:
    rows = timeline_display_rows_for_project(db, project_id)
    total = 0
    for r in rows:
        dur = r.get("display_duration_days") or r.get("duration_days")
        if dur and int(dur) > 0:
            total += int(dur)
    if total > 0:
        label = f"{total} hari kerja (total timeline)"
        if sph and sph.planned_md:
            label += f" · {sph.planned_md:g} MD (rencana SPH)"
        return label
    if sph and sph.planned_md:
        return f"{sph.planned_md:g} mandays (rencana SPH)"
    start, end = schedule_bounds(db, project_id)
    if start and end:
        return f"{start.isoformat()} s.d. {end.isoformat()}"
    return "—"


def _period_label(report_date: date, period_start: date, week_end: date) -> str:
    return f"{_fmt_date_slash(period_start)} s.d. {_fmt_date_slash(week_end)}"


def _status_display(row: dict) -> str:
    cu = row.get("clickup_status")
    if cu:
        mapping = {
            "done": "Done",
            "in_progress": "In Progress",
            "not_started": "Not Started",
            "open": "Not Started",
        }
        return mapping.get(str(cu).lower(), str(cu).replace("_", " ").title())
    st = str(row.get("status") or "open").lower()
    if st in ("done", "closed"):
        return "Done"
    if st == "in_progress":
        return "In Progress"
    return "Not Started"


def _progress_pct(row: dict) -> float | None:
    if row.get("clickup_progress_pct") is not None:
        return float(row["clickup_progress_pct"])
    st = _status_display(row)
    if st == "Done":
        return 100.0
    if st == "Not Started":
        return 0.0
    return None


def _set_run_font(run, *, size: int, bold: bool = False, color: RGBColor | None = None):
    run.font.size = Pt(size)
    run.font.bold = bold
    if color:
        run.font.color.rgb = color


def _add_textbox(
    slide,
    left,
    top,
    width,
    height,
    text: str,
    *,
    size: int = 14,
    bold: bool = False,
    color: RGBColor = COLOR_DARK,
    align=PP_ALIGN.LEFT,
):
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.TOP
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    _set_run_font(run, size=size, bold=bold, color=color)
    return box


def _slide_footer(slide, page: int):
    bar = slide.shapes.add_shape(
        1, Inches(0), Inches(7.05), Inches(4.2), Inches(0.35)
    )
    bar.fill.solid()
    bar.fill.fore_color.rgb = COLOR_ORANGE
    bar.line.fill.background()
    _add_textbox(
        slide,
        Inches(0.15),
        Inches(7.08),
        Inches(3),
        Inches(0.3),
        "www.ebesha.net",
        size=9,
        bold=True,
        color=COLOR_WHITE,
    )
    _add_textbox(
        slide,
        Inches(12.5),
        Inches(7.08),
        Inches(0.6),
        Inches(0.3),
        str(page),
        size=11,
        bold=True,
        color=COLOR_NAVY,
        align=PP_ALIGN.RIGHT,
    )


def _slide_header(slide, title: str):
    _add_textbox(
        slide,
        Inches(0.45),
        Inches(0.35),
        Inches(8),
        Inches(0.55),
        title,
        size=26,
        bold=True,
        color=COLOR_NAVY,
    )
    accent = slide.shapes.add_shape(
        1, Inches(11.2), Inches(0.15), Inches(1.8), Inches(0.55)
    )
    accent.fill.solid()
    accent.fill.fore_color.rgb = COLOR_ORANGE
    accent.line.fill.background()
    accent2 = slide.shapes.add_shape(
        1, Inches(11.55), Inches(0.35), Inches(1.5), Inches(0.45)
    )
    accent2.fill.solid()
    accent2.fill.fore_color.rgb = COLOR_NAVY
    accent2.line.fill.background()


def _blank_slide(prs: Presentation):
    layout = prs.slide_layouts[6] if len(prs.slide_layouts) > 6 else prs.slide_layouts[0]
    return prs.slides.add_slide(layout)


def _build_slide_title(prs: Presentation, project: Project, report_date: date):
    slide = _blank_slide(prs)
    bg = _title_bg_path()
    if bg:
        slide.shapes.add_picture(str(bg), Inches(0), Inches(0), prs.slide_width, prs.slide_height)
    else:
        panel = slide.shapes.add_shape(
            1, Inches(0), Inches(0), Inches(8.8), prs.slide_height
        )
        panel.fill.solid()
        panel.fill.fore_color.rgb = COLOR_ORANGE
        panel.line.fill.background()

    overlay = slide.shapes.add_shape(
        1, Inches(0.55), Inches(2.35), Inches(7.2), Inches(2.35)
    )
    overlay.fill.solid()
    overlay.fill.fore_color.rgb = RGBColor(0x33, 0x33, 0x33)
    overlay.fill.transparency = 0.35
    overlay.line.fill.background()

    _add_textbox(
        slide,
        Inches(0.75),
        Inches(2.5),
        Inches(6.8),
        Inches(0.55),
        "Weekly Report Project",
        size=28,
        bold=True,
        color=COLOR_WHITE,
    )
    _add_textbox(
        slide,
        Inches(0.75),
        Inches(3.15),
        Inches(6.8),
        Inches(0.9),
        project.name,
        size=24,
        bold=True,
        color=COLOR_WHITE,
    )
    _add_textbox(
        slide,
        Inches(0.75),
        Inches(4.05),
        Inches(6.5),
        Inches(0.4),
        _fmt_date_en_long(report_date),
        size=16,
        color=COLOR_WHITE,
    )
    pm = (project.project_manager or "LMD").strip()
    _add_textbox(
        slide,
        Inches(0.55),
        Inches(6.55),
        Inches(4),
        Inches(0.35),
        f"Prepared By: {pm}",
        size=11,
        color=COLOR_DARK,
    )


def _build_slide_overview(
    prs: Presentation,
    project: Project,
    sph: ProjectSph | None,
    po: ProjectPo | None,
    db: Session,
):
    slide = _blank_slide(prs)
    _slide_header(slide, "Overview Project")
    _slide_footer(slide, 2)

    brief = _resolve_brief(project, sph)
    duration = _project_duration_label(db, project.id, sph)
    target = _resolve_po_due(project, po)
    pm = project.project_manager or "—"

    rows = [
        ("Project Name", project.name),
        ("Project Brief", brief),
        ("Project Duration", duration),
        ("Project Target Date", _fmt_date_en_long(target) if target else "—"),
        ("Project Manager", pm),
    ]

    table_shape = slide.shapes.add_table(
        len(rows) + 1, 2, Inches(0.55), Inches(1.15), Inches(12.2), Inches(5.5)
    )
    table = table_shape.table
    table.columns[0].width = Inches(2.4)
    table.columns[1].width = Inches(9.8)

    hdr = table.cell(0, 0)
    hdr.merge(table.cell(0, 1))
    hdr.text = project.name
    for p in hdr.text_frame.paragraphs:
        p.alignment = PP_ALIGN.CENTER
        for run in p.runs:
            _set_run_font(run, size=16, bold=True, color=COLOR_WHITE)
    hdr.fill.solid()
    hdr.fill.fore_color.rgb = COLOR_ORANGE

    for i, (label, value) in enumerate(rows, start=1):
        lc = table.cell(i, 0)
        vc = table.cell(i, 1)
        lc.text = label
        vc.text = value
        lc.fill.solid()
        vc.fill.solid()
        fill = COLOR_PEACH if i % 2 else COLOR_PEACH_LIGHT
        lc.fill.fore_color.rgb = fill
        vc.fill.fore_color.rgb = COLOR_WHITE
        for cell in (lc, vc):
            for p in cell.text_frame.paragraphs:
                for run in p.runs:
                    _set_run_font(
                        run,
                        size=11 if cell is vc and label == "Project Brief" else 12,
                        bold=cell is lc,
                        color=COLOR_DARK,
                    )
        vc.text_frame.word_wrap = True


def _build_slide_progress(
    prs: Presentation,
    project: Project,
    report: WeeklyReport,
    db: Session,
    *,
    planned: float,
    actual: float,
    highlights: str,
    period_start: date,
    week_end: date,
):
    slide = _blank_slide(prs)
    _slide_header(slide, "Progress Milestone")
    _slide_footer(slide, 3)

    period = _period_label(report.week_start, period_start, week_end)
    band = slide.shapes.add_shape(
        1, Inches(0.45), Inches(1.0), Inches(5.55), Inches(0.5)
    )
    band.fill.solid()
    band.fill.fore_color.rgb = COLOR_NAVY
    band.line.fill.background()
    _add_textbox(
        slide,
        Inches(0.55),
        Inches(1.08),
        Inches(5.35),
        Inches(0.42),
        f"Project Status this Period ({period})",
        size=10,
        bold=True,
        color=COLOR_WHITE,
    )

    def metric_box(left, title: str, value: str, header_color: RGBColor):
        hdr_shape = slide.shapes.add_shape(
            1, left, Inches(1.65), Inches(2.5), Inches(0.38)
        )
        hdr_shape.fill.solid()
        hdr_shape.fill.fore_color.rgb = header_color
        hdr_shape.line.fill.background()
        _add_textbox(
            slide,
            left + Inches(0.05),
            Inches(1.68),
            Inches(2.4),
            Inches(0.32),
            title,
            size=11,
            bold=True,
            color=COLOR_WHITE,
            align=PP_ALIGN.CENTER,
        )
        body = slide.shapes.add_shape(
            1, left, Inches(2.03), Inches(2.5), Inches(0.85)
        )
        body.fill.solid()
        body.fill.fore_color.rgb = RGBColor(0xF1, 0xF5, 0xF9)
        body.line.color.rgb = RGBColor(0xE2, 0xE8, 0xF0)
        _add_textbox(
            slide,
            left,
            Inches(2.15),
            Inches(2.5),
            Inches(0.65),
            value,
            size=28,
            bold=True,
            color=COLOR_DARK,
            align=PP_ALIGN.CENTER,
        )

    metric_box(Inches(0.45), "Target Progress", f"{planned:.0f}%", COLOR_ORANGE)
    metric_box(Inches(3.15), "Actual Progress", f"{actual:.0f}%", COLOR_GREEN)

    _add_textbox(
        slide,
        Inches(0.45),
        Inches(3.05),
        Inches(5.2),
        Inches(0.35),
        "Highlight:",
        size=12,
        bold=True,
        color=COLOR_DARK,
    )
    hl = (highlights or "—").strip()
    if len(hl) > 1200:
        hl = hl[:1197] + "…"
    _add_textbox(
        slide,
        Inches(0.45),
        Inches(3.4),
        Inches(5.35),
        Inches(3.35),
        hl,
        size=9,
        color=COLOR_DARK,
    )

    points = scurve_points(db, project.id, date_to=report.week_start)
    if points:
        cats = []
        planned_series = []
        actual_series = []
        for pt in points[-16:]:
            rd = pt.get("date") or pt.get("anchor_date") or pt.get("week_start")
            d = _parse_iso(rd if isinstance(rd, str) else None)
            cats.append(_fmt_date_slash(d) if d else str(rd or ""))
            planned_series.append(float(pt.get("planned_pct") or 0))
            actual_series.append(float(pt.get("actual_pct") or 0))

        chart_data = ChartData()
        chart_data.categories = cats
        chart_data.add_series("Target Linear (%)", planned_series)
        chart_data.add_series("Cummulative Actual (%)", actual_series)

        chart_frame = slide.shapes.add_chart(
            XL_CHART_TYPE.LINE_MARKERS,
            Inches(6.05),
            Inches(1.15),
            Inches(6.85),
            Inches(5.55),
            chart_data,
        )
        chart = chart_frame.chart
        chart.has_legend = True
        chart.legend.position = XL_LEGEND_POSITION.RIGHT
        chart.legend.include_in_layout = False
        chart.chart_title.text_frame.text = project.name[:60]
        if chart.series:
            chart.series[0].format.line.color.rgb = COLOR_ORANGE
            if len(chart.series) > 1:
                chart.series[1].format.line.color.rgb = COLOR_GREEN
    else:
        _add_textbox(
            slide,
            Inches(6.2),
            Inches(2.5),
            Inches(6.5),
            Inches(1),
            "S-curve belum tersedia — konfirmasi timeline kick off dan generate target laporan.",
            size=11,
            color=COLOR_MUTED,
        )


def _activity_name(row: dict) -> str:
    depth = int(row.get("depth") or 0)
    prefix = "  " * depth
    return prefix + str(row.get("name") or "—")


def _build_slide_timeline(prs: Presentation, project: Project, db: Session):
    slide = _blank_slide(prs)
    title = f"Progress {project.name[:48]} – Timeline"
    _slide_header(slide, title)
    _slide_footer(slide, 4)

    rows = timeline_display_rows_for_project(db, project.id)
    display_rows = [r for r in rows if r.get("item_type") != "milestone"][:28]
    if not display_rows:
        display_rows = rows[:28]

    n = len(display_rows) + 1
    table_shape = slide.shapes.add_table(
        n, 7, Inches(0.35), Inches(1.05), Inches(12.55), Inches(5.75)
    )
    table = table_shape.table
    headers = [
        "No",
        "Activity",
        "Start Date",
        "Due Date",
        "Progress",
        "Status",
        "Keterangan",
    ]
    widths = [0.45, 3.6, 1.15, 1.15, 0.85, 1.05, 2.3]
    for i, w in enumerate(widths):
        table.columns[i].width = Inches(w)

    for j, h in enumerate(headers):
        cell = table.cell(0, j)
        cell.text = h
        cell.fill.solid()
        cell.fill.fore_color.rgb = COLOR_ORANGE
        for p in cell.text_frame.paragraphs:
            p.alignment = PP_ALIGN.CENTER
            for run in p.runs:
                _set_run_font(run, size=9, bold=True, color=COLOR_WHITE)

    for i, row in enumerate(display_rows, start=1):
        start_d = _parse_iso(row.get("display_start") or row.get("start_date"))
        end_d = _parse_iso(row.get("display_end") or row.get("target_date"))
        pct = _progress_pct(row)
        pct_txt = f"{pct:.0f}%" if pct is not None else "—"
        notes = (row.get("notes") or "").strip()
        if not notes and row.get("module"):
            notes = str(row.get("module"))
        values = [
            str(i),
            _activity_name(row),
            _fmt_date_slash(start_d),
            _fmt_date_slash(end_d),
            pct_txt,
            _status_display(row),
            notes or "—",
        ]
        fill = COLOR_PEACH if i % 2 == 0 else COLOR_PEACH_LIGHT
        for j, val in enumerate(values):
            cell = table.cell(i, j)
            cell.text = val
            cell.fill.solid()
            cell.fill.fore_color.rgb = fill if j != 0 else COLOR_PEACH_LIGHT
            for p in cell.text_frame.paragraphs:
                align = PP_ALIGN.CENTER if j in (0, 2, 3, 4, 5) else PP_ALIGN.LEFT
                p.alignment = align
                for run in p.runs:
                    bold = j == 1 and int(row.get("depth") or 0) == 0
                    _set_run_font(run, size=8, bold=bold, color=COLOR_DARK)


def build_weekly_report_pptx(
    dest: Path,
    *,
    db: Session,
    project: Project,
    report: WeeklyReport,
    sph: ProjectSph | None = None,
    po: ProjectPo | None = None,
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    frozen = report.frozen_metrics or {}
    planned = float(frozen.get("planned_pct") or 0)
    actual = float(frozen.get("actual_pct") or 0)
    summary = report.summary or {}
    highlights = str(summary.get("highlights") or "")

    pstart = project_report_start_date(db, project)
    _, period_start, week_end = resolve_report_period(
        project.weekly_report_anchor_weekday,
        project.weekly_report_cutoff_offset_days,
        report.week_start,
        project_start_date=pstart,
    )

    _build_slide_title(prs, project, report.week_start)
    _build_slide_overview(prs, project, sph, po, db)
    _build_slide_progress(
        prs,
        project,
        report,
        db,
        planned=planned,
        actual=actual,
        highlights=highlights,
        period_start=period_start,
        week_end=week_end,
    )
    _build_slide_timeline(prs, project, db)

    prs.save(str(dest))
