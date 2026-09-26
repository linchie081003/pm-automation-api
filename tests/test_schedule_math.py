from datetime import date

from app.services.schedule import compute_spi, planned_pct_as_of


class Row:
    def __init__(self, weight_pct, target_date):
        self.weight_pct = weight_pct
        self.target_date = target_date


def test_planned_pct():
    rows = [
        Row(50, date(2026, 3, 1)),
        Row(50, date(2026, 4, 1)),
    ]
    assert planned_pct_as_of(rows, date(2026, 3, 15)) == 50.0
    assert planned_pct_as_of(rows, date(2026, 4, 15)) == 100.0


def test_spi():
    assert compute_spi(90, 100) == 0.9
    assert compute_spi(100, 0) is None
