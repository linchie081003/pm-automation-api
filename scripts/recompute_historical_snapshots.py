"""Migrate legacy forward snapshots to trailing keys and recompute stored metrics.

Run from backend dir:
  python -m scripts.recompute_historical_snapshots --dry-run
  python -m scripts.recompute_historical_snapshots --project-id 1
  python -m scripts.recompute_historical_snapshots --all --yes
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

backend = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(backend))

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Project
from app.services.snapshot_recompute import (
    recompute_all_projects,
    recompute_project_historical_snapshots,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", type=int, help="Single project id")
    parser.add_argument("--all", action="store_true", help="All projects")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report actions without writing to the database",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Required to commit changes (ignored with --dry-run)",
    )
    args = parser.parse_args()

    if not args.project_id and not args.all:
        parser.error("Specify --project-id N or --all")
    if not args.dry_run and not args.yes:
        print("Re-run with --yes to apply changes, or use --dry-run to preview.")
        return

    db = SessionLocal()
    try:
        if args.project_id:
            stats = recompute_project_historical_snapshots(
                db, args.project_id, dry_run=args.dry_run
            )
            results = [stats]
        else:
            results = recompute_all_projects(db, dry_run=args.dry_run)

        print(json.dumps(results, indent=2, default=str))
        if not args.dry_run:
            db.commit()
            print("Committed.")
        else:
            db.rollback()
            print("Dry run — no changes committed.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
