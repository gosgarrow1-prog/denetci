"""Büro iş takibi: bu ayın beyan işleri, son günler ve mükellef bazında durum.

Son günler kanunî günlerdir (KDV 28, muhtasar ve damga 26, KDV 2 beyan 21 / ödeme 23, geçici vergi izleyen 2. ayın 17'si);
hafta sonu ve resmî tatile denk gelirse ilk iş gününe kayar. GİB'in ayrıca ilan ettiği idari uzatmalar bilinmez:
resmî duyuruları kontrol edin.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]

# (kod, ad, izleyen ay sayısı, gün, hangi aylarda)
ISLER = [
    ("kdv", "KDV", 1, 28, range(1, 13)),
    ("muhtasar", "Muhtasar ve SGK", 1, 26, range(1, 13)),
    ("gecici", "Geçici vergi", 2, 17, (3, 6, 9)),
]
ISIM = {k: ad for k, ad, *_ in ISLER}
# Yalnızca takvimde gösterilen (mükellef sütunu olmayan) yükümlülükler.
EK_TAKVIM = [
    ("KDV 2 beyanı", 1, 21), ("KDV 2 ödemesi", 1, 23), ("Damga vergisi beyanı", 1, 26),
]


def is_gunu_mu(d: date) -> bool:
    from .checks.risk import tatil_adi
    return d.weekday() < 5 and tatil_adi(d) in (None, "pazar")


def kaydir(d: date) -> date:
    while not is_gunu_mu(d):
        d += timedelta(days=1)
    return d


def _ay_ekle(y: int, m: int, n: int) -> tuple[int, int]:
    m += n
    return y + (m - 1) // 12, (m - 1) % 12 + 1


def son_gun(tur: str, yil: int, ay: int) -> date | None:
    """tur işinin yil/ay dönemi için son gün; o ayda böyle bir iş yoksa None."""
    for kod, _ad, n, gun, aylar in ISLER:
        if kod == tur:
            if ay not in aylar:
                return None
            y, m = _ay_ekle(yil, ay, n)
            return kaydir(date(y, m, gun))
    return None


def donem_adi(yil: int, ay: int) -> str:
    return f"{AYLAR[ay - 1]} {yil}"


def varsayilan_ay(bugun: date) -> tuple[int, int]:
    """Bugün beyanı verilecek en yakın dönem: önceki ay (KDV ve muhtasar)."""
    return _ay_ekle(bugun.year, bugun.month, -1)


@dataclass
class Vade:
    tarih: date
    ad: str
    donem: str
    kalan: int


def yaklasan(bugun: date, gun: int = 45) -> list[Vade]:
    """Önümüzdeki `gun` gün içindeki son günler (tarih sırasıyla)."""
    out: list[Vade] = []
    bitis = bugun + timedelta(days=gun)
    for ofs in range(-4, 1):
        y, m = _ay_ekle(bugun.year, bugun.month, ofs)  # dönem ayı
        for kod, ad, *_ in ISLER:
            d = son_gun(kod, y, m)
            if d and bugun <= d <= bitis:
                out.append(Vade(d, f"{ad} beyanı", donem_adi(y, m), (d - bugun).days))
        for ad, n, g in EK_TAKVIM:
            yy, mm = _ay_ekle(y, m, n)
            d = kaydir(date(yy, mm, g))
            if bugun <= d <= bitis:
                out.append(Vade(d, ad, donem_adi(y, m), (d - bugun).days))
    out.sort(key=lambda v: (v.tarih, v.ad))
    return out
