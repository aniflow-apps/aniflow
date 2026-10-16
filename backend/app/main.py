"""AniFlow API entry point.

Hardening applied here:
- pending migrations are applied at startup (AUTO_MIGRATE, idempotent)
- unhandled exceptions become clean JSON 500s without leaking internals
- basic security headers on every response
"""
import mimetypes
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from backend.app.api.router import api_router
from backend.app.core.config import get_settings
from backend.app.db.calendar_store import CalendarStoreBlocked
from backend.app.core.logging import configure_logging, for_component, reconfigure_logging
from backend.app.core.version import app_version, build_string
from backend.app.worker.runtime import start_worker, stop_worker

settings = get_settings()
logger = for_component("Main")


def _run_migrations() -> None:
    if not settings.auto_migrate:
        return
    try:
        from alembic import command
        from alembic.config import Config

        backend_dir = Path(__file__).resolve().parents[1]
        config = Config(str(backend_dir / "alembic.ini"))
        config.set_main_option("script_location", str(backend_dir / "alembic"))
        command.upgrade(config, "head")
        logger.info("database migrations applied")

        # Settings moved out of the database into an editable file; bring
        # whatever an older install stored across, once.
        from backend.app.db.session import SessionLocal
        from backend.app.services.app_settings import (
            migrate_settings_from_database,
            repair_config_file,
        )

        session = SessionLocal()
        try:
            migrate_settings_from_database(session)
            # A setting that cannot be used as written is corrected in the
            # file, not only on the way past it (build 133).
            repair_config_file()
            # And an AniFlow that already has an account has already been set
            # up, whatever the new wizard would like to ask it (build 137).
            from backend.app.services.setup import adopt_existing_install

            adopt_existing_install(session)
        except Exception:  # noqa: BLE001 - never block startup on this
            logger.exception("settings import from the database failed")
        finally:
            session.close()
    except Exception:  # noqa: BLE001 - startup must not crash on migration issues
        logger.exception("automatic migration failed; continuing startup")


def _check_import_root() -> None:
    """In Shoko Server mode, say at boot whether the hand-over folder works.

    Two ways it does not, and both are silent until the first download
    finishes: the volume was never mounted, so files would go into the
    container's own ``/import`` and disappear on the next recreate; or it is
    mounted but not writable by the user the container runs as, which is the
    usual outcome of a host folder owned by somebody else.
    """
    from backend.app.core.mode import is_shoko_mode

    if not is_shoko_mode():
        return
    root = settings.import_root
    if root is None:
        logger.error(
            "Shoko Server mode needs an import folder, but IMPORT_ROOT is empty"
        )
        return
    if not root.is_dir():
        logger.error(
            "the import folder %s does not exist - mount it in docker-compose.yml "
            "(- /your/path:%s), or finished downloads have nowhere to go",
            root, root,
        )
        return
    if not os.access(root, os.W_OK):
        logger.error(
            "the import folder %s is not writable by this container (uid %s) - "
            "check the owner of the host folder it is mounted from",
            root, getattr(os, "getuid", lambda: "?")(),
        )
        return
    logger.info("import folder ready: %s", root)


def _load_config_folders() -> None:
    """Create the configuration folders and mirror them into the database.

    Doing it at startup means a fresh install with a populated config volume
    comes up already configured - which is the whole point of the folders:
    wiping the database costs you no settings.
    """
    from backend.app.db.session import SessionLocal
    from backend.app.services.config_store import ensure_folders
    from backend.app.services.config_sync import sync_from_files

    ensure_folders()
    session = SessionLocal()
    try:
        changed = sync_from_files(session)
        if changed:
            logger.info("configuration folders applied (%s change(s))", changed)
    except Exception:  # noqa: BLE001 - a bad file must not stop the app
        logger.exception("configuration folders could not be read")
    finally:
        session.close()


def _verify_mode() -> None:
    """Adopt the mode this library was built under, and say which it is.

    It used to stop the app when ``ANIFLOW_MODE`` disagreed with the database,
    because the variable was the only place the choice could be made. The
    install wizard makes it now (build 137), so the recorded mode wins and a
    stale variable is a warning inside ``verify_mode`` rather than a refusal
    to boot - the library keeps being written exactly as it always was.
    """
    from backend.app.core.mode import verify_mode
    from backend.app.db.session import SessionLocal

    session = SessionLocal()
    try:
        mode = verify_mode(session)
        # First line worth reading in a container log: which copy is this.
        logger.info("%s", build_string())
        logger.info("storage mode: %s", mode.value)
    finally:
        session.close()


def _announce_setup_code() -> None:
    """Put a setup code in the log when AniFlow has no account yet.

    Printed at every start while that is true, because the log line is the
    only way in and a container that restarted an hour ago has an expired
    code. Announced so no log level can hide it.
    """
    from backend.app.db.session import SessionLocal
    from backend.app.services.auth import current_user, issue_token

    session = SessionLocal()
    try:
        if current_user(session) is None:
            issue_token(session, "setup")
            session.commit()
    except Exception as exc:  # noqa: BLE001 - never take the app down for this
        logger.error("could not issue a setup code: %s", exc)
    finally:
        session.close()


