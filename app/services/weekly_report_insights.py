"""Draft highlights: phase GAP, tasks done this week, tasks planned next week."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import ClickUpTaskCache, Milestone, TimelineItemType
from app.services.progress import build_clickup_lookups, row_clickup_progress_pct
from app.services.report_calendar import resolve_report_period
from app.services.schedule import _phase_rows_for_planned, phase_planned_progress_pct


def _ms_to_date(value) -> date | None:
    if value is None:
        return None
    from app.services.clickup import _parse_date

    if isinstance(value, (int, float)):
        ts = int(value)
        if ts > 10_000_000_000:
            ts //= 1000
        return datetime.utcfromtimestamp(ts).date()
    if isinstance(value, str):
        if value.isdigit():
            ts = int(value)
            if ts > 10_000_000_000:
                ts //= 1000
            return datetime.utcfromtimestamp(ts).date()
        return _parse_date(value)
    return None


def _task_closed_in_period(task: ClickUpTaskCache, period_start: date, cut_off: date) -> bool:
    if not task.is_closed:
        return False
    raw = task.raw_json if isinstance(task.raw_json, dict) else {}
    for key in ("date_closed", "date_done", "date_updated"):
        d = _ms_to_date(raw.get(key))
        if d and period_start <= d <= cut_off:
            return True
    if task.due_date and period_start <= task.due_date <= cut_off:
        return True
    return False


def _task_planned_next_period(
    task: ClickUpTaskCache,
    next_start: date,
    next_end: date,
) -> bool:
    if task.is_closed:
        return False
    raw = task.raw_json if isinstance(task.raw_json, dict) else {}
    start = _ms_to_date(raw.get("start_date"))
    due = task.due_date or _ms_to_date(raw.get("due_date"))
    if due and next_start <= due <= next_end:
        return True
    if start and next_start <= start <= next_end:
        return True
    if due and due <= next_end and (task.percent_complete or 0) < 100:
        return due >= next_start - timedelta(days=7)
    return False


def _timeline_phases(plan_rows: list) -> list:
    phases = _phase_rows_for_planned(plan_rows)
    if phases:
        return phases
    from app.services.schedule import _progress_weight_rows

    return [
        m
        for m in _progress_weight_rows(plan_rows)
        if getattr(m, "start_date", None) or getattr(m, "target_date", None)
    ]


def _phase_overlaps_period(phase, period_start: date, period_end: date) -> bool:
    start = getattr(phase, "start_date", None)
    end = getattr(phase, "target_date", None)
    if not start and not end:
        return False
    s = start or end
    e = end or start
    assert s and e
    return s <= period_end and e >= period_start


def phases_in_period(
    db: Session,
    plan_rows: list,
    milestones: list[Milestone],
    caches: list[ClickUpTaskCache],
    period_start: date,
    period_end: date,
    *,
    as_of: date,
) -> list[dict]:
    tasks_by_id, milestone_cache = build_clickup_lookups(milestones, caches)
    rows: list[dict] = []
    for phase in _timeline_phases(plan_rows):
        if not _phase_overlaps_period(phase, period_start, period_end):
            continue
        planned = phase_planned_progress_pct(phase, as_of, db)
        actual = row_clickup_progress_pct(
            phase, milestones, tasks_by_id, milestone_cache, caches=caches
        )
        rows.append(
            {
                "name": phase.name,
                "start_date": phase.start_date.isoformat() if phase.start_date else None,
                "target_date": phase.target_date.isoformat() if phase.target_date else None,
                "planned_pct": round(planned, 2),
                "actual_pct": round(float(actual or 0), 2),
            }
        )
    rows.sort(key=lambda r: (r["start_date"] or "", r["name"]))
    return rows


def phase_gap_rows(
    db: Session,
    plan_rows: list,
    milestones: list[Milestone],
    caches: list[ClickUpTaskCache],
    as_of: date,
    *,
    min_gap_pp: float = 1.0,
) -> list[dict]:
    tasks_by_id, milestone_cache = build_clickup_lookups(milestones, caches)
    phases = _phase_rows_for_planned(plan_rows)
    rows: list[dict] = []
    if phases:
        for phase in phases:
            planned = phase_planned_progress_pct(phase, as_of, db)
            actual = row_clickup_progress_pct(
                phase, milestones, tasks_by_id, milestone_cache, caches=caches
            )
            gap_pp = round(planned - float(actual or 0), 2)
            if gap_pp >= min_gap_pp:
                rows.append(
                    {
                        "phase_id": phase.id,
                        "name": phase.name,
                        "planned_pct": round(planned, 2),
                        "actual_pct": round(float(actual or 0), 2),
                        "gap_pp": gap_pp,
                    }
                )
    else:
        from app.services.schedule import _progress_weight_rows

        for m in _progress_weight_rows(plan_rows):
            if m.item_type != TimelineItemType.milestone:
                continue
            target = getattr(m, "target_date", None)
            planned = 100.0 if target and target <= as_of else 0.0
            actual = row_clickup_progress_pct(
                m, milestones, tasks_by_id, milestone_cache, caches=caches
            )
            gap_pp = round(planned - float(actual or 0), 2)
            if gap_pp >= min_gap_pp:
                rows.append(
                    {
                        "phase_id": m.id,
                        "name": m.name,
                        "planned_pct": round(planned, 2),
                        "actual_pct": round(float(actual or 0), 2),
                        "gap_pp": gap_pp,
                    }
                )
    rows.sort(key=lambda r: r["gap_pp"], reverse=True)
    return rows


def task_activity_rows(
    tasks: list[ClickUpTaskCache],
    period_start: date,
    cut_off: date,
    next_start: date,
    next_end: date,
    *,
    limit: int = 15,
) -> tuple[list[dict], list[dict]]:
    leaf = [t for t in tasks if t.parent_task_id or t.milestone_id]
    pool = leaf if leaf else list(tasks)
    done: list[dict] = []
    next_week: list[dict] = []
    for t in pool:
        if _task_closed_in_period(t, period_start, cut_off):
            done.append(
                {
                    "name": t.name,
                    "status": t.status,
                    "due_date": t.due_date.isoformat() if t.due_date else None,
                }
            )
        elif _task_planned_next_period(t, next_start, next_end):
            next_week.append(
                {
                    "name": t.name,
                    "status": t.status,
                    "due_date": t.due_date.isoformat() if t.due_date else None,
                }
            )
    done.sort(key=lambda x: x["name"])
    next_week.sort(key=lambda x: (x["due_date"] or "9999", x["name"]))
    return done[:limit], next_week[:limit]


def build_highlights_draft(
    *,
    planned_pct: float,
    actual_pct: float,
    deviation_pct: float,
    gap_pp: float,
    rag_gap: str | None,
    phase_gaps: list[dict],
    tasks_done: list[dict],
    tasks_next: list[dict],
    phases_current: list[dict],
    phases_next: list[dict],
    next_period_label: str,
) -> str:
    lines: list[str] = []
    sign = "+" if deviation_pct >= 0 else ""
    rag_txt = (rag_gap or "—").upper()
    lines.append(
        f"Ringkasan progress: Planned {planned_pct:.2f}% · Actual {actual_pct:.2f}% · "
        f"Deviasi {sign}{deviation_pct:.2f}% · RAG gap {rag_txt}."
    )
    lines.append("")
    lines.append("GAP timeline phase (ketinggalan vs target):")
    if phase_gaps:
        for g in phase_gaps[:6]:
            lines.append(
                f"- {g['name']}: target {g['planned_pct']:.1f}% vs actual "
                f"{g['actual_pct']:.1f}% (selisih {g['gap_pp']:.1f} p.p.)"
            )
    else:
        lines.append("- Tidak ada phase dengan ketinggalan signifikan vs target timeline.")
    lines.append("")
    lines.append("Task selesai minggu ini:")
    if tasks_done:
        for t in tasks_done:
            due = t.get("due_date") or "—"
            lines.append(f"- {t['name']} ({t.get('status') or 'done'}, due {due})")
    elif phases_current:
        lines.append("- (Task tidak terdeteksi — fase aktif minggu ini:)")
        for p in phases_current[:8]:
            lines.append(
                f"  · {p['name']}: target {p['planned_pct']:.1f}% · actual "
                f"{p['actual_pct']:.1f}%"
            )
    else:
        lines.append("- (Belum terdeteksi task/fase di periode ini.)")
    lines.append("")
    lines.append(f"Task / fase rencana {next_period_label}:")
    if tasks_next:
        for t in tasks_next:
            due = t.get("due_date") or "—"
            lines.append(f"- {t['name']} ({t.get('status') or 'open'}, due {due})")
    elif phases_next:
        lines.append("- (Task tidak terdeteksi — fase di minggu depan:)")
        for p in phases_next[:8]:
            sd = p.get("start_date") or "—"
            td = p.get("target_date") or "—"
            lines.append(f"  · {p['name']} ({sd} s/d {td})")
    else:
        lines.append("- (Belum terdeteksi task/fase terjadwal.)")
    return "\n".join(lines)


def weekly_report_preview_insights(
    db: Session,
    project,
    *,
    anchor: date,
    period_start: date,
    cut_off: date,
    plan_rows: list,
    planned_pct: float,
    actual_pct: float,
    milestones: list[Milestone],
    tasks: list[ClickUpTaskCache],
    rag_gap: str | None = None,
    rag_schedule: str | None = None,
) -> dict:
    deviation_pct = round(actual_pct - planned_pct, 2)
    gap_pp = round(planned_pct - actual_pct, 2)

    caches = list(tasks)
    phase_gaps = phase_gap_rows(db, plan_rows, milestones, caches, cut_off)

    next_anchor = anchor + timedelta(days=7)
    _, next_start, next_end = resolve_report_period(
        project.weekly_report_anchor_weekday,
        project.weekly_report_cutoff_offset_days,
        next_anchor,
        cut_off,
    )
    tasks_done, tasks_next = task_activity_rows(
        caches, period_start, cut_off, next_start, next_end
    )
    phases_current = phases_in_period(
        db,
        plan_rows,
        milestones,
        caches,
        period_start,
        cut_off,
        as_of=cut_off,
    )
    phases_next = phases_in_period(
        db,
        plan_rows,
        milestones,
        caches,
        next_start,
        next_end,
        as_of=next_end,
    )
    use_phase_fallback = not tasks_done and bool(phases_current)
    use_phase_next_fallback = not tasks_next and bool(phases_next)
    next_label = f"minggu depan ({next_start.isoformat()} s/d {next_end.isoformat()})"
    highlights_draft = build_highlights_draft(
        planned_pct=planned_pct,
        actual_pct=actual_pct,
        deviation_pct=deviation_pct,
        gap_pp=gap_pp,
        rag_gap=rag_gap,
        phase_gaps=phase_gaps,
        tasks_done=tasks_done,
        tasks_next=tasks_next,
        phases_current=phases_current,
        phases_next=phases_next,
        next_period_label=next_label,
    )
    return {
        "deviation_pct": deviation_pct,
        "deviation_pp": deviation_pct,
        "gap_pp": gap_pp,
        "rag_gap": rag_gap,
        "rag_schedule": rag_schedule,
        "rag_deviation": rag_gap,
        "phase_gaps": phase_gaps,
        "phases_current_week": phases_current,
        "phases_next_week": phases_next,
        "use_phase_fallback": use_phase_fallback,
        "use_phase_next_fallback": use_phase_next_fallback,
        "tasks_completed": tasks_done,
        "tasks_next_week": tasks_next,
        "next_period_start": next_start.isoformat(),
        "next_period_end": next_end.isoformat(),
        "highlights_draft": highlights_draft,
    }
