"""Hierarchical timeline structure and weight validation."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.models import TimelineItemType

WEIGHT_TOLERANCE = 0.5


@dataclass
class TimelineItemRow:
    row_key: str
    name: str
    item_type: str
    weight_pct: float
    parent_key: str | None = None
    duration_days: int = 1
    children: list[TimelineItemRow] = field(default_factory=list)


def _norm_type(raw: str) -> TimelineItemType:
    try:
        return TimelineItemType(str(raw or "phase").strip().lower())
    except ValueError:
        return TimelineItemType.phase


def _is_weighted_type(t: TimelineItemType) -> bool:
    return t in (
        TimelineItemType.phase,
        TimelineItemType.task,
        TimelineItemType.subtask,
    )


def build_tree(items: list[dict]) -> tuple[list[TimelineItemRow], dict[str, TimelineItemRow]]:
    nodes: dict[str, TimelineItemRow] = {}
    order: list[str] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("row_key") or raw.get("id") or "").strip()
        if not key:
            continue
        row = TimelineItemRow(
            row_key=key,
            name=str(raw.get("name") or "").strip(),
            item_type=str(raw.get("item_type") or "phase"),
            weight_pct=float(raw.get("weight_pct") or 0),
            parent_key=(
                str(raw.get("parent_key") or raw.get("parent_ref") or "").strip() or None
            ),
            duration_days=(
                int(raw["duration_days"])
                if raw.get("duration_days") is not None
                else 1
            ),
        )
        nodes[key] = row
        order.append(key)

    roots: list[TimelineItemRow] = []
    for key in order:
        row = nodes[key]
        pk = row.parent_key
        if pk and pk in nodes:
            nodes[pk].children.append(row)
        elif not pk:
            roots.append(row)
        else:
            raise ValueError(f"parent_key tidak ditemukan: {pk} (baris {key})")
    return roots, nodes


def validate_timeline_items(items: list[dict]) -> None:
    if not items:
        raise ValueError("Minimal satu baris timeline.")
    roots, nodes = build_tree(items)

    for key, row in nodes.items():
        if not row.name:
            raise ValueError(f"Nama kosong untuk row_key {key}")
        t = _norm_type(row.item_type)
        parent = None
        if row.parent_key:
            parent = nodes.get(row.parent_key)
            if not parent:
                raise ValueError(f"Parent {row.parent_key} tidak ada untuk {key}")

        if parent is None:
            if t == TimelineItemType.milestone:
                raise ValueError(
                    f"Milestone gate «{row.name}» tidak boleh di root — pilih parent_key "
                    f"ke phase (mis. ph_uat). Hanya phase yang boleh di root."
                )
            if t != TimelineItemType.phase:
                raise ValueError(
                    f"Baris root «{row.name}» harus tipe phase (bukan {t.value})."
                )
        elif _norm_type(parent.item_type) == TimelineItemType.phase:
            if t not in (TimelineItemType.task, TimelineItemType.milestone):
                raise ValueError(
                    f"Anak phase «{parent.name}» hanya task atau milestone gate (baris {key})."
                )
        elif _norm_type(parent.item_type) == TimelineItemType.task:
            if t != TimelineItemType.subtask:
                raise ValueError(
                    f"Anak task «{parent.name}» hanya subtask (baris {key})."
                )
        else:
            raise ValueError(f"Tipe parent tidak valid untuk baris {key}.")

        if t == TimelineItemType.milestone:
            if abs(row.weight_pct) > WEIGHT_TOLERANCE:
                raise ValueError(
                    f"Milestone gate «{row.name}» bobot harus 0% (sekarang {row.weight_pct})."
                )
        elif (row.duration_days or 0) < 1:
            raise ValueError(
                f"Durasi «{row.name}» minimal 1 hari kerja (tipe {t.value})."
            )
        if t == TimelineItemType.subtask and parent and _norm_type(parent.item_type) != TimelineItemType.task:
            raise ValueError(f"Subtask «{row.name}» harus di bawah task.")

    phase_roots = [r for r in roots if _norm_type(r.item_type) == TimelineItemType.phase]
    if not phase_roots:
        raise ValueError("Minimal satu phase di root.")
    root_weight = sum(r.weight_pct for r in phase_roots)
    if abs(root_weight - 100.0) > WEIGHT_TOLERANCE:
        raise ValueError(
            f"Total bobot phase root {root_weight:.1f}% — harus 100% (±{WEIGHT_TOLERANCE})."
        )

    def check_children(parent: TimelineItemRow) -> None:
        pt = _norm_type(parent.item_type)
        if pt == TimelineItemType.milestone:
            return
        weighted_children = [
            c
            for c in parent.children
            if _norm_type(c.item_type) != TimelineItemType.milestone
        ]
        if not weighted_children:
            return
        child_sum = sum(c.weight_pct for c in weighted_children)
        if abs(child_sum - parent.weight_pct) > WEIGHT_TOLERANCE:
            raise ValueError(
                f"Bobot anak «{parent.name}» = {child_sum:.1f}% "
                f"harus sama dengan bobot parent {parent.weight_pct:.1f}%."
            )
        for ch in parent.children:
            check_children(ch)

    for r in roots:
        check_children(r)


def validate_draft_rows(rows: list[dict], id_to_row: dict[int, dict] | None = None) -> None:
    """Validate draft timeline rows (parent_ref / parent_id)."""
    if not rows:
        raise ValueError("Timeline kosong.")
    items: list[dict] = []
    for raw in rows:
        parent_ref = raw.get("parent_ref") or raw.get("parent_row_key")
        if parent_ref is None and raw.get("parent_id") and id_to_row:
            pid = raw["parent_id"]
            pr = id_to_row.get(pid) or id_to_row.get(str(pid))
            if pr:
                parent_ref = pr.get("row_key") or str(pr.get("id"))
        items.append(
            {
                "row_key": str(raw.get("row_key") or raw.get("id") or ""),
                "name": raw.get("name"),
                "item_type": raw.get("item_type") or "phase",
                "weight_pct": raw.get("weight_pct") or 0,
                "parent_key": parent_ref,
                "duration_days": raw.get("duration_days"),
            }
        )
    validate_timeline_items(items)
