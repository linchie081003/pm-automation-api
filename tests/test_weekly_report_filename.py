from app.services.weekly_report import (
    _report_file_basename,
    _safe_project_name_for_file,
)


def test_safe_project_name_sanitizes_spaces_and_symbols():
    assert _safe_project_name_for_file("Sistem Informasi ABC") == "Sistem_Informasi_ABC"
    assert _safe_project_name_for_file("Proyek A & B (Fase 2)") == "Proyek_A_B_Fase_2"


def test_safe_project_name_empty_fallback():
    assert _safe_project_name_for_file("   ") == "project"
    assert _safe_project_name_for_file("!!!") == "project"


def test_report_file_basename_format():
    base = _report_file_basename("20261006", "Sistem Informasi ABC")
    assert base == "20261006_Sistem_Informasi_ABC"
    assert _report_file_basename("20261006", "Proyek A & B (Fase 2)") == "20261006_Proyek_A_B_Fase_2"
