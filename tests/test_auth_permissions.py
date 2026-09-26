from app.services.authorization import has_permission


def test_admin_wildcard():
    assert has_permission({"*"}, "approvals.decide")
    assert has_permission({"*"}, "anything.here")


def test_exact_permission():
    assert has_permission({"projects.read.all"}, "projects.read.all")
    assert not has_permission({"projects.read.own"}, "projects.read.all")


def test_finance_no_approve():
    finance = {
        "projects.read.all",
        "reports.weekly.download",
        "schedule.read",
        "dashboard.executive",
    }
    assert not has_permission(finance, "approvals.decide")
    assert has_permission(finance, "dashboard.executive")
