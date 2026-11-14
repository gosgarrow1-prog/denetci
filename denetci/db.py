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
    "periods": {"devreden_kdv": "INTEGER", "durum": "VARCHAR(16)", "kesin_at": "DATETIME", "kesin_kullanici": "VARCHAR(64)",
                "kesin_run_id": "INTEGER", "notlar": "TEXT", "gv": "JSON"},
    "users": {"misafir": "BOOLEAN DEFAULT 0", "buro_id": "INTEGER", "yonetici": "BOOLEAN DEFAULT 0",
              "sistem": "BOOLEAN DEFAULT 0", "ad": "VARCHAR(128)", "unvan": "VARCHAR(64)"},
    "clients": {"buro_id": "INTEGER", "vkn": "VARCHAR(16)", "vergi_dairesi": "VARCHAR(128)", "luca_kodu": "VARCHAR(64)"},
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
        _buro_gecisi(conn, text)


def _buro_gecisi(conn, text):
    """Büro ayrımından önceki veriler 1 numaralı büroya aittir; o zamanki büro kullanıcıları bu büronun yöneticisidir."""
    if conn.execute(text("SELECT COUNT(*) FROM burolar")).scalar():
        return
    ad = "Büro"
    try:
        from . import buro
        ad = buro.dosya_bilgileri().get("unvan") or ad
    except Exception:
        pass
    conn.execute(text("INSERT INTO burolar (id, ad, aktif, created_at) VALUES (1, :ad, 1, CURRENT_TIMESTAMP)"), {"ad": ad})
    conn.execute(text("UPDATE clients SET buro_id = 1 WHERE buro_id IS NULL AND NOT is_demo"))
    conn.execute(text("UPDATE users SET buro_id = 1 WHERE buro_id IS NULL"))
    conn.execute(text("UPDATE users SET yonetici = 1, sistem = 1 WHERE NOT misafir"))


def reset_engine():
    """Testlerde veri klasörü değişince bağlantıyı sıfırlar."""
    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _Session = None
