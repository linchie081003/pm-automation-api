"""Best-effort ADD COLUMN for dev DBs created before phase A–F."""

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine


def _try_execute(conn: Connection, stmt: str) -> bool:
    """Run SQL; on failure roll back to savepoint so the outer transaction stays valid."""
    conn.execute(text("SAVEPOINT schema_migrate_sp"))
    try:
        conn.execute(text(stmt))
        conn.execute(text("RELEASE SAVEPOINT schema_migrate_sp"))
        return True
    except Exception:
        conn.execute(text("ROLLBACK TO SAVEPOINT schema_migrate_sp"))
        return False


def _add_enum_values_autocommit(engine: Engine, type_name: str, values: list[str]) -> None:
    """ALTER TYPE ADD VALUE runs in autocommit — safe on PostgreSQL 11+."""
    with engine.connect() as conn:
        ac = conn.execution_options(isolation_level="AUTOCOMMIT")
        for val in values:
            try:
                ac.execute(
                    text(f"ALTER TYPE {type_name} ADD VALUE IF NOT EXISTS '{val}'")
                )
            except Exception:
                pass


def _document_type_enum_names(conn: Connection) -> list[str]:
    rows = conn.execute(
        text(
            """
            SELECT DISTINCT t.typname
            FROM pg_type t
            JOIN pg_enum e ON e.enumtypid = t.oid
            JOIN pg_attribute a ON a.atttypid = t.oid
            JOIN pg_class c ON c.oid = a.attrelid
            WHERE c.relname = 'documents'
              AND a.attname = 'doc_type'
            """
        )
    ).fetchall()
    return [r[0] for r in rows]


