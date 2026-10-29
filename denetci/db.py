from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import data_dir


class Base(DeclarativeBase):
    pass


_engine = None
_Session = None


def get_engine():
    global _engine, _Session
    if _engine is None:
        url = f"sqlite:///{data_dir() / 'denetci.db'}"
        _engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(_engine, "connect")
        def _pragmas(dbapi_conn, _record):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
            dbapi_conn.execute("PRAGMA journal_mode=WAL")

        _Session = sessionmaker(_engine, expire_on_commit=False)
    return _engine


def SessionLocal():
    get_engine()
    return _Session()


# Var olan veritabanlarına sonradan eklenen kolonlar (basit, yalnızca ekleme yapan geçiş).
_ADDED_COLUMNS = {
    "import_batches": {"profile": "VARCHAR(32)"},
    "analysis_runs": {"source_batches": "JSON"},
    "clients": {"vkn": "VARCHAR(16)", "vergi_dairesi": "VARCHAR(128)", "luca_kodu": "VARCHAR(64)"},
    "journal_lines": {"madde_no": "VARCHAR(32)", "fis_tipi": "VARCHAR(32)", "evrak_no": "VARCHAR(64)"},
}


def init_db():
    from sqlalchemy import inspect, text

    from . import models  # noqa: F401  (tabloları kaydeder)

    engine = get_engine()
    Base.metadata.create_all(engine)
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, cols in _ADDED_COLUMNS.items():
            existing = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def reset_engine():
    """Testlerde veri klasörü değişince bağlantıyı sıfırlar."""
    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _Session = None
