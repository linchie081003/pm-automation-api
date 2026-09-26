"""Phase A-F schema extensions

Revision ID: 20260225_phase_af
"""

revision = "20260225_phase_af"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Dev environments may use create_all + schema_migrate.py; migration documents phase A-F.
    pass


def downgrade() -> None:
    pass
