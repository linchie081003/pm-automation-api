"""PO completeness rules."""
from sqlalchemy.orm import Session

from app.models import ProjectPo


def po_complete_for_closing(db: Session, project_id: int) -> bool:
    po = db.get(ProjectPo, project_id)
    if not po:
        return False
    has_no = bool(po.po_no and str(po.po_no).strip())
    has_due = bool(po.po_due_date)
    has_items = bool(po.service_items and len(po.service_items) > 0)
    return has_no and has_due and (has_items or bool(po.po_name and str(po.po_name).strip()))


def po_closing_error_message() -> str:
    return (
        "Lengkapi tab PO (nomor PO, due date, dan service items atau judul PO) "
        "sebelum fase BAST atau Closing."
    )
