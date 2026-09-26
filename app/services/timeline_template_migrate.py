"""One-time content migration for timeline_templates JSON items."""


def normalize_template_item_dict(raw: dict) -> dict:
    it = dict(raw)
    typ = str(it.get("item_type") or "phase").lower()
    it["item_type"] = typ
    if typ == "milestone":
        it["duration_days"] = 0
        it["weight_pct"] = 0
    elif int(it.get("duration_days") or 0) < 1:
        it["duration_days"] = 1
    return it


def normalize_template_items_list(items: list) -> list[dict]:
    out: list[dict] = []
    for raw in items or []:
        if isinstance(raw, dict):
            out.append(normalize_template_item_dict(raw))
    return out


def migrate_template_items_to_phases(db) -> int:
    from sqlalchemy import select

    from app.models import TimelineTemplate

    updated = 0
    for tpl in db.scalars(select(TimelineTemplate)).all():
        items = list(tpl.items or [])
        changed = False
        new_items: list[dict] = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            it = dict(raw)
            parent = it.get("parent_key")
            typ = str(it.get("item_type") or "milestone").lower()
            if not parent and typ == "milestone":
                it["item_type"] = "phase"
                changed = True
            if str(it.get("item_type") or "").lower() == "milestone":
                if it.get("duration_days") != 0 or it.get("weight_pct") not in (0, 0.0, None):
                    it["duration_days"] = 0
                    it["weight_pct"] = 0
                    changed = True
            new_items.append(it)
        if changed:
            tpl.items = new_items
            updated += 1
    if updated:
        db.flush()
    return updated
