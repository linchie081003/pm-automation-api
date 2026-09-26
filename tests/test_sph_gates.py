from datetime import date

from app.models import ProjectSph
from app.services.sph import sph_is_complete


def test_sph_complete_requires_fields():
    sph = ProjectSph(
        project_id=1,
        sph_no="SPH-001",
        sph_name="Project Alpha",
        sales_pic="Sales A",
        estimated_start_date=date(2026, 1, 1),
        target_delivery_days=90,
        scope_items=[{"id": "1", "text": "a"}],
        non_scope_items=[{"id": "2", "text": "b"}],
        delivery_items=[{"id": "3", "name": "Delivery", "amount_rupiah": 100_000_000}],
        sph_total_rupiah=100_000_000,
        delivery_method="remote",
        pic_user_name="x",
    )
    assert sph_is_complete(sph)


def test_sph_incomplete():
    assert not sph_is_complete(None)
    assert not sph_is_complete(ProjectSph(project_id=1))
