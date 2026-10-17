from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.core.logging import for_component
from backend.app.core.mode import current_mode
from backend.app.core.version import app_version, build_number, build_string
from backend.app.db.session import get_db
from backend.app.services.app_mode import current_mode as app_mode, needs_conversion
from backend.app.db.session import SessionLocal, engine
from backend.app.schemas.system import AppInfoResponse, DebugLogResponse, HealthResponse
from backend.app.worker.runtime import worker_state

router = APIRouter()
_debug_logger = for_component("Debug")


def _probe_qbittorrent() -> str:
    try:
        from backend.app.integrations.torrent_client import client_for
        from backend.app.services.app_settings import load_app_settings

        try:
            with SessionLocal() as session:
                cfg = load_app_settings(session)
        except Exception:  # noqa: BLE001 - fresh database without settings table
            return "disabled"
        if not cfg.qb_url:
            return "disabled"
        client = client_for(cfg.torrent_client, cfg.qb_url, cfg.qb_username, cfg.qb_password, timeout=3)
        try:
            client.version()
        finally:
            client.close()
        return "ok"
    except Exception:  # noqa: BLE001 - health must never raise
        return "unreachable"


def _probe_metadata() -> str:
    """What the last metadata question produced.

    Asks nobody anything: every search, every calendar page and the worker's
    own tick record their outcome, so this is a lookup. Deliberately so - see
    services/metadata.refresh for why the probe is not on this path.
    """
    try:
        from backend.app.services import metadata

        return metadata.status().state
    except Exception:  # noqa: BLE001 - health must never raise
        return "unknown"


def _shoko_state() -> str:
    """What the last question to Shoko Server produced. Asks nothing.

    Not a probe: this endpoint already makes two live calls and Docker
    restarts the container when it is slow. See services/shoko_status.py.
    """
    try:
        from backend.app.services import shoko_status

        if not shoko_status.configured():
            return "disabled"
        state = shoko_status.status().state
        # Configured, but the last thing anybody learned was from before it
        # was configured. "Not checked yet" is the honest answer for the few
        # seconds until the worker's next tick asks.
        return "unknown" if state == "disabled" else state
    except Exception:  # noqa: BLE001 - health must never raise
        return "unknown"


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    database = "ok"
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        database = "unavailable"

    return HealthResponse(
        status="ok" if database == "ok" else "degraded",
        database=database,
        worker="running" if worker_state.running else "stopped",
        qbittorrent=_probe_qbittorrent(),
        metadata=_probe_metadata(),
        shoko=_shoko_state(),
        timestamp=datetime.now(UTC),
    )


class CheckResultResponse(BaseModel):
    id: str
    group: str
    #: ok | warning | error | skipped
    state: str
    code: str
    values: dict[str, str] = {}
    #: A page address to fix it at (``#/settings/downloads``); null when there is nothing to open.
    fix: str | None = None
    subject: str | None = None
    #: Asked somebody else over the network, so only a person pressing the button runs it.
    live: bool = False


class ChecksResponse(BaseModel):
    """The checklist (build 232): everything AniFlow needs to do its job, tested one thing at a time."""

    checks: list[CheckResultResponse]
    errors: int
    warnings: int
    ran_at: str


@router.get("/checks", response_model=ChecksResponse)
def checks(live: bool = False, db: Session = Depends(get_db)) -> ChecksResponse:
    """Run every check. Some ask the torrent client, so this takes as long as that answers (five seconds at most).

    ``live=true`` also asks each indexer and the metadata provider - one request each, spaced; the page's button
    does, the background refresh of the sidebar number does not.
    """
    from dataclasses import asdict

    from backend.app.services.system_checks import run_checks, summarize

    results = run_checks(db, live=live)
    return ChecksResponse(checks=[CheckResultResponse(**asdict(item)) for item in results], **summarize(results))


@router.get("/info", response_model=AppInfoResponse)
def info(db: Session = Depends(get_db)) -> AppInfoResponse:
    from backend.app.services.app_settings import load_app_settings
    from backend.app.services.clock import now_local, timezone_name

    settings = get_settings()
    local = now_local()
    return AppInfoResponse(
        version=app_version(),
        build=build_number(),
        build_string=build_string(),
        environment=settings.app_env,
        timezone=timezone_name(),
        # 0 = Monday, matching `Anime.airs_weekday`.
        today_weekday=local.weekday(),
        server_time=local.strftime("%d.%m.%Y %H:%M"),
        mode=current_mode().value,
        config_dir=str(settings.config_dir or "/config"),
        app_mode=app_mode(db).value,
        needs_conversion=len(needs_conversion(db)),
        torrent_client_configured=bool(load_app_settings(db).qb_url),
    )


@router.get("/about")
def about(db: Session = Depends(get_db)) -> dict:
    """Which build this is and how it is set up (build 262): the Settings page's About panel."""
    from backend.app.services import diagnostics

    return diagnostics.about(db)


@router.get("/diagnostics")
def diagnostics_bundle(db: Session = Depends(get_db)) -> Response:
    """One zip for a bug report (build 262): the settings with secrets removed, the Checks, the libraries, the
    schedule, counts of the database, the last Activity entries and AniFlow's own last log lines.

    Not the database and not the AniDB export. See ``services/diagnostics.py`` for what it holds and why.
    """
    from backend.app.services import diagnostics

    return Response(
        content=diagnostics.build_bundle(db),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{diagnostics.bundle_name()}"'},
    )


@router.post("/debug-log", response_model=DebugLogResponse)
def debug_log() -> DebugLogResponse:
    """Emit one line at every level, so log filtering can be checked at a glance.

    Sending a single error only proved that logging was wired up: an error
    comes through at every setting. One line per level turns the button into
    the actual test - set WARNING, press it, and if INFO and DEBUG are absent
    while WARNING and ERROR are there, the filtering does what it says.

    Emitted newest-level-last so the four sit together in the order they are
    listed on screen.
    """
    sent = []
    for level, note in (
        ("DEBUG", "the noisiest level - visible only at DEBUG"),
        ("INFO", "routine progress - hidden from WARNING upwards"),
        ("WARNING", "something worth knowing - hidden only at ERROR"),
        ("ERROR", "a real failure - visible at every level"),
    ):
        getattr(_debug_logger, level.lower())("logging test [%s]: %s", level, note)
        sent.append(level)
    return DebugLogResponse(
        ok=True,
        message=(
            f"Sent one line at each level ({', '.join(sent)}). "
            "Whichever are missing from the log are the ones being filtered."
        ),
    )