def ensure_phase_af_columns(engine: Engine) -> None:
    statements = [
        """
        CREATE TABLE IF NOT EXISTS refresh_token_sessions (
            jti VARCHAR(36) PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires_at TIMESTAMP NOT NULL,
            revoked_at TIMESTAMP NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_refresh_token_sessions_user_id ON refresh_token_sessions (user_id)",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS po_due_date DATE",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS planned_start_date DATE",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS document_repo_url VARCHAR(1024)",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS clickup_space_id VARCHAR(64)",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS clickup_folder_id VARCHAR(64)",
        "ALTER TABLE milestones ADD COLUMN IF NOT EXISTS start_date DATE",
        "ALTER TABLE schedule_baselines ADD COLUMN IF NOT EXISTS is_draft BOOLEAN DEFAULT FALSE",
        "ALTER TABLE weekly_reports ADD COLUMN IF NOT EXISTS pptx_path VARCHAR(512)",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS external_url VARCHAR(1024)",
        "ALTER TABLE clickup_task_cache ADD COLUMN IF NOT EXISTS time_spent_ms INTEGER",
        "ALTER TABLE clickup_task_cache ADD COLUMN IF NOT EXISTS time_estimate_ms INTEGER",
        "ALTER TABLE clickup_task_cache ADD COLUMN IF NOT EXISTS percent_complete DOUBLE PRECISION",
        "ALTER TABLE clickup_task_cache ADD COLUMN IF NOT EXISTS is_closed BOOLEAN DEFAULT FALSE",
        "ALTER TABLE clickup_task_cache ADD COLUMN IF NOT EXISTS parent_task_id VARCHAR(64)",
        "ALTER TABLE clickup_task_cache ADD COLUMN IF NOT EXISTS raw_json JSONB DEFAULT '{}'",
        "ALTER TABLE project_sph ADD COLUMN IF NOT EXISTS scope_items JSONB DEFAULT '[]'",
        "ALTER TABLE project_sph ADD COLUMN IF NOT EXISTS non_scope_items JSONB DEFAULT '[]'",
        "ALTER TABLE project_sph ADD COLUMN IF NOT EXISTS delivery_items JSONB DEFAULT '[]'",
        "ALTER TABLE project_sph ADD COLUMN IF NOT EXISTS sph_total_rupiah DOUBLE PRECISION",
        "ALTER TABLE project_sph ADD COLUMN IF NOT EXISTS sph_no VARCHAR(64)",
        "ALTER TABLE project_sph ADD COLUMN IF NOT EXISTS sph_name VARCHAR(255)",
        "ALTER TABLE project_sph ADD COLUMN IF NOT EXISTS sales_pic VARCHAR(255)",
        "ALTER TABLE project_sph ADD COLUMN IF NOT EXISTS estimated_start_date DATE",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS project_manager VARCHAR(255)",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS project_brief TEXT",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS kickoff_timeline_confirmed_at TIMESTAMP",
        "ALTER TABLE milestones ADD COLUMN IF NOT EXISTS is_payment_milestone BOOLEAN DEFAULT FALSE",
        "ALTER TABLE milestones ADD COLUMN IF NOT EXISTS module VARCHAR(128)",
        "ALTER TABLE pre_kickoff_packs ADD COLUMN IF NOT EXISTS deliverables_items JSONB DEFAULT '[]'",
        "ALTER TABLE pre_kickoff_packs ADD COLUMN IF NOT EXISTS org_vendor TEXT",
        "ALTER TABLE pre_kickoff_packs ADD COLUMN IF NOT EXISTS org_client TEXT",
        "ALTER TABLE schedule_baseline_milestones ADD COLUMN IF NOT EXISTS start_date DATE",
        "ALTER TABLE schedule_baseline_milestones ADD COLUMN IF NOT EXISTS is_payment_milestone BOOLEAN DEFAULT FALSE",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS methodology VARCHAR(32) DEFAULT 'waterfall'",
        "ALTER TABLE project_sph ADD COLUMN IF NOT EXISTS timeline_template_id INTEGER",
        "ALTER TABLE milestones ADD COLUMN IF NOT EXISTS duration_days INTEGER",
        "ALTER TABLE milestones ADD COLUMN IF NOT EXISTS item_type VARCHAR(32) DEFAULT 'milestone'",
        "ALTER TABLE milestones ADD COLUMN IF NOT EXISTS parent_id INTEGER",
        "ALTER TABLE milestones ADD COLUMN IF NOT EXISTS sort_order INTEGER DEFAULT 0",
        "ALTER TABLE schedule_baseline_milestones ADD COLUMN IF NOT EXISTS duration_days INTEGER",
        "ALTER TABLE schedule_baseline_milestones ADD COLUMN IF NOT EXISTS item_type VARCHAR(32) DEFAULT 'milestone'",
        "ALTER TABLE schedule_baseline_milestones ADD COLUMN IF NOT EXISTS parent_id INTEGER",
        "ALTER TABLE schedule_baseline_milestones ADD COLUMN IF NOT EXISTS sort_order INTEGER DEFAULT 0",
        "ALTER TABLE schedule_baseline_milestones ADD COLUMN IF NOT EXISTS row_key VARCHAR(64)",
        "ALTER TABLE schedule_baseline_milestones ADD COLUMN IF NOT EXISTS predecessor_ref VARCHAR(64)",
        "ALTER TABLE schedule_baseline_milestones ADD COLUMN IF NOT EXISTS predecessor_link_type VARCHAR(8)",
        "ALTER TABLE integration_settings ADD COLUMN IF NOT EXISTS holiday_dates JSONB DEFAULT '[]'",
        "CREATE TABLE IF NOT EXISTS timeline_templates (id SERIAL PRIMARY KEY, name VARCHAR(128) NOT NULL, methodology VARCHAR(32) NOT NULL, description TEXT, items JSONB DEFAULT '[]', is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP DEFAULT NOW())",
        "CREATE TABLE IF NOT EXISTS project_roster_entries (id SERIAL PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE, full_name VARCHAR(255) NOT NULL, email VARCHAR(255) DEFAULT '', role_label VARCHAR(128) DEFAULT '', created_at TIMESTAMP DEFAULT NOW())",
        "ALTER TABLE project_sph ADD COLUMN IF NOT EXISTS sph_client VARCHAR(255)",
        "CREATE TABLE IF NOT EXISTS project_po (project_id INTEGER PRIMARY KEY REFERENCES projects(id), po_no VARCHAR(64), po_name VARCHAR(255), updated_at TIMESTAMP DEFAULT NOW())",
        "ALTER TABLE project_po ADD COLUMN IF NOT EXISTS buyer_name VARCHAR(255)",
        "ALTER TABLE project_po ADD COLUMN IF NOT EXISTS contract_number VARCHAR(128)",
        "ALTER TABLE project_po ADD COLUMN IF NOT EXISTS quotation_reference VARCHAR(128)",
        "ALTER TABLE project_po ADD COLUMN IF NOT EXISTS po_due_date DATE",
        "ALTER TABLE project_po ADD COLUMN IF NOT EXISTS po_payment_terms JSONB DEFAULT '[]'",
        "ALTER TABLE project_po ADD COLUMN IF NOT EXISTS service_items JSONB DEFAULT '[]'",
        "ALTER TABLE project_po ADD COLUMN IF NOT EXISTS po_sub_total DOUBLE PRECISION",
        "ALTER TABLE integration_settings ADD COLUMN IF NOT EXISTS clickup_status_mappings JSONB DEFAULT '[]'",
        "ALTER TABLE integration_settings ADD COLUMN IF NOT EXISTS google_drive_service_account_json TEXT",
        "ALTER TABLE integration_settings ADD COLUMN IF NOT EXISTS google_drive_service_account_email VARCHAR(255)",
        "ALTER TABLE integration_settings ADD COLUMN IF NOT EXISTS google_drive_configured_at TIMESTAMP",
        "ALTER TABLE integration_settings ADD COLUMN IF NOT EXISTS work_weekdays JSONB DEFAULT '[0,1,2,3,4]'",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS weekly_report_anchor_weekday INTEGER DEFAULT 4",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS weekly_report_cutoff_offset_days INTEGER DEFAULT 0",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS weekly_report_first_anchor_date DATE",
        """
        DO $$ BEGIN
            ALTER TYPE progresssnapshotsource ADD VALUE 'planned_target';
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$;
        """,
        "ALTER TABLE milestones ADD COLUMN IF NOT EXISTS clickup_task_id VARCHAR(64)",
        "ALTER TABLE milestones ADD COLUMN IF NOT EXISTS clickup_list_id VARCHAR(64)",
        "ALTER TABLE clickup_task_cache ADD COLUMN IF NOT EXISTS clickup_list_id VARCHAR(64)",
        "ALTER TABLE clickup_task_cache ADD COLUMN IF NOT EXISTS milestone_id INTEGER",
        """
        CREATE TABLE IF NOT EXISTS project_change_requests (
            id SERIAL PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            cr_no VARCHAR(32) NOT NULL,
            title VARCHAR(255) NOT NULL,
            background TEXT,
            scope_change TEXT,
            schedule_impact_days INTEGER,
            cost_impact_rupiah DOUBLE PRECISION,
            priority VARCHAR(16) DEFAULT 'medium',
            status VARCHAR(32) DEFAULT 'draft',
            requested_by_id INTEGER NOT NULL REFERENCES users(id),
            submitted_at TIMESTAMP,
            decided_by_id INTEGER REFERENCES users(id),
            decided_at TIMESTAMP,
            decision_comment TEXT,
            implemented_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
        """,
        "ALTER TABLE projects ALTER COLUMN weekly_report_cutoff_offset_days SET DEFAULT 6",
        "UPDATE projects SET weekly_report_cutoff_offset_days = 6 WHERE weekly_report_cutoff_offset_days = 0",
        """
        CREATE TABLE IF NOT EXISTS milestone_predecessors (
            id SERIAL PRIMARY KEY,
            milestone_row_id INTEGER NOT NULL REFERENCES schedule_baseline_milestones(id) ON DELETE CASCADE,
            predecessor_ref VARCHAR(64) NOT NULL,
            link_type VARCHAR(8) NOT NULL DEFAULT 'FS',
            lag_days INTEGER NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_milestone_predecessors_row ON milestone_predecessors (milestone_row_id)",
        "ALTER TABLE schedule_baseline_milestones ADD COLUMN IF NOT EXISTS schedule_driver VARCHAR(32)",
        """
        CREATE TABLE IF NOT EXISTS milestone_live_predecessors (
            id SERIAL PRIMARY KEY,
            milestone_id INTEGER NOT NULL REFERENCES milestones(id) ON DELETE CASCADE,
            predecessor_ref VARCHAR(64) NOT NULL,
            link_type VARCHAR(8) NOT NULL DEFAULT 'FS',
            lag_days INTEGER NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_milestone_live_pred ON milestone_live_predecessors (milestone_id)",
        """
        CREATE TABLE IF NOT EXISTS timeline_editor_states (
            project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
            start_date DATE,
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS timeline_editor_rows (
            id SERIAL PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            name VARCHAR(255) NOT NULL,
            start_date DATE,
            target_date DATE,
            weight_pct DOUBLE PRECISION NOT NULL DEFAULT 0,
            duration_days INTEGER,
            item_type timelineitemtype NOT NULL DEFAULT 'phase',
            parent_id INTEGER REFERENCES timeline_editor_rows(id),
            sort_order INTEGER NOT NULL DEFAULT 0,
            row_key VARCHAR(64),
            predecessor_ref VARCHAR(64),
            predecessor_link_type VARCHAR(8),
            schedule_driver VARCHAR(32)
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_timeline_editor_rows_project ON timeline_editor_rows (project_id)",
        """
        CREATE TABLE IF NOT EXISTS timeline_editor_predecessors (
            id SERIAL PRIMARY KEY,
            editor_row_id INTEGER NOT NULL REFERENCES timeline_editor_rows(id) ON DELETE CASCADE,
            predecessor_ref VARCHAR(64) NOT NULL,
            link_type VARCHAR(8) NOT NULL DEFAULT 'FS',
            lag_days INTEGER NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_timeline_editor_pred_row ON timeline_editor_predecessors (editor_row_id)",
    ]
    enum_values = ["phase", "subtask"]
    doc_type_values = [
        "po",
        "sph",
        "pre_kickoff_deck",
        "kickoff_deck",
        "mom",
        "progress_report",
        "draft_bast",
        "other",
    ]
    doc_enums: list[str] = []
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
        doc_enums = _document_type_enum_names(conn)
        conn.execute(
            text(
                """
                UPDATE milestones SET item_type = 'phase'
                WHERE parent_id IS NULL AND item_type IN ('milestone')
                """
            )
        )
        conn.execute(
            text(
                """
                UPDATE schedule_baseline_milestones SET item_type = 'phase'
                WHERE parent_id IS NULL AND item_type IN ('milestone')
                """
            )
        )

    _add_enum_values_autocommit(engine, "timelineitemtype", enum_values)
    if not doc_enums:
        doc_enums = ["documenttype", "document_type"]
    for type_name in doc_enums:
        _add_enum_values_autocommit(engine, type_name, doc_type_values)
