from app.services.bast import (
    bast_checklist_for_display,
    default_bast_checklist,
    normalize_bast_checklist,
)


def test_bast_complete_when_all_items_done():
    out = normalize_bast_checklist(
        {
            "items": [
                {"id": "a", "label": "BAST signed", "done": True},
                {"id": "b", "label": "Invoice paid", "done": True},
            ]
        }
    )
    assert out["complete"] is True
    assert len(out["items"]) == 2


def test_bast_incomplete_when_item_open():
    out = normalize_bast_checklist(
        {"items": [{"label": "One", "done": False}]}
    )
    assert out["complete"] is False


def test_default_bast_checklist_items():
    out = default_bast_checklist()
    labels = [i["label"] for i in out["items"]]
    assert "Data PO lengkap (BAST)" in labels
    assert "Progress Proyek 100%" in labels
    assert out["complete"] is False


def test_bast_checklist_for_display_seeds_when_empty():
    out = bast_checklist_for_display({})
    assert len(out["items"]) >= 2
