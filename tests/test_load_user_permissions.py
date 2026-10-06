from unittest.mock import MagicMock

from app.models import Permission, Role
from app.services.authorization import load_user_permissions


def test_admin_role_uses_db_permissions_not_implicit_wildcard(monkeypatch):
    admin_perm = Permission(id=1, code="projects.read.all", name="x", module="p")
    decide = Permission(id=2, code="approvals.decide", name="y", module="w")
    role = Role(id=1, code="admin", name="Admin", permissions=[admin_perm])
    user = MagicMock()
    user.id = 1
    user.roles = [role]

    db = MagicMock()
    db.scalar.return_value = user

    codes = load_user_permissions(db, user)
    assert codes == {"projects.read.all"}
    assert "approvals.decide" not in codes
    assert "*" not in codes
