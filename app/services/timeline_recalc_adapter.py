"""Adapter: draft timeline dict rows ↔ timeline editor recalc engine."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.services.timeline_editor_engine import (
    normalize_predecessors,
    recalc_timeline_editor_rows,
)
from app.services.timeline_schedule import apply_schedule_driver_to_raw


def _row_key(raw: dict) -> str:
    rk = str(raw.get("row_key") or "").strip()
    if rk:
        return rk
    if raw.get("id") is not None:
        return str(raw["id"])
    return str(raw.get("sort_order") or "")


def prepare_draft_rows_for_recalc(rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    for i, raw in enumerate(rows):
        copy = dict(raw)
        copy["row_key"] = _row_key(copy) or f"row_{i}"
        copy["sort_order"] = int(copy.get("sort_order") if copy.get("sort_order") is not None else i)
        if not copy.get("predecessors"):
            copy["predecessors"] = normalize_predecessors(copy)
        out.append(copy)
    return out


def recalc_draft_dict_rows(
    db: Session | None,
    rows: list[dict],
    project_start: date,
) -> list[dict]:
    working = prepare_draft_rows_for_recalc(rows)
    for raw in working:
        apply_schedule_driver_to_raw(db, raw)
    recalced = recalc_timeline_editor_rows(db, working, project_start)
    by_key = {_row_key(r): r for r in recalced}
    merged: list[dict] = []
    for i, sent in enumerate(working):
        key = _row_key(sent)
        r = by_key.get(key) or recalced[i] if i < len(recalced) else sent
        copy = dict(sent)
        copy.update(
            {
                "start_date": r.get("start_date"),
                "target_date": r.get("target_date"),
                "duration_days": r.get("duration_days"),
            }
        )
        preds = sent.get("predecessors") or normalize_predecessors(r)
        copy["predecessors"] = preds
        first = preds[0] if preds else None
        if first:
            copy["predecessor_ref"] = first.get("predecessor_ref")
            copy["predecessor_link_type"] = first.get("link_type")
        merged.append(copy)
    return merged
