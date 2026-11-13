"""Analiz türleri ve her birinin ihtiyaç duyduğu belgeler.

Gösterge panelinde her analiz, hangi belgelerin yüklü olduğunu ve eksiklerin neler olduğunu gösterir.
Eksik belge için kullanıcıya ne yapması gerektiği söylenir (dosya yükle veya tutarı elle gir).
"""

from dataclasses import dataclass, field
from datetime import timedelta

from . import beyannameler
from .checks import kdv
from .checks.rules import Context


@dataclass
class Need:
    label: str
    ok: bool
    hint: str = ""
    optional: bool = False  # "önerilir": eksikse analiz yine çalışır, bir kısmı atlanır


@dataclass
class Analysis:
    key: str
    title: str
    rules: tuple
    needs: list = field(default_factory=list)
    yakinda: bool = False
    link: str = ""

    @property
    def missing(self):
        return [n for n in self.needs if not n.ok and not n.optional]

    @property
    def status(self) -> str:
        if self.yakinda:
            return "yakinda"
        return "eksik" if self.missing else "hazir"


KDV_RULES = ("K-21", "K-22", "K-22D", "K-23", "K-24", "K-25", "K-26", "K-30")
GECICI_RULES = ("K-27", "K-28", "K-29")
RISK_RULES = ("K-10", "K-11", "K-12", "K-13", "K-14", "K-15", "K-16", "K-17", "K-18", "K-19", "K-20", "K-31", "K-32", "K-33", "K-34", "K-35")
MUTABAKAT_RULES = ("K-00", "K-01", "K-02", "K-03", "K-04", "K-05", "K-05K", "K-05M", "K-09")


AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]


def _ay(y, m):
    return f"{AYLAR[m - 1]} {y}"


