"""Task / phase predecessor link types (MS Project–style, hari kerja)."""

from __future__ import annotations

import enum
from datetime import date

from sqlalchemy.orm import Session


class PredecessorLinkType(str, enum.Enum):
    FS = "FS"  # Finish-to-Start
    SS = "SS"  # Start-to-Start
    FF = "FF"  # Finish-to-Finish
    SF = "SF"  # Start-to-Finish


def parse_predecessor_link_type(raw: str | None) -> PredecessorLinkType:
    if not raw or not str(raw).strip():
        return PredecessorLinkType.FS
    key = str(raw).strip().upper()
    try:
        return PredecessorLinkType(key)
    except ValueError:
        return PredecessorLinkType.FS


def resolve_successor_span(
    link: PredecessorLinkType,
    pred_start: date | None,
    pred_end: date | None,
    duration_days: int,
    db: Session | None,
) -> tuple[date | None, date | None]:
    """
    Return (start, target) for successor given predecessor dates and inclusive durasi.
    """
    from app.services.business_calendar import (
        add_business_days,
        business_day_after,
        subtract_business_days,
    )

    dur = max(int(duration_days or 1), 1)

    if link == PredecessorLinkType.FS:
        if not pred_end:
            return None, None
        start = business_day_after(pred_end, db)
        return start, add_business_days(start, dur, db)

    if link == PredecessorLinkType.SS:
        if not pred_start:
            return None, None
        start = add_business_days(pred_start, 1, db)
        return start, add_business_days(start, dur, db)

    if link == PredecessorLinkType.FF:
        if not pred_end:
            return None, None
        target = add_business_days(pred_end, 1, db)
        start = subtract_business_days(target, dur, db)
        return start, target

    if link == PredecessorLinkType.SF:
        if not pred_start:
            return None, None
        target = add_business_days(pred_start, 1, db)
        start = subtract_business_days(target, dur, db)
        return start, target

    return None, None


def link_type_requires_pred_end(link: PredecessorLinkType) -> bool:
    return link in (PredecessorLinkType.FS, PredecessorLinkType.FF)


def link_type_requires_pred_start(link: PredecessorLinkType) -> bool:
    return link in (PredecessorLinkType.SS, PredecessorLinkType.SF)
