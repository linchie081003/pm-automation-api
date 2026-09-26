from datetime import date
from types import SimpleNamespace

from app.services.schedule import snapshot_anchor_for_date


def test_snapshot_anchor_uses_report_anchor_not_monday():
    project = SimpleNamespace(
        weekly_report_anchor_weekday=4,  # Jumat
        weekly_report_cutoff_offset_days=0,
    )
    # Kamis 8 Okt 2026 → anchor Jumat berikutnya 9 Okt (bukan Senin / Jumat sebelumnya)
    anchor, cut = snapshot_anchor_for_date(project, date(2026, 10, 8))
    assert anchor == date(2026, 10, 9)
    anchor2, _ = snapshot_anchor_for_date(project, date(2026, 10, 9))
    assert anchor2 == date(2026, 10, 9)
    assert cut == date(2026, 10, 9)