def _aylar(period):
    """Dönemdeki (yıl, ay) çiftleri."""
    if period is None:
        return []
    out, y, m = [], period.baslangic.year, period.baslangic.month
    while (y, m) <= (period.bitis.year, period.bitis.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _aralik(aylar):
    a, b = aylar[0], aylar[-1]
    return f"{AYLAR[a[1] - 1]}–{_ay(*b)}" if a[0] == b[0] else f"{_ay(*a)}–{_ay(*b)}"


def _liste(aylar):
    """Eksik ayları yıla göre gruplayarak yazar: 'Temmuz ve Eylül 2026'."""
    by = {}
    for y, m in aylar:
        by.setdefault(y, []).append(AYLAR[m - 1])
    parca = []
    for y, ad in by.items():
        parca.append((", ".join(ad[:-1]) + " ve " + ad[-1] if len(ad) > 1 else ad[0]) + f" {y}")
    return "; ".join(parca)


def _has(batches, doc):
    return bool(batches and batches.get(doc))


def build(period, batches: dict, ctx: Context | None, fin, prev_ctx_exists: bool, decls=()) -> list[Analysis]:
    """period: Period veya None; batches: belge türü -> son onaylı aktarım; decls: mükellefin aktif beyannameleri."""
    has_mizan, has_kebir = _has(batches, "mizan"), _has(batches, "kebir")
    has_yev, has_muavin = _has(batches, "yevmiye"), _has(batches, "muavin")
    hareket = bool(ctx and ctx.mizan_hareket)

    # --- KDV ---
    prev_adjacent = bool(ctx and ctx.previous and ctx.previous[0].period_end == ctx.period_start - timedelta(days=1))
    aylik = period is not None and beyannameler.tek_ay(period.baslangic, period.bitis)
    aylar = _aylar(period)
    onceki = (period.baslangic - timedelta(days=1)) if period is not None else None
    onceki_ad = _ay(onceki.year, onceki.month) if onceki else "Önceki ay"
    devreden_girildi = period is not None and (
        period.devreden_kdv is not None or beyannameler.onceki_ay_kdv(decls, period.baslangic) is not None)
    eksik_beyan = [(y, m) for y, m in aylar if beyannameler.bul(decls, "kdv", y, m) is None]
    beyan_etiket = (f"{_ay(*aylar[0])} KDV beyannamesi" if len(aylar) == 1 else
                    f"KDV beyannameleri ({_aralik(aylar)})") if aylar else "Bu ayın KDV beyannamesi"
    beyan_ipucu = ((f"Eksik: {_liste(eksik_beyan)}. " if eksik_beyan else "Tümü yüklü. ")
                   + "Beyan verildiyse GİB'den PDF'ini yükleyin; beyandaki tutarlar defterle karşılaştırılır.")
    k = Analysis("kdv", "KDV Ön Kontrol", KDV_RULES, link="kdv")
    k.needs = [
        Need("Dönem mizanı, defteri kebir veya yılbaşından yevmiye", has_mizan or has_kebir or bool(ctx and ctx.ytd),
             "191, 391, 190 ve 360 hesaplarının bakiyeleri buradan okunur."),
        Need("Yevmiye defteri", has_yev, "Ay içindeki KDV hareketleri ve mahsup fişi için.", optional=True),
        Need(f"{onceki_ad} devreden KDV'si", devreden_girildi or prev_adjacent,
             f"{onceki_ad} KDV beyannamesini (PDF) yükleyin, 'sonraki döneme devreden KDV' tutarını girin "
             f"veya {onceki_ad} Luca dosyalarını yükleyin."),
        Need(beyan_etiket, not eksik_beyan and bool(aylar), beyan_ipucu, optional=True),
        Need("e-Fatura / e-Arşiv listesi", bool(ctx and ctx.faturalar),
             "GİB'den veya entegratörden gelen ve giden fatura listesini Excel olarak yükleyin; her fatura defterle eşleştirilir.",
             optional=True),
        Need("Önceki aylar (oran karşılaştırması)", bool(ctx and ctx.previous),
             "Hesaplanan KDV / satış oranını önceki aylarla karşılaştırmak için.", optional=True),
    ]

    # --- Defter mutabakatı ---
    m = Analysis("mutabakat", "Defter Mutabakatı", MUTABAKAT_RULES, link="mutabakat")
    m.needs = [
        Need("Yevmiye defteri", has_yev, "Fiş denkliği ve diğer defterlerle karşılaştırma için."),
        Need("Defteri kebir", has_kebir, "Yevmiye–kebir karşılaştırması için.", optional=True),
        Need("Muavin defteri", has_muavin, "Yevmiye–muavin karşılaştırması için.", optional=True),
        Need("Mizan", has_mizan, "Mizan denkliği ve yevmiye–mizan karşılaştırması için.", optional=True),
    ]

    # --- Hata ve risk taraması ---
    r = Analysis("risk", "Hata ve Risk Taraması", RISK_RULES, link="risk")
    r.needs = [
        Need("Yevmiye defteri", has_yev or has_kebir or has_muavin,
             "Kasa hareketleri, tatil günü giderleri ve mükerrer fatura kayıtları buradan taranır."),
        Need("Dönem sonu bakiyeleri", bool(fin and fin.has_balance),
             "Ters bakiye kontrolü için yılbaşından alınmış mizan, defteri kebir veya yılbaşından yevmiye.", optional=True),
    ]

    # --- Finansal analiz ---
    f = Analysis("finansal", "Finansal Analiz", (), link="finansal")
    balance_hint = ("Yüklenen mizan yalnızca ayın hareketlerini içeriyor; yılbaşından alınmış mizan veya defteri kebir yükleyin."
                    if hareket and not has_kebir else "Yılbaşından dönem sonuna mizan veya defteri kebir.")
    f.needs = [
        Need("Dönem sonu bakiyeleri", bool(fin and fin.has_balance), balance_hint),
        Need("Gelir ve gider hareketleri", bool(fin and fin.has_income), "Yevmiye, defteri kebir veya mizan."),
        Need("Önceki dönem (karşılaştırma)", prev_ctx_exists, "Önceki dönemin dosyaları yüklenirse değişimler gösterilir.",
             optional=True),
    ]

    # --- Geçici vergi ---
    from .gecici import donem_no
    g = Analysis("gecici", "Geçici Vergi", GECICI_RULES, link="gecici-vergi")
    gv = (period.gv or {}) if period is not None else {}
    g.needs = [
        Need("Yılbaşından dönem sonuna bakiyeler", bool(fin and fin.has_balance),
             "Ocak'tan dönem sonuna kümülatif mizan veya defteri kebir (nakli yekün dahil)."),
        Need("Geçici vergi dönem sonu", period is not None and donem_no(period.bitis) is not None,
             "Mart, Haziran veya Eylül sonuyla biten dönemin dosyalarını yükleyin."),
        Need("KKEG, istisna ve indirimler", bool(gv.get("kaydedildi")),
             "Geçici Vergi sayfasında girin; girilmezse sıfır kabul edilir.", optional=True),
        Need(f"Önceki dönemin geçici vergi beyannamesi{_gv_onceki(period)}", bool(gv.get("onceki_odenen")) or
             (period is not None and (donem_no(period.bitis) == 1 or beyannameler.onceki_gv(decls, period.bitis) is not None)),
             "Mahsup için önceki dönemin beyannamesini (PDF) yükleyin veya hesaplanan vergiyi elle girin.", optional=True),
        Need(f"Bu dönemin geçici vergi beyannamesi{_gv_bu(period)}", period is not None and
             beyannameler.bul(decls, "gecici", period.bitis.year, period.bitis.month) is not None,
             "Beyan verildiyse PDF'ini yükleyin; beyandaki kâr defterle karşılaştırılır.", optional=True),
    ]
    return [k, m, r, f, g]


def kdv_amounts(ctx: Context | None) -> dict:
    """Panel kutucukları için hesaplanan ve indirilecek KDV hareketleri (kuruş)."""
    if ctx is None:
        return {}
    mv = kdv.movements(ctx)
    if mv is None:
        return {}
    return {"hesaplanan": mv.total(("391",), "alacak"), "indirilecek": mv.total(("191",), "borc")}


def _gv_bu(period):
    if period is None:
        return ""
    return f" (Ocak–{_ay(period.bitis.year, period.bitis.month)})"


def _gv_onceki(period):
    if period is None or period.bitis.month < 6:
        return ""
    return f" (Ocak–{_ay(period.bitis.year, period.bitis.month - 3)})"