def _seed_libraries() -> None:
    """The built-in libraries exist, and every anime has one (build 192)."""
    from backend.app.db.session import SessionLocal
    from backend.app.services import libraries

    session = SessionLocal()
    try:
        libraries.ensure_system(session)
        # A watchlist that predates the adult flag is right the first time it is opened.
        from backend.app.services import adult_rule

        adult_rule.backfill(session)
    except Exception as exc:  # noqa: BLE001 - never take the app down for this
        logger.error("could not set up the libraries: %s", exc)
    finally:
        session.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Configure logging AFTER uvicorn has initialized
    configure_logging(settings)

    _run_migrations()
    _verify_mode()
    _check_import_root()
    _load_config_folders()
    _seed_libraries()
    # Re-assert logging: anything that calls logging.config.fileConfig or
    # dictConfig can silently disable every existing logger and replace the
    # root handlers. Alembic's env.py used to do exactly that, leaving the app
    # running with no output at all. This is idempotent and cheap.
    reconfigure_logging(settings)

    _announce_setup_code()

    await start_worker()
    yield
    await stop_worker()


app = FastAPI(title="AniFlow", version=app_version(), lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


@app.middleware("http")
async def tv_cors(request: Request, call_next):
    """Any origin may call the television API (build 181).

    A Tizen or Android TV app has no fixed web origin - a packaged Tizen app
    is loaded from the TV itself - so the allow-list above cannot name it. That
    is safe here and only here: the API is guarded by a bearer token, not by
    a cookie (``allow_credentials`` stays off), so a foreign page gains nothing
    by being allowed to *ask*. Added after the middleware above so that it
    answers first, including the preflight the allow-list would refuse.
    """
    if not request.url.path.startswith("/api/tv/"):
        return await call_next(request)
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Authorization, Content-Type",
        "Access-Control-Max-Age": "600",
    }
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=headers)
    response = await call_next(request)
    for name, value in headers.items():
        response.headers[name] = value
    return response


#: The last unhandled error, and how many times it has repeated. One entry,
#: because the failure being guarded against is one thing going wrong very
#: fast rather than many different things.
_last_failure: tuple[str, float, int] = ("", 0.0, 0)

#: How long the same unhandled error is counted instead of logged in full.
REPEAT_WINDOW_SECONDS = 60.0


@app.exception_handler(CalendarStoreBlocked)
async def calendar_store_blocked_handler(_: Request, exc: CalendarStoreBlocked) -> JSONResponse:
    """The calendar database was left alone, and here is why (build 141).

    Up to build 140 a file this version could not read was deleted, so this
    state did not exist. It does now, on purpose: the file holds canonical ids
    that AniFlow TV will be written against, the evidence behind every proven
    link, and the record of seasons providers have stopped reporting - none of
    which a download brings back.

    503 rather than 500: nothing is broken in AniFlow, the calendar is
    unavailable until somebody decides what to do with the file, and the
    detail is the sentence that tells them.
    """
    logger.warning("calendar database unavailable: %s", exc)
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    """Report it, and refuse to report it four hundred times (build 84).

    A hundred-line traceback per request is the right amount of detail for one
    failure and a denial of service for a repeating one. Build 83 exhausted
    the database connection pool, which made every request fail for thirty
    seconds at a time: 433 identical tracebacks, twenty megabytes of log in
    ten minutes, and the actual first cause pushed out of the file by its own
    consequences.

    So the first one is logged in full and the repeats are counted. The count
    is what says "this is a flood, not an incident" - which is the thing worth
    knowing and the thing the flood itself was hiding.
    """
    global _last_failure
    signature = f"{type(exc).__name__}: {exc}"
    previous, when, count = _last_failure
    now = time.monotonic()
    if signature == previous and (now - when) < REPEAT_WINDOW_SECONDS:
        _last_failure = (signature, when, count + 1)
        if count + 1 in (10, 50, 100) or (count + 1) % 500 == 0:
            logger.error("the same error has now happened %s times: %s", count + 1, signature)
    else:
        if count > 1:
            logger.error("the previous error repeated %s times: %s", count, previous)
        _last_failure = (signature, now, 1)
        logger.exception("unhandled error: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.include_router(api_router)

# Content types the host registry may not know. Windows has no entry for
# woff2 at all, so the bundled Sora files were served as text/plain - browsers
# tolerate that for fonts, but the type should not depend on the machine the
# server happens to run on.
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/woff", ".woff")
mimetypes.add_type("application/javascript", ".mjs")

frontend_dir = Path(__file__).resolve().parents[2] / "frontend_dist"


def resolve_frontend_file(root: Path, path: str) -> Path | None:
    """The built file this request asks for, or None to fall back to the SPA.

    Vite copies ``public/`` to the root of ``dist``, so the logo, the favicons
    and robots.txt live *beside* index.html rather than under /assets. Without
    this, the catch-all below answered those requests with index.html and a
    200, and the browser dutifully failed to decode HTML as a PNG.

    Only regular files inside the build directory are served. The resolved
    path is compared against the resolved root, so ``../`` and a symlink out
    of the tree both fall through to index.html instead of escaping.
    """
    if not path or path.endswith("/"):
        return None
    try:
        candidate = (root / path).resolve()
        candidate.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return candidate if candidate.is_file() else None


if frontend_dir.exists():
    app.mount("/assets", StaticFiles(directory=frontend_dir / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str) -> FileResponse:
        # A real build artefact wins; everything else is a client-side route
        # and gets the SPA shell.
        target = resolve_frontend_file(frontend_dir, path)
        return FileResponse(target or frontend_dir / "index.html")
