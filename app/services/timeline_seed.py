from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ProjectMethodology, TimelineTemplate


def _waterfall_items() -> list[dict]:
    """Phase → task → subtask + milestone gates (bobot gate = 0)."""
    return [
        {
            "row_key": "ph_init",
            "name": "Inisiasi & perencanaan",
            "duration_days": 10,
            "weight_pct": 15,
            "item_type": "phase",
            "parent_key": None,
            "sort_order": 0,
        },
        {
            "row_key": "t_req",
            "name": "Requirements & scope baseline",
            "duration_days": 4,
            "weight_pct": 8,
            "item_type": "task",
            "parent_key": "ph_init",
            "sort_order": 1,
        },
        {
            "row_key": "st_req_ws",
            "name": "Workshop kebutuhan",
            "duration_days": 2,
            "weight_pct": 4,
            "item_type": "subtask",
            "parent_key": "t_req",
            "sort_order": 2,
        },
        {
            "row_key": "st_req_doc",
            "name": "Dokumen BRD / sign-off",
            "duration_days": 2,
            "weight_pct": 4,
            "item_type": "subtask",
            "parent_key": "t_req",
            "sort_order": 3,
        },
        {
            "row_key": "t_design",
            "name": "Solution design",
            "duration_days": 4,
            "weight_pct": 5,
            "item_type": "task",
            "parent_key": "ph_init",
            "sort_order": 4,
        },
        {
            "row_key": "t_plan",
            "name": "Project plan & RACI",
            "duration_days": 2,
            "weight_pct": 2,
            "item_type": "task",
            "parent_key": "ph_init",
            "sort_order": 5,
        },
        {
            "row_key": "mg_ko",
            "name": "Kick-off signed",
            "duration_days": 0,
            "weight_pct": 0,
            "item_type": "milestone",
            "parent_key": "ph_init",
            "sort_order": 6,
        },
        {
            "row_key": "ph_build",
            "name": "Build & konfigurasi",
            "duration_days": 40,
            "weight_pct": 50,
            "item_type": "phase",
            "parent_key": None,
            "sort_order": 10,
        },
        {
            "row_key": "t_dev",
            "name": "Development / konfigurasi",
            "duration_days": 25,
            "weight_pct": 30,
            "item_type": "task",
            "parent_key": "ph_build",
            "sort_order": 11,
        },
        {
            "row_key": "st_dev_core",
            "name": "Core module",
            "duration_days": 15,
            "weight_pct": 18,
            "item_type": "subtask",
            "parent_key": "t_dev",
            "sort_order": 12,
        },
        {
            "row_key": "st_dev_int",
            "name": "Integrasi",
            "duration_days": 10,
            "weight_pct": 12,
            "item_type": "subtask",
            "parent_key": "t_dev",
            "sort_order": 13,
        },
        {
            "row_key": "t_sit",
            "name": "SIT / unit test",
            "duration_days": 10,
            "weight_pct": 12,
            "item_type": "task",
            "parent_key": "ph_build",
            "sort_order": 14,
        },
        {
            "row_key": "t_data",
            "name": "Data migration / cutover prep",
            "duration_days": 5,
            "weight_pct": 8,
            "item_type": "task",
            "parent_key": "ph_build",
            "sort_order": 15,
        },
        {
            "row_key": "ph_uat",
            "name": "UAT & deploy",
            "duration_days": 15,
            "weight_pct": 25,
            "item_type": "phase",
            "parent_key": None,
            "sort_order": 20,
        },
        {
            "row_key": "t_uat",
            "name": "UAT support",
            "duration_days": 8,
            "weight_pct": 15,
            "item_type": "task",
            "parent_key": "ph_uat",
            "sort_order": 21,
        },
        {
            "row_key": "t_deploy",
            "name": "Deployment & go-live",
            "duration_days": 5,
            "weight_pct": 10,
            "item_type": "task",
            "parent_key": "ph_uat",
            "sort_order": 22,
        },
        {
            "row_key": "mg_uat",
            "name": "UAT sign-off",
            "duration_days": 0,
            "weight_pct": 0,
            "item_type": "milestone",
            "parent_key": "ph_uat",
            "sort_order": 23,
        },
        {
            "row_key": "mg_live",
            "name": "Go-live",
            "duration_days": 0,
            "weight_pct": 0,
            "item_type": "milestone",
            "parent_key": "ph_uat",
            "sort_order": 24,
        },
        {
            "row_key": "ph_close",
            "name": "Penutupan proyek",
            "duration_days": 8,
            "weight_pct": 10,
            "item_type": "phase",
            "parent_key": None,
            "sort_order": 30,
        },
        {
            "row_key": "t_doc",
            "name": "Dokumentasi & handover",
            "duration_days": 4,
            "weight_pct": 6,
            "item_type": "task",
            "parent_key": "ph_close",
            "sort_order": 31,
        },
        {
            "row_key": "t_lesson",
            "name": "Lessons learned",
            "duration_days": 2,
            "weight_pct": 4,
            "item_type": "task",
            "parent_key": "ph_close",
            "sort_order": 32,
        },
    ]


