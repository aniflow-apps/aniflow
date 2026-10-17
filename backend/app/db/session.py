from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import get_settings

settings = get_settings()
# The pool is sized for a page, not for a person. One screen of the calendar
# is a couple of hundred requests arriving at once, and the default five plus
# ten overflow is what turned a slow request in build 83 into an application
# that answered nothing for half a minute.
#
# The timeout matters as much as the size: thirty seconds of waiting is
# indistinguishable from a hang, and every one of those waits wrote a
# hundred-line traceback. Five seconds fails while somebody is still watching.
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {},
    pool_size=20,
    max_overflow=30,
    pool_timeout=5,
    # A connection that died while idle - a restarted database, a dropped
    # socket - is discovered and replaced instead of failing a request.
    pool_pre_ping=True,
)

# SQLite only enforces foreign keys (CASCADE / SET NULL) with this pragma,
# matching the behaviour of the PostgreSQL-compatible schema definitions.
if engine.dialect.name == "sqlite":

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
