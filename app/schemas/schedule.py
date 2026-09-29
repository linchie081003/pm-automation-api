from datetime import date
from typing import Optional

from pydantic import BaseModel, Field


class RebaselineRequest(BaseModel):
    effective_from: Optional[date] = None
    reason: str = Field(min_length=1, max_length=2000)


class BaselineMilestoneOut(BaseModel):
    name: str
    target_date: Optional[date]
    weight_pct: float
    milestone_id: Optional[int]

    model_config = {"from_attributes": True}


class BaselineOut(BaseModel):
    id: int
    version: int
    effective_from: date
    reason: Optional[str]
    is_current: bool
    is_draft: bool = False
    created_at: str
    milestones: list[BaselineMilestoneOut] = []

    model_config = {"from_attributes": True}


class ProgressSnapshotOut(BaseModel):
    id: int
    week_start: date
    week_end: date
    baseline_version: int
    planned_cumulative_pct: float
    actual_cumulative_pct: float
    spi_at_week: float
    source: str
    created_at: str

    model_config = {"from_attributes": True}


class ScurvePoint(BaseModel):
    date: str
    cut_off_date: Optional[str] = None
    planned_pct: float
    actual_pct: Optional[float] = None
    baseline_version: Optional[int]
    is_frozen: bool
    spi: Optional[float] = None


class RebaselineMarker(BaseModel):
    version: int
    effective_from: str
    reason: Optional[str]
    is_current: bool
    created_at: Optional[str]


class SaveWeekResponse(BaseModel):
    created: bool
    snapshot: ProgressSnapshotOut
