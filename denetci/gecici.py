"""Geçici vergi ön hesaplaması. Beyan değildir; tahmini matrah ve vergi gösterir.

Ticari kâr, yılbaşından dönem sonuna 6-7 sınıfı bakiyelerinden (kümülatif mizan veya defteri kebir, nakli yekün dahil)
hesaplanır. KKEG, istisna, indirim ve geçmiş yıl zararları kullanıcı tarafından girilir.
Önceki dönemde hesaplanan geçici vergi, elle girilmemişse bir önceki dönemin yüklenmiş beyannamesinden alınır.
"""

import re
from dataclasses import dataclass, field
from datetime import date

from . import beyannameler
from .finance import ana_balances, income_statement
from .parametreler import GECICI_VERGI, KURUM_ISARETLERI
from .parsing import fold, kurus_str

ALANLAR = [
    ("kkeg", "Kanunen kabul edilmeyen giderler (KKEG)", "+"),
    ("istisna", "İstisna kazançlar", "-"),
    ("gecmis_zarar", "Geçmiş yıl zararları", "-"),
    ("indirim", "Diğer indirimler (Ar-Ge, bağış vb.)", "-"),
    ("onceki_odenen", "Önceki dönemlerde hesaplanan geçici vergi", "mahsup"),
]
AYLAR_DONEM = {3: "1. dönem (Ocak–Mart)", 6: "2. dönem (Ocak–Haziran)", 9: "3. dönem (Ocak–Eylül)",
               12: "4. dönem (Ocak–Aralık)"}


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
    mahsup_kaynak: str | None = None  # "girildi" veya beyanname dönemi adı
    beyan: date | None = None
    beyanname: dict | None = None  # aynı dönemin yüklenmiş beyannamesi (alanlar)
    gecmis_zarar_kalan: int | None = None  # önceki beyannamedeki toplam geçmiş yıl zararı (bilgi)
    tur: str = "kurum"
    notlar: list = field(default_factory=list)
    maliyet: "Maliyet | None" = None

    @property
    def tahmini_kar(self):
        if self.ticari_kar is None:
            return None
        return self.ticari_kar - (self.maliyet.ek if self.maliyet else 0)


MALIYET_YONTEMLERI = {
    "otomatik": "Otomatik (maliyet kaydı eksikse tahmin et)",
    "oran": "Girdiğim brüt kâr oranıyla",
    "stok": "Girdiğim dönem sonu stokuyla",
    "yok": "Tahmin yapma (defterdeki kayıtları kullan)",
}


@dataclass
class Maliyet:
    yontem: str
    eksik: bool = False  # dönemde satış var, maliyet kaydı yok
    ek: int = 0  # kâra eklenen tahmini ek satış maliyeti (kuruş)
    oran: float | None = None  # kullanılan brüt kâr oranı
    kaynak: str | None = None
    aciklama: str | None = None
    net_satis: int = 0
    kayitli_smm: int = 0


def oran_str(x: float) -> str:
    return f"{x * 100:.1f}".replace(".", ",")


def _gv_beyan_orani(decls, yil: int):
    """Yüklenmiş geçici vergi beyannamelerinin gelir tablosundan brüt kâr oranı (en yeni)."""
    for d in sorted((d for d in decls if d.tur == "gecici"), key=lambda x: (x.yil, x.ay), reverse=True):
        a = d.alanlar or {}
        if a.get("net_satis") and a.get("smm") is not None:
            return 1 - a["smm"] / a["net_satis"], f"{d.yil} / {d.ay // 3}. dönem beyannamesinin gelir tablosu"
    return None


