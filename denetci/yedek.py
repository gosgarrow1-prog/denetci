"""Gece yedeği: veritabanı ve yüklenen dosyalar tek bir zip dosyasına alınır, eski yedekler silinir."""

import sqlite3
import tempfile
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from .config import data_dir, yedek_dir

SAKLAMA_GUN = 14
ATLA = {"denetci.db", "denetci.db-wal", "denetci.db-shm", "demo_dosyalari", "bekleyen"}


def al(simdi: datetime | None = None, ek_klasorler: list[Path] | None = None) -> Path:
    """Yedeği alır, yolunu döner. Veritabanı çalışırken tutarlı kopyalanır (sqlite yedekleme)."""
    simdi = simdi or datetime.now()
    veri = data_dir()
    hedef = yedek_dir() / f"denetci-{simdi:%Y-%m-%d-%H%M}.zip"
    with tempfile.TemporaryDirectory() as tmp:
        kopya = Path(tmp) / "denetci.db"
        if (veri / "denetci.db").exists():
            kaynak = sqlite3.connect(veri / "denetci.db")
            hedef_db = sqlite3.connect(kopya)
            with hedef_db:
                kaynak.backup(hedef_db)
            kaynak.close()
            hedef_db.close()
        with zipfile.ZipFile(hedef, "w", zipfile.ZIP_DEFLATED) as z:
            if kopya.exists():
                z.write(kopya, "veri/denetci.db")
            for f in sorted(veri.rglob("*")):
                if f.is_file() and f.relative_to(veri).parts[0] not in ATLA:
                    z.write(f, "veri/" + f.relative_to(veri).as_posix())
            for k in ek_klasorler or []:
                if k.is_dir() and veri not in k.resolve().parents and k.resolve() != veri:
                    for f in sorted(k.rglob("*")):
                        if f.is_file():
                            z.write(f, f"{k.name}/" + f.relative_to(k).as_posix())
    temizle(simdi)
    return hedef


def temizle(simdi: datetime | None = None) -> int:
    """SAKLAMA_GUN günden eski yedekleri siler; en yeni yedek her zaman kalır."""
    simdi = simdi or datetime.now()
    yedekler = sorted(yedek_dir().glob("denetci-*.zip"))
    silinen = 0
    for f in yedekler[:-1]:
        if datetime.fromtimestamp(f.stat().st_mtime) < simdi - timedelta(days=SAKLAMA_GUN):
            f.unlink()
            silinen += 1
    return silinen
