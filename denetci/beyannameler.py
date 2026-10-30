"""Yüklenmiş beyannamelerin dönemlerle eşleştirilmesi.

- KDV: bir ayın beyannamesindeki "sonraki döneme devreden KDV", izleyen ayın devreden KDV'sidir (K-22).
  Aynı ayın beyannamesi defterle karşılaştırılır (K-24).
- Geçici vergi: aynı yılın önceki dönem beyannamesi, mahsup edilecek "önceki dönemlerde hesaplanan geçici vergi"yi verir.
  Aynı dönemin beyannamesi defterdeki kârla karşılaştırılır (K-28).
"""

from datetime import date, timedelta

from sqlalchemy import select

from .models import Declaration


def aktif(session, client_id: int) -> list[Declaration]:
    return list(session.scalars(select(Declaration).where(Declaration.client_id == client_id, Declaration.durum == "aktif")
                                .order_by(Declaration.yil.desc(), Declaration.ay.desc(), Declaration.tur)))


def bul(decls, tur: str, yil: int, ay: int) -> Declaration | None:
    return next((d for d in decls if d.tur == tur and d.yil == yil and d.ay == ay), None)


def tek_ay(baslangic: date, bitis: date) -> bool:
    return baslangic.day == 1 and (baslangic.year, baslangic.month) == (bitis.year, bitis.month)


def onceki_ay_kdv(decls, baslangic: date) -> Declaration | None:
    prev = baslangic - timedelta(days=1)
    return bul(decls, "kdv", prev.year, prev.month)


def gv_hesaplanan(d: Declaration) -> int:
    """Beyannamedeki dönem sonu hesaplanan geçici vergi (asgari vergi dahil nihai tutar varsa o)."""
    a = d.alanlar or {}
    return int(a.get("nihai") if a.get("nihai") is not None else a.get("hesaplanan") or 0)


def onceki_gv(decls, bitis: date) -> tuple[int, Declaration] | None:
    """Bir önceki geçici vergi döneminin beyannamesinden, bu dönemde mahsup edilecek önceki dönem vergisi.
    Önceki beyanda hesaplanan vergi, kendinden önce hesaplanandan azsa yüksek olan alınır."""
    if bitis.month <= 3:
        return None
    d = bul(decls, "gecici", bitis.year, bitis.month - 3)
    if d is None:
        return None
    return max(gv_hesaplanan(d), int((d.alanlar or {}).get("onceki") or 0)), d