def maliyet_tahmini(ctx, period, decls, bal, kal) -> Maliyet:
    """Dönem sonu satış maliyeti kaydı yapılmamışsa kâr için tahmini maliyet.

    - Brüt kâr oranı: hedef maliyet = net satış × (1 − oran); kâra eklenen = hedef − kayıtlı maliyet.
      Oran sırasıyla: kullanıcının girdiği, bu yılın maliyeti kaydedilmiş önceki aylarınınki, yüklenen geçici vergi
      beyannamesinin gelir tablosundaki.
    - Dönem sonu stok: 153 bakiyesi (açılış + alışlar − kaydedilen maliyet) − girilen stok = kaydedilmemiş maliyet.
    """
    from .checks.kdv import movements

    gv = period.gv or {}
    m = Maliyet(gv.get("maliyet") or "otomatik")
    m.net_satis = kal["net_satis"]
    m.kayitli_smm = kal["net_satis"] - kal["brut_kar"]
    mv = movements(ctx)
    win_satis = mv.total(("600", "601", "602"), "alacak") - mv.total(("610", "611", "612"), "borc") if mv else 0
    win_smm = mv.total(("620", "621", "622", "623"), "borc") if mv else 0
    m.eksik = win_satis > 0 and win_smm == 0
    if m.yontem == "yok" or m.net_satis <= 0:
        return m
    if m.yontem == "stok":
        if gv.get("donem_sonu_stok") is None:
            m.aciklama = "Dönem sonu stok tutarı girilmedi; maliyet tahmini yapılmadı."
            return m
        m.ek = (bal.get("153") or 0) - int(gv["donem_sonu_stok"])
        m.kaynak = "153 bakiyesi − girilen dönem sonu stoku"
        m.aciklama = (f"Tahmini ek satış maliyeti {kurus_str(m.ek)} TL: 153 Ticari Mallar bakiyesi "
                      f"{kurus_str(bal.get('153') or 0)} TL − girilen dönem sonu stoku {kurus_str(int(gv['donem_sonu_stok']))} TL.")
        return m
    if m.yontem == "oran":
        if gv.get("brut_kar_orani") is None:
            m.aciklama = "Brüt kâr oranı girilmedi; maliyet tahmini yapılmadı."
            return m
        m.oran, m.kaynak = float(gv["brut_kar_orani"]), "girilen brüt kâr oranı"
    else:  # otomatik
        if not m.eksik:
            return m
        onceki_satis = m.net_satis - win_satis
        if m.kayitli_smm > 0 and onceki_satis > 0:
            m.oran, m.kaynak = 1 - m.kayitli_smm / onceki_satis, "bu yılın maliyeti kaydedilmiş önceki ayları"
        elif (b := _gv_beyan_orani(decls, period.bitis.year)):
            m.oran, m.kaynak = b
        else:
            m.aciklama = ("Dönemde satış var ama satışların maliyeti kaydı yok, kâr olduğundan yüksek görünebilir. "
                          "Tahmin için Düzeltmeler bölümünde brüt kâr oranını veya dönem sonu stokunu girin.")
            return m
    hedef = round(m.net_satis * (1 - m.oran))
    m.ek = max(hedef - m.kayitli_smm, 0)
    m.aciklama = (f"Tahmini ek satış maliyeti {kurus_str(m.ek)} TL: net satış {kurus_str(m.net_satis)} TL × "
                  f"(1 − brüt kâr oranı %{oran_str(m.oran)}) = {kurus_str(hedef)} TL; kayıtlı maliyet "
                  f"{kurus_str(m.kayitli_smm)} TL. Oran kaynağı: {m.kaynak}.")
    return m


def ticari_kar(ctx):
    """Yılbaşından dönem sonuna ticari kâr: (kâr, gelir tablosu satırları, notlar, kaynak, bakiyeler) veya None."""
    bal, src = ana_balances(ctx)
    if bal is None:
        return None
    net = {k: -v for k, v in bal.items() if k[:1] in ("6", "7")}
    lines, kal, notes = income_statement(net)
    return kal["donem_kari"], lines, notes, src, bal, kal


def hesapla(ctx, period, client, decls=()) -> Hesap:
    h = Hesap(donem_no(period.bitis), False, None)
    h.tur = (period.gv or {}).get("tur") or mukellef_turu(client.sirket_turu, client.unvan)
    h.girdiler = {k: int((period.gv or {}).get(k) or 0) for k, _, _ in ALANLAR}
    h.beyan = beyan_tarihi(period.bitis) if h.donem_no else None
    if h.girdiler["onceki_odenen"]:
        h.mahsup_kaynak = "girildi"
    elif (og := beyannameler.onceki_gv(decls, period.bitis)):
        h.girdiler["onceki_odenen"] = og[0]
        h.mahsup_kaynak = f"{og[1].yil} / {og[1].ay // 3}. dönem beyannamesi"
    if (d := beyannameler.bul(decls, "gecici", period.bitis.year, period.bitis.month)):
        h.beyanname = d.alanlar
    prev = [d for d in decls if d.tur == "gecici" and d.yil == period.bitis.year and d.ay < period.bitis.month]
    if prev:
        h.gecmis_zarar_kalan = max(prev, key=lambda x: x.ay).alanlar.get("toplam_gecmis_zarar")
    if ctx is None:
        return h
    tk = ticari_kar(ctx)
    if tk is None:
        h.notlar.append("Yılbaşından dönem sonuna bakiyeler yok: kümülatif mizan veya defteri kebir (nakli yekün dahil) gerekli.")
        return h
    h.ticari_kar, h.gelir_tablosu, notes, h.kaynak, bal, kal = tk
    h.ytd = True
    h.notlar += notes
    h.pesin_193 = bal.get("193")
    g = h.girdiler
    h.maliyet = maliyet_tahmini(ctx, period, decls, bal, kal)
    if h.maliyet.aciklama:
        h.notlar.append(h.maliyet.aciklama)
    h.matrah = max(h.ticari_kar - h.maliyet.ek + g["kkeg"] - g["istisna"] - g["gecmis_zarar"] - g["indirim"], 0)
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
