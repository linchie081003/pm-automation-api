import pytest

from app.models import Project, ProjectPhase
from app.services.project_duplicate import validate_project_duplication


def test_validate_duplicate_only_sph_phase():
    p = Project(code="X", name="T", owner_id=1, current_phase=ProjectPhase.kickoff)
    with pytest.raises(ValueError, match="fase SPH"):
        validate_project_duplication(p)
    p.current_phase = ProjectPhase.po_received
    validate_project_duplication(p)
