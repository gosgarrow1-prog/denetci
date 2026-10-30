"""Geçici vergi ön hesaplaması (K-28). Beyan değildir; tahmini matrah ve vergi gösterir.

Ticari kâr, yılbaşından dönem sonuna 6-7 sınıfı bakiyelerinden (kümülatif mizan veya defteri kebir, nakli yekün dahil)
hesaplanır. KKEG, istisna, indirim ve geçmiş yıl zararları kullanıcı tarafından girilir.
"""

import re
from dataclasses import dataclass, field
from datetime import date

from .finance import ana_balances, income_statement
from .parametreler import GECICI_VERGI, KURUM_ISARETLERI
from .parsing import fold

ALANLAR = [
    ("kkeg", "Kanunen kabul edilmeyen giderler (KKEG)", "+"),
    ("istisna", "İstisna kazançlar", "-"),
    ("gecmis_zarar", "Geçmiş yıl zararları", "-"),
    ("indirim", "Diğer indirimler (Ar-Ge, bağış vb.)", "-"),
    ("onceki_odenen", "Önceki dönemlerde hesaplanan geçici vergi", "mahsup"),
]
AYLAR_DONEM = {3: "1. dönem (Ocak–Mart)", 6: "2. dönem (Nisan–Haziran)", 9: "3. dönem (Temmuz–Eylül)",
               12: "4. dönem (Ekim–Aralık)"}


def donem_no(end: date) -> int | None:
    """Dönem sonu bir geçici vergi dönem sonuysa 1-3 döner."""
    for i, (m, d) in enumerate(GECICI_VERGI["donem_sonlari"], 1):
        if end.month == m and end.day == d:
            return i
    return None


def mukellef_turu(sirket_turu: str | None, unvan: str) -> str:
    text = " " + re.sub(r"[^0-9a-z]+", " ", fold(f"{sirket_turu or ''} {unvan}")) + " "
    text = text.replace(" a s ", " as ")
    return "kurum" if set(text.split()) & set(KURUM_ISARETLERI) else "sahis"


def oran(tur: str, yil: int) -> float | None:
    table = GECICI_VERGI["kurum_orani" if tur == "kurum" else "sahis_orani"]
    return table.get(yil)


def beyan_tarihi(end: date) -> date:
    m = end.month + GECICI_VERGI["beyan_ay_sonra"]
    y = end.year + (m - 1) // 12
    m = (m - 1) % 12 + 1
    return date(y, m, GECICI_VERGI["beyan_gunu"])


@dataclass
class Hesap:
    donem_no: int | None
    ytd: bool
    kaynak: str | None
    ticari_kar: int | None = None
    gelir_tablosu: list = field(default_factory=list)
    girdiler: dict = field(default_factory=dict)
    matrah: int | None = None
    oran: float | None = None
    oran_girildi: bool = False
    hesaplanan: int | None = None
    mahsup: int = 0
    odenecek: int | None = None
    pesin_193: int | None = None
    tur: str = "kurum"
    beyan: date | None = None
    notlar: list = field(default_factory=list)


def hesapla(ctx, period, client) -> Hesap:
    h = Hesap(donem_no(period.bitis), False, None)
    h.tur = (period.gv or {}).get("tur") or mukellef_turu(client.sirket_turu, client.unvan)
    h.girdiler = {k: int((period.gv or {}).get(k) or 0) for k, _, _ in ALANLAR}
    h.beyan = beyan_tarihi(period.bitis) if h.donem_no else None
    if ctx is None:
        return h
    bal, src = ana_balances(ctx)
    if bal is None:
        h.notlar.append("Yılbaşından dönem sonuna bakiyeler yok: kümülatif mizan veya defteri kebir (nakli yekün dahil) gerekli.")
        return h
    h.ytd, h.kaynak = True, src
    net = {k: -v for k, v in bal.items() if k[:1] in ("6", "7")}
    h.gelir_tablosu, kal, notes = income_statement(net)
    h.notlar += notes
    h.ticari_kar = kal["donem_kari"]
    h.pesin_193 = bal.get("193")
    g = h.girdiler
    h.matrah = max(h.ticari_kar + g["kkeg"] - g["istisna"] - g["gecmis_zarar"] - g["indirim"], 0)
    user_rate = (period.gv or {}).get("oran")
    if user_rate:
        h.oran, h.oran_girildi = float(user_rate), True
    else:
        h.oran = oran(h.tur, period.bitis.year)
    if h.oran is None:
        h.notlar.append(f"{period.bitis.year} yılı için oran parametre tablosunda yok; oranı elle girin.")
        return h
    h.hesaplanan = round(h.matrah * h.oran)
    h.mahsup = g["onceki_odenen"]
    h.odenecek = max(h.hesaplanan - h.mahsup, 0)
    if h.ticari_kar + g["kkeg"] < 0:
        h.notlar.append("Dönem zararla sonuçlanıyor; geçici vergi çıkmaz.")
    h.notlar.append("Ön hesaplamadır, beyan değildir. Asgari kurumlar vergisi, yatırım indirimi ve özel durumlar dikkate alınmadı.")
    return h
