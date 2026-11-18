from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.app.config import get_settings

settings = get_settings()


def create_database_engine(database_url: str):
    engine_options = {'pool_pre_ping': True}
    if database_url.startswith('sqlite:'):
        engine_options['connect_args'] = {'check_same_thread': False}
        if database_url != 'sqlite:///:memory:':
            database_path = database_url.removeprefix('sqlite:///')
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database_url, **engine_options)
    if database_url.startswith('sqlite:'):
        event.listen(engine, 'connect', _enable_sqlite_foreign_keys)
    return engine


def _enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute('PRAGMA foreign_keys=ON')
    cursor.close()


engine = create_database_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()