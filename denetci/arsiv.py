"""Yüklenen her belgenin bir kopyası Mükellef\\Yıl\\Ay klasörüne kaydedilir (yalnızca arşiv; uygulama oradan okumaz)."""

import io
import re
import zipfile
from pathlib import Path

from .config import arsiv_dir

AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
TUR = {"mizan": "Mizan", "yevmiye": "Yevmiye", "kebir": "Kebir", "muavin": "Muavin"}


def _temiz(s: str) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", s or "").strip(" .")
    return " ".join(s.split())[:80] or "Adsız"


def _yer(o):
    """(yıl, ay, ön ek) veya None."""
    if o.declaration is not None:
        d = o.declaration
        on = "KDV Beyannamesi" if d.tur == "kdv" else "Geçici Vergi Beyannamesi"
        return d.yil, d.ay, on
    if o.fatura is not None:
        f = o.fatura
        yon = {"giden": "Giden", "gelen": "Gelen"}.get(f.yon or "", "")
        return f.bitis.year, f.bitis.month, f"Fatura Listesi {yon}".strip()
    if o.period is not None:
        tur = TUR.get(getattr(o.batch, "doc_type", None) or "", "Belge")
        p = o.period
        return p.bitis.year, p.bitis.month, tur
    return None


def kaydet(client, o, content: bytes) -> Path | None:
    """Başarılı yüklemenin kopyasını arşive yazar. Hata olursa yüklemeyi bozmaz, None döner."""
    if o.status not in ("aktarildi", "esleme"):
        return None
    try:
        yer = _yer(o)
        if yer is None:
            return None
        yil, ay, on = yer
        folder = arsiv_dir() / _temiz(client.unvan) / str(yil) / f"{ay:02d} {AYLAR[ay - 1]}"
        folder.mkdir(parents=True, exist_ok=True)
        ad = Path(o.filename)
        hedef = folder / f"{on} - {_temiz(ad.stem)}{ad.suffix.lower()}"
        if hedef.exists() and hedef.read_bytes() == content:
            return hedef
        n = 2
        while hedef.exists():
            hedef = folder / f"{on} - {_temiz(ad.stem)} ({n}){ad.suffix.lower()}"
            n += 1
        hedef.write_bytes(content)
        return hedef
    except Exception as exc:  # arşiv hatası yüklemeyi bozmamalı
        import logging
        logging.getLogger(__name__).warning('Arşive kopyalanamadı: %s', exc)
        return None


def mukellef_klasoru(client) -> Path:
    return arsiv_dir() / _temiz(client.unvan)


def zip_olarak(client) -> bytes | None:
    """Mükellefin arşiv klasörünü (yıl/ay düzeniyle) tek bir zip dosyası olarak verir; klasör boşsa None."""
    kok = mukellef_klasoru(client)
    dosyalar = sorted(f for f in kok.rglob("*") if f.is_file()) if kok.is_dir() else []
    if not dosyalar:
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in dosyalar:
            z.write(f, f.relative_to(kok).as_posix())
    return buf.getvalue()
