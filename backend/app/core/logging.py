"""Centralized logging system for AniFlow.

Provides component-based loggers with filtering, structured context,
file rotation, and per-component level configuration.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import threading
import time
from contextvars import ContextVar
from datetime import datetime
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from backend.app.core.clock import resolve_timezone
from backend.app.core.config import Settings, get_settings
from backend.app.core.log_buffer import RingBufferHandler

if TYPE_CHECKING:  # annotation only - importing a service here would be a cycle
    from backend.app.services.app_settings import AppSettings

# Context variable for structured context (operation IDs, etc.)
_log_context: ContextVar[dict[str, Any]] = ContextVar("log_context", default={})

# Level constants
LOG_LEVELS = {
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}

# Default format: 27.08.2026 21:10:32 [Search] [INFO] Message
DEFAULT_FORMAT = "%(asctime)s [%(component)s] [%(levelname)s] %(message)s"
DEFAULT_DATEFMT = "%d.%m.%Y %H:%M:%S"


@dataclass
class LoggingConfig:
    """Resolved logging configuration from settings."""
    level: int
    file_path: str | None
    file_max_bytes: int
    file_backup_count: int
    enabled_components: set[str] | None  # None = all enabled
    component_levels: dict[str, int]  # component -> level overrides
    #: The timezone name timestamps are rendered in. Resolved here so the
    #: console and the file handler cannot disagree, and so the precedence
    #: (setting, then TZ, then UTC) is testable in one place.
    timezone: str = "UTC"


def resolve_log_timezone(settings: AppSettings) -> str:
    """Which timezone timestamps use: the setting, then TZ, then UTC.

    The empty setting is the normal case - it means "follow the container" -
    so it must fall through to TZ rather than being treated as UTC. Getting
    that wrong is what left the log two hours behind the NAS.
    """
    configured = (getattr(settings, "timezone", "") or "").strip()
    return configured or (os.environ.get("TZ") or "").strip() or "UTC"


def parse_logging_config(settings: AppSettings, env: Settings | None = None) -> LoggingConfig:
    """Resolve logging configuration.

    Level and component filtering are user settings (the config file). *Where*
    the log is written is not: it is deployment plumbing, fixed by the image at
    ``/config/log/aniflow.log``, so it cannot be pointed at a path that does not
    exist inside the container.
    """
    # Global level
    level_name = settings.log_level.upper()
    level = LOG_LEVELS.get(level_name, logging.INFO)

    # File settings come from the environment, not from the editable settings.
    env = env or get_settings()
    file_path = env.log_file
    file_max_bytes = env.log_file_max_bytes
    file_backup_count = env.log_file_backup_count

    # Component filtering
    enabled_components = None
    if settings.log_components:
        enabled_components = {c.strip() for c in settings.log_components.split(",") if c.strip()}

    # Per-component level overrides
    component_levels = {}
    if settings.log_component_levels:
        try:
            raw = json.loads(settings.log_component_levels)
            for comp, level_name in raw.items():
                lvl = LOG_LEVELS.get(str(level_name).upper())
                if lvl is not None:
                    component_levels[comp] = lvl
        except (json.JSONDecodeError, ValueError):
            pass  # Invalid config - ignore

    return LoggingConfig(
        timezone=resolve_log_timezone(settings),
        level=level,
        file_path=file_path,
        file_max_bytes=file_max_bytes,
        file_backup_count=file_backup_count,
        enabled_components=enabled_components,
        component_levels=component_levels,
    )


#: Libraries whose INFO level is a running commentary rather than news.
#: httpx logs every request; httpcore and urllib3 are noisier still.
CHATTY_LIBRARIES = ("httpx", "httpcore", "urllib3")


class AccessNoiseFilter(logging.Filter):
    """Drop the access lines that only say a page asked for something and got it.

    A successful ``GET`` or ``HEAD`` carries no news: the UI polls, reloads and
    refreshes its badges all day, and every one of those printed a line - on the
    NAS, seven per page load - burying the lines that matter. This used to be a
    list of the paths the UI polled (build 0.0.56 onward), which was always one
    page behind the UI, so it is the rule now: **a read that worked is silent.**

    What still speaks: anything that is not a read (a POST that starts a
    download, a DELETE), and any request that fails, because a page that starts
    returning errors is exactly what someone reading the log needs. At debug
    level everything is shown again, for the day the full trace is the point.
    Anything that does not look like a uvicorn access record is passed
    untouched.
    """

    READS = frozenset({"GET", "HEAD"})

    def __init__(self, show_all: bool = False) -> None:
        super().__init__()
        self.show_all = show_all

    def filter(self, record: logging.LogRecord) -> bool:
        if self.show_all:
            return True
        args = record.args
        # uvicorn logs: (client_addr, method, full_path, http_version, status).
        if not isinstance(args, tuple) or len(args) < 5:
            return True
        try:
            status = int(args[4])  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return True
        if status >= 400:
            return True
        return str(args[1]).upper() not in self.READS


class ComponentFilter(logging.Filter):
    """Filter log records by component and per-component level."""

    def __init__(self, config: LoggingConfig):
        super().__init__()
        self.config = config

    def filter(self, record: logging.LogRecord) -> bool:
        # Marked by announce(): a line that exists to be read by the person
        # running the deployment, and that no setting may suppress. Checked
        # first so neither the level nor the component list applies.
        if getattr(record, "aniflow_announce", False):
            return True

        # Get component from record (added by ComponentLogger)
        component = getattr(record, "component", None)

        # Set default component from logger name if not present
        if component is None:
            component = record.name
            record.component = component

        # Component filtering
        if self.config.enabled_components is not None:
            if component not in self.config.enabled_components:
                return False

        # Determine effective level for this component
        effective_level = self.config.level
        if component and component in self.config.component_levels:
            effective_level = self.config.component_levels[component]

        # Filter by level: allow if record level >= effective level
        if record.levelno < effective_level:
            return False

        return True


class ComponentLogger:
    """Logger for a specific component with structured context support."""

    def __init__(self, component: str, base_logger: logging.Logger):
        self.component = component
        self._logger = base_logger

    def _log(self, level: int, msg: str, *args: Any, **kwargs: Any) -> None:
        """Log with component and context."""
        # Merge context
        context = _log_context.get()
        extra = {"component": self.component}
        if context:
            # Add context as structured data
            for key, value in context.items():
                extra[key] = value
        self._logger.log(level, msg, *args, extra=extra, **kwargs)

    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._log(logging.DEBUG, msg, *args, **kwargs)

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._log(logging.INFO, msg, *args, **kwargs)

    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._log(logging.WARNING, msg, *args, **kwargs)

    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._log(logging.ERROR, msg, *args, **kwargs)

    def exception(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._log(logging.ERROR, msg, *args, exc_info=True, **kwargs)

    def bind(self, **context: Any) -> "ComponentLogger":
        """Return a new logger with additional context bound."""
        # Create a new logger that adds the context
        class BoundLogger:
            def __init__(self, logger: "ComponentLogger", bound_context: dict):
                self._logger = logger
                self._bound_context = bound_context

            def _log(self, level: int, msg: str, *args: Any, **kwargs: Any) -> None:
                context = _log_context.get()
                extra = {"component": self._logger.component}
                extra.update(context)
                extra.update(self._bound_context)
                self._logger._logger.log(level, msg, *args, extra=extra, **kwargs)

            def debug(self, msg: str, *args: Any, **kwargs: Any) -> None:
                self._log(logging.DEBUG, msg, *args, **kwargs)

            def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
                self._log(logging.INFO, msg, *args, **kwargs)

            def warning(self, msg: str, *args: Any, **kwargs: Any) -> None:
                self._log(logging.WARNING, msg, *args, **kwargs)

            def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
                self._log(logging.ERROR, msg, *args, **kwargs)

            def exception(self, msg: str, *args: Any, **kwargs: Any) -> None:
                self._log(logging.ERROR, msg, *args, exc_info=True, **kwargs)

        return BoundLogger(self, context)


def _zoned_formatter(timezone_name: str) -> logging.Formatter:
    """A formatter whose timestamps are in the configured timezone.

    `logging.Formatter` calls `time.localtime`, which is the container's TZ and
    nothing else - so a `timezone` set in aniflow.yaml would have been visible
    everywhere in the UI while the log stayed two hours off. The converter is
    bound once per configure() call, not resolved per record.
    """
    zone = resolve_timezone(timezone_name)

    def converter(timestamp: float | None) -> time.struct_time:
        moment = datetime.fromtimestamp(timestamp or 0, tz=zone)
        return moment.timetuple()

    formatter = logging.Formatter(fmt=DEFAULT_FORMAT, datefmt=DEFAULT_DATEFMT)
    formatter.converter = converter  # type: ignore[assignment]
    return formatter


class LoggingSystem:
    """Centralized logging system for AniFlow."""

    _instance: "LoggingSystem | None" = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._config: LoggingConfig | None = None
        self._root_logger = logging.getLogger()
        self._component_loggers: dict[str, ComponentLogger] = {}
        self._base_logger = logging.getLogger("aniflow")
        self._file_handler: logging.handlers.RotatingFileHandler | None = None
        self._component_filter: ComponentFilter | None = None
        # The last INFO-and-above lines, whatever the console level is (build 262): what the diagnostics bundle holds.
        self.buffer = RingBufferHandler()
        self._initialized = True

    @property
    def is_configured(self) -> bool:
        """Whether configure() has run - i.e. AniFlow owns logging now."""
        return self._config is not None

    def configure(self, settings: AppSettings, env: Settings | None = None) -> None:
        """Take ownership of logging. Safe to call repeatedly.

        Every call rebuilds the handler set from scratch, including our own:
        keeping the previous ones would attach a second console and file
        handler on each call, so every line would be emitted twice (and
        written to the log file twice).
        """
        self._config = parse_logging_config(settings, env)
        formatter = _zoned_formatter(self._config.timezone)
        root_logger = logging.getLogger()

        # Drop everything currently on the root logger - foreign handlers
        # (uvicorn/alembic defaults) and any of ours from an earlier call.
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
        if self._file_handler is not None:
            self._file_handler.close()
            self._file_handler = None

        self._component_filter = ComponentFilter(self._config)

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        console_handler._aniflow_formatter = True  # mark as ours
        console_handler.addFilter(self._component_filter)
        root_logger.addHandler(console_handler)

        # Deliberately without the component filter: the console may be set to ERROR or to one component, and the
        # buffer exists for the day somebody needs what that hid.
        self.buffer.setFormatter(formatter)
        root_logger.addHandler(self.buffer)

        # The filters carry the effective levels, so the root must pass all.
        root_logger.setLevel(logging.DEBUG)

        # The access logger is uvicorn's, not ours, so the filter goes on the
        # logger itself. Re-attaching on every configure() would stack copies.
        access_logger = logging.getLogger("uvicorn.access")
        for existing in [f for f in access_logger.filters if isinstance(f, AccessNoiseFilter)]:
            access_logger.removeFilter(existing)
        access_logger.addFilter(AccessNoiseFilter(show_all=self._config.level <= logging.DEBUG))

        self._quiet_chatty_libraries()

        if self._config.file_path:
            self._setup_file_handler(formatter)

    def _quiet_chatty_libraries(self) -> None:
        """Third-party loggers that report routine success at INFO.

        httpx writes a line per request, and AniFlow polls the torrent client
        every thirty seconds - three requests, three lines, all "200 OK". At
        DEBUG they are left alone, because "what did it actually ask for?" is
        usually the question DEBUG was turned on to answer.
        """
        verbose = self._config.level <= logging.DEBUG
        for name in CHATTY_LIBRARIES:
            logging.getLogger(name).setLevel(logging.DEBUG if verbose else logging.WARNING)

    def _setup_file_handler(self, formatter: logging.Formatter | None = None) -> None:
        """Attach the rotating file handler for ``LOG_FILE``.

        Failures are reported on the console instead of being swallowed: a
        misconfigured ``LOG_FILE`` used to disable file logging silently, which
        is indistinguishable from "logging works, nothing happened". The most
        common cause is a *host* path in ``LOG_FILE`` - inside the container the
        folder is mounted somewhere else (``/app/log``), so the path does not
        exist and cannot be created.
        """
        if not self._config or not self._config.file_path:
            return

        path = self._config.file_path
        directory = os.path.dirname(path)
        if directory:
            try:
                os.makedirs(directory, exist_ok=True)
            except OSError as exc:
                logging.getLogger("aniflow.Logging").warning(
                    "file logging disabled: cannot create %r (%s). Inside the container "
                    "LOG_FILE must be a container path such as /app/log/aniflow.log, "
                    "not a host path.",
                    directory,
                    exc,
                    extra={"component": "Logging"},
                )
                return

        formatter = formatter or _zoned_formatter(self._config.timezone)

        try:
            self._file_handler = logging.handlers.RotatingFileHandler(
                path,
                maxBytes=self._config.file_max_bytes,
                backupCount=self._config.file_backup_count,
                encoding="utf-8",
            )
        except OSError as exc:
            logging.getLogger("aniflow.Logging").warning(
                "file logging disabled: cannot open %r (%s)",
                path,
                exc,
                extra={"component": "Logging"},
            )
            return

        self._file_handler.setFormatter(formatter)
        self._file_handler._aniflow_formatter = True
        if self._component_filter:
            self._file_handler.addFilter(self._component_filter)
        self._root_logger.addHandler(self._file_handler)
        logging.getLogger("aniflow.Logging").info(
            "file logging enabled: %s", path, extra={"component": "Logging"}
        )

    def reconfigure(self, settings: AppSettings, env: Settings | None = None) -> None:
        """Reconfigure logging with new settings."""
        self.configure(settings)

    def get_logger(self, component: str) -> ComponentLogger:
        """Get or create a component logger."""
        if component not in self._component_loggers:
            # Use a child logger of the base logger
            child = self._base_logger.getChild(component)
            self._component_loggers[component] = ComponentLogger(component, child)
        return self._component_loggers[component]

    def for_component(self, component: str) -> ComponentLogger:
        """Alias for get_logger - more explicit API."""
        return self.get_logger(component)

    def update_config(self, settings: AppSettings, env: Settings | None = None) -> None:
        """Update configuration from new settings."""
        old_config = self._config
        new_config = parse_logging_config(settings, env)

        if old_config is None:
            self.configure(settings, env)
            return

        # Root stays at DEBUG on purpose - the handler filters carry the
        # effective levels, and a root set to WARNING would drop a record
        # before a component override set to DEBUG could ever see it.
        logging.getLogger().setLevel(logging.DEBUG)

        # Update component filter
        if self._component_filter:
            self._component_filter.config = new_config

        # Update file handler if path changed
        if new_config.file_path != old_config.file_path:
            if self._file_handler:
                self._root_logger.removeHandler(self._file_handler)
                self._file_handler.close()
                self._file_handler = None
            if new_config.file_path:
                self._setup_file_handler()

        self._config = new_config

    def set_context(self, **context: Any) -> Any:
        """Set structured context for current execution context."""
        return _log_context.set(context)

    def clear_context(self, token: Any) -> None:
        """Clear context token."""
        _log_context.reset(token)


# Global instance
_logging_system = LoggingSystem()


def get_logging_system() -> LoggingSystem:
    """Get the global logging system instance."""
    return _logging_system


def logging_is_configured() -> bool:
    """Whether AniFlow has already taken ownership of logging.

    Used by ``alembic/env.py`` so applying migrations inside the running
    application does not tear the application's own logging down.
    """
    return _logging_system.is_configured


def get_logger(component: str) -> ComponentLogger:
    """Get a component logger."""
    return _logging_system.get_logger(component)


def for_component(component: str) -> ComponentLogger:
    """Get a logger for a specific component. Preferred API."""
    return _logging_system.for_component(component)


def configure_logging(settings: AppSettings, env: Settings | None = None) -> None:
    """Configure the global logging system."""
    _logging_system.configure(settings, env)


def reconfigure_logging(settings: AppSettings, env: Settings | None = None) -> None:
    """Reconfigure logging with new settings."""
    _logging_system.reconfigure(settings, env)


def set_log_context(**context: Any) -> Any:
    """Set structured context for current execution context."""
    return _logging_system.set_context(**context)


def clear_log_context(token: Any) -> None:
    """Clear context token."""
    _logging_system.clear_context(token)


def get_logging_config(settings: AppSettings) -> LoggingConfig:
    """Get parsed logging config (for testing)."""
    return parse_logging_config(settings)


def announce(component: str, message: str, *args: object) -> None:
    """Write one line that the log settings cannot filter out.

    For things that are useless if unseen - so far exactly one: the one-time
    code for creating the account or resetting the password. Deliberately
    awkward to reach for, because a second caller would start to erode the
    meaning of the log level.
    """
    logger = logging.getLogger(f"aniflow.{component}")
    logger.critical(message, *args, extra={"component": component, "aniflow_announce": True})


__all__ = [
    "announce",
    "LOG_LEVELS",
    "DEFAULT_FORMAT",
    "DEFAULT_DATEFMT",
    "AccessNoiseFilter",
    "LoggingConfig",
    "ComponentFilter",
    "ComponentLogger",
    "LoggingSystem",
    "LoggingSystem",
    "get_logging_system",
    "get_logger",
    "for_component",
    "configure_logging",
    "reconfigure_logging",
    "set_log_context",
    "clear_log_context",
    "get_logging_config",
    "parse_logging_config",
    "LoggingConfig",
    "ComponentFilter",
    "ComponentLogger",
]