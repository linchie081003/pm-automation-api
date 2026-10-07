import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger("pdc")

from app.database import Base, SessionLocal, engine
import app.models  # noqa: F401
from app.routers import (
    approvals,
    change_requests,
    auth,
    clickup,
    google_drive_integration,
    dashboard,
    documents,
    evaluations,
    health,
    milestones,
    permissions,
    pre_kickoff,
    project_members,
    project_tasks,
    projects,
    rebaseline_approvals,
    reminders,
    reports,
    roles,
    schedule,
    sph,
    timeline_editor,
    timeline_templates,
    project_roster,
    project_po,
    work_calendar,
    users,
)
from app.config import settings
from app.services.bootstrap import (
    backfill_project_members,
    ensure_org_defaults,
    seed_rbac,
    sync_permission_catalog,
)
from app.services.schema_migrate import ensure_phase_af_columns
from app.services.timeline_seed import ensure_default_timeline_templates

app = FastAPI(title="Project Delivery Control", version="0.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def no_store_api_responses(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-store"
    return response


app.include_router(auth.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(roles.router, prefix="/api")
app.include_router(permissions.router, prefix="/api")
app.include_router(projects.router, prefix="/api")
app.include_router(project_members.router, prefix="/api")
app.include_router(schedule.router, prefix="/api")
app.include_router(approvals.router, prefix="/api")
app.include_router(documents.router, prefix="/api")
app.include_router(dashboard.router, prefix="/api")
app.include_router(reports.router, prefix="/api")
app.include_router(health.router, prefix="/api")
app.include_router(milestones.router, prefix="/api")
app.include_router(clickup.router, prefix="/api")
app.include_router(google_drive_integration.router, prefix="/api")
app.include_router(sph.router, prefix="/api")
app.include_router(timeline_editor.router, prefix="/api")
app.include_router(timeline_templates.router, prefix="/api")
app.include_router(project_roster.router, prefix="/api")
app.include_router(project_po.router, prefix="/api")
app.include_router(work_calendar.router, prefix="/api")
app.include_router(pre_kickoff.router, prefix="/api")
app.include_router(change_requests.router, prefix="/api")
app.include_router(project_tasks.router, prefix="/api")
app.include_router(reminders.router, prefix="/api")
app.include_router(rebaseline_approvals.router, prefix="/api")
app.include_router(rebaseline_approvals.global_router, prefix="/api")
app.include_router(evaluations.router, prefix="/api")


@app.on_event("startup")
def on_startup():
    if settings.app_env != "production" and settings.jwt_secret_is_weak:
        logger.warning(
            "JWT_SECRET is weak or default — set a random 32+ char secret in .env "
            "(required when APP_ENV=production)."
        )
    if "*" in settings.cors_origins:
        raise RuntimeError("Invalid CORS_ORIGINS: wildcard not allowed with credentials")
    Base.metadata.create_all(bind=engine)
    ensure_phase_af_columns(engine)
    if not settings.skip_template_verify:
        try:
            import subprocess
            import sys
            from pathlib import Path

            backend = Path(__file__).resolve().parent.parent
            subprocess.run(
                [sys.executable, "-m", "scripts.ensure_min_templates"],
                cwd=str(backend),
                check=False,
                timeout=30,
            )
        except Exception:
            pass
    db = SessionLocal()
    try:
        seed_rbac(db)
        sync_permission_catalog(db)
        ensure_org_defaults(db)
        backfill_project_members(db)
        if settings.seed_timeline_templates_on_startup:
            ensure_default_timeline_templates(db)
    finally:
        db.close()


@app.get("/api/health")
def health_check():
    return {"status": "ok"}
