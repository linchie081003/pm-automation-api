import uuid

DEFAULT_BAST_CHECKLIST_ITEMS: list[dict] = [
    {"id": "default_po_bast", "label": "Data PO lengkap (BAST)", "done": False},
    {"id": "default_progress_100", "label": "Progress Proyek 100%", "done": False},
]


def default_bast_checklist() -> dict:
    items = [{**it} for it in DEFAULT_BAST_CHECKLIST_ITEMS]
    return normalize_bast_checklist({"items": items})


def bast_checklist_for_display(raw: dict | None) -> dict:
    normalized = normalize_bast_checklist(raw)
    if normalized.get("items"):
        return normalized
    return default_bast_checklist()


def normalize_bast_checklist(raw: dict | None) -> dict:
    raw = raw or {}
    items_in = raw.get("items")
    cleaned: list[dict] = []
    if isinstance(items_in, list):
        for it in items_in:
            if not isinstance(it, dict):
                continue
            label = str(it.get("label") or "").strip()
            if not label:
                continue
            item_id = str(it.get("id") or uuid.uuid4())
            cleaned.append(
                {
                    "id": item_id,
                    "label": label,
                    "done": bool(it.get("done")),
                }
            )
    if cleaned:
        complete = all(x["done"] for x in cleaned)
    else:
        complete = bool(raw.get("complete"))
    return {"items": cleaned, "complete": complete}