def _default_items(methodology: ProjectMethodology) -> list[dict]:
    if methodology == ProjectMethodology.waterfall:
        return _waterfall_items()
    if methodology == ProjectMethodology.agile:
        return [
            {
                "row_key": "ph_s0",
                "name": "Sprint 0 / Discovery",
                "duration_days": 5,
                "weight_pct": 15,
                "item_type": "phase",
                "parent_key": None,
                "sort_order": 0,
            },
            {
                "row_key": "t_disc",
                "name": "Discovery & backlog",
                "duration_days": 5,
                "weight_pct": 15,
                "item_type": "task",
                "parent_key": "ph_s0",
                "sort_order": 1,
            },
            {
                "row_key": "ph_del",
                "name": "Delivery sprints",
                "duration_days": 30,
                "weight_pct": 70,
                "item_type": "phase",
                "parent_key": None,
                "sort_order": 2,
            },
            {
                "row_key": "t_s1",
                "name": "Sprint 1",
                "duration_days": 10,
                "weight_pct": 35,
                "item_type": "task",
                "parent_key": "ph_del",
                "sort_order": 3,
            },
            {
                "row_key": "t_s2",
                "name": "Sprint 2",
                "duration_days": 10,
                "weight_pct": 35,
                "item_type": "task",
                "parent_key": "ph_del",
                "sort_order": 4,
            },
            {
                "row_key": "ph_rel",
                "name": "Release",
                "duration_days": 5,
                "weight_pct": 15,
                "item_type": "phase",
                "parent_key": None,
                "sort_order": 5,
            },
            {
                "row_key": "t_rel",
                "name": "UAT & release",
                "duration_days": 5,
                "weight_pct": 15,
                "item_type": "task",
                "parent_key": "ph_rel",
                "sort_order": 6,
            },
        ]
    # hybrid
    return [
        {
            "row_key": "ph_ko",
            "name": "Kick-off & perencanaan",
            "duration_days": 5,
            "weight_pct": 10,
            "item_type": "phase",
            "parent_key": None,
            "sort_order": 0,
        },
        {
            "row_key": "t_ko",
            "name": "Kick-off & baseline",
            "duration_days": 5,
            "weight_pct": 10,
            "item_type": "task",
            "parent_key": "ph_ko",
            "sort_order": 1,
        },
        {
            "row_key": "ph_build",
            "name": "Delivery (iterative)",
            "duration_days": 30,
            "weight_pct": 60,
            "item_type": "phase",
            "parent_key": None,
            "sort_order": 2,
        },
        {
            "row_key": "t_inc1",
            "name": "Increment 1",
            "duration_days": 15,
            "weight_pct": 30,
            "item_type": "task",
            "parent_key": "ph_build",
            "sort_order": 3,
        },
        {
            "row_key": "t_inc2",
            "name": "Increment 2",
            "duration_days": 15,
            "weight_pct": 30,
            "item_type": "task",
            "parent_key": "ph_build",
            "sort_order": 4,
        },
        {
            "row_key": "ph_uat",
            "name": "UAT & Go-Live",
            "duration_days": 10,
            "weight_pct": 30,
            "item_type": "phase",
            "parent_key": None,
            "sort_order": 5,
        },
        {
            "row_key": "t_uat",
            "name": "UAT & deployment",
            "duration_days": 10,
            "weight_pct": 30,
            "item_type": "task",
            "parent_key": "ph_uat",
            "sort_order": 6,
        },
    ]


def ensure_default_timeline_templates(db: Session) -> None:
    from app.services.timeline_template_migrate import migrate_template_items_to_phases

    migrate_template_items_to_phases(db)
    for meth in ProjectMethodology:
        existing = db.scalar(
            select(TimelineTemplate).where(
                TimelineTemplate.methodology == meth,
                TimelineTemplate.name.like(f"Default {meth.value}%"),
            )
        )
        wbs = _default_items(meth)
        if existing:
            existing.items = wbs
            existing.description = f"Template timeline standar — {meth.value} (WBS)"
            existing.is_active = True
            # #region agent log
            try:
                import json
                import time
                from pathlib import Path

                log_path = Path(__file__).resolve().parents[3] / "debug-aa7388.log"
                with log_path.open("a", encoding="utf-8") as f:
                    f.write(
                        json.dumps(
                            {
                                "sessionId": "aa7388",
                                "hypothesisId": "H2-seed-skip",
                                "location": "timeline_seed.py:ensure_default",
                                "message": "refreshed default template WBS",
                                "data": {
                                    "methodology": meth.value,
                                    "template_id": existing.id,
                                    "item_count": len(wbs),
                                },
                                "timestamp": int(time.time() * 1000),
                            }
                        )
                        + "\n"
                    )
            except OSError:
                pass
            # #endregion
            continue
        db.add(
            TimelineTemplate(
                name=f"Default {meth.value.title()}",
                methodology=meth,
                description=f"Template timeline standar — {meth.value}",
                items=wbs,
                is_active=True,
            )
        )
    db.commit()
