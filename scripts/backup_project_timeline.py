"""Export timeline data for a project (draft, editor workspace, live milestones).

Run from backend dir:
  python -m scripts.backup_project_timeline --project-id 1
  python -m scripts.backup_project_timeline --all-active
  python -m scripts.backup_project_timeline --project-id 1 --compare other.json
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Milestone, Project, ProjectStatus, TimelineEditorState
from app.services.draft_timeline import list_draft_rows
from app.services.timeline_editor_store import editor_has_rows, list_editor_rows, workspace_updated_at_iso


def _json_default(obj):
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    raise TypeError(type(obj))


def export_project_timeline(db, project_id: int) -> dict:
    project = db.get(Project, project_id)
    if not project:
        raise ValueError(f"Project {project_id} not found")
    draft_rows = list_draft_rows(db, project_id)
    editor_rows = list_editor_rows(db, project_id) if editor_has_rows(db, project_id) else []
    st = db.get(TimelineEditorState, project_id)
    live = db.scalars(select(Milestone).where(Milestone.project_id == project_id)).all()
    return {
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "project_id": project_id,
        "project_code": project.code,
        "draft_timeline": draft_rows,
        "timeline_editor": {
            "rows": editor_rows,
            "workspace_updated_at": workspace_updated_at_iso(db, project_id),
            "state_start_date": st.start_date.isoformat() if st and st.start_date else None,
        },
        "live_milestones": [
            {
                "id": m.id,
                "name": m.name,
                "item_type": m.item_type.value if m.item_type else None,
                "parent_id": m.parent_id,
                "sort_order": m.sort_order,
                "start_date": m.start_date.isoformat() if m.start_date else None,
                "target_date": m.target_date.isoformat() if m.target_date else None,
                "weight_pct": m.weight_pct,
            }
            for m in live
        ],
    }


def _row_signature(rows: list[dict]) -> list[tuple]:
    sig = []
    for r in rows:
        sig.append(
            (
                r.get("row_key") or r.get("id"),
                r.get("name"),
                r.get("item_type"),
                r.get("start_date"),
                r.get("target_date"),
                tuple(
                    (p.get("predecessor_ref"), p.get("link_type"), p.get("lag_days"))
                    for p in (r.get("predecessors") or [])
                ),
            )
        )
    return sig


def compare_exports(a: dict, b: dict) -> list[str]:
    diffs: list[str] = []
    if _row_signature(a.get("draft_timeline") or []) != _row_signature(
        b.get("draft_timeline") or []
    ):
        diffs.append("draft_timeline rows differ")
    if _row_signature(a.get("timeline_editor", {}).get("rows") or []) != _row_signature(
        b.get("timeline_editor", {}).get("rows") or []
    ):
        diffs.append("timeline_editor rows differ")
    return diffs


def main() -> None:
    parser = argparse.ArgumentParser(description="Backup project timeline JSON")
    parser.add_argument("--project-id", type=int, action="append", dest="project_ids")
    parser.add_argument("--all-active", action="store_true")
    parser.add_argument("--out-dir", type=Path, default=Path("timeline_backups"))
    parser.add_argument("--compare", type=Path, help="Compare export to this JSON file")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        ids = list(args.project_ids or [])
        if args.all_active:
            ids.extend(
                db.scalars(
                    select(Project.id).where(Project.status != ProjectStatus.closed)
                ).all()
            )
        ids = sorted(set(ids))
        if not ids:
            raise SystemExit("Specify --project-id and/or --all-active")

        args.out_dir.mkdir(parents=True, exist_ok=True)
        for pid in ids:
            payload = export_project_timeline(db, pid)
            code = payload.get("project_code") or pid
            path = args.out_dir / f"timeline_backup_{code}_{pid}.json"
            path.write_text(
                json.dumps(payload, indent=2, default=_json_default),
                encoding="utf-8",
            )
            print(f"Wrote {path}")
            if args.compare:
                other = json.loads(args.compare.read_text(encoding="utf-8"))
                for line in compare_exports(payload, other):
                    print(f"  compare: {line}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
