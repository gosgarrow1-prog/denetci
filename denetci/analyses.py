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


KDV_RULES = ("K-21", "K-22", "K-22D", "K-23", "K-24")
GECICI_RULES = ("K-27", "K-28", "K-29")
MUTABAKAT_RULES = ("K-00", "K-01", "K-02", "K-03", "K-04", "K-05", "K-05K", "K-05M", "K-09")


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
    devreden_girildi = period is not None and (
        period.devreden_kdv is not None or (aylik and beyannameler.onceki_ay_kdv(decls, period.baslangic) is not None))
    ayin_beyani = aylik and beyannameler.bul(decls, "kdv", period.bitis.year, period.bitis.month) is not None
    k = Analysis("kdv", "KDV Ön Kontrol", KDV_RULES, link="kdv")
    k.needs = [
        Need("Dönem mizanı veya defteri kebir", has_mizan or has_kebir,
             "191, 391, 190 ve 360 hesaplarının bakiyeleri buradan okunur."),
        Need("Yevmiye defteri", has_yev, "Ay içindeki KDV hareketleri ve mahsup fişi için.", optional=True),
        Need("Önceki ayın devreden KDV'si", devreden_girildi or prev_adjacent,
             "Önceki ayın KDV beyannamesini (PDF) yükleyin, 'sonraki döneme devreden KDV' tutarını girin "
             "veya önceki ayın Luca dosyalarını yükleyin."),
        Need("Bu ayın KDV beyannamesi", ayin_beyani,
             "Beyan verildiyse PDF'ini yükleyin; beyandaki tutarlar defterle karşılaştırılır.", optional=True),
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
        Need("Önceki dönemin geçici vergi beyannamesi", bool(gv.get("onceki_odenen")) or
             (period is not None and (donem_no(period.bitis) == 1 or beyannameler.onceki_gv(decls, period.bitis) is not None)),
             "Mahsup için önceki dönemin beyannamesini (PDF) yükleyin veya hesaplanan vergiyi elle girin.", optional=True),
        Need("Bu dönemin geçici vergi beyannamesi", period is not None and
             beyannameler.bul(decls, "gecici", period.bitis.year, period.bitis.month) is not None,
             "Beyan verildiyse PDF'ini yükleyin; beyandaki kâr defterle karşılaştırılır.", optional=True),
    ]
    return [k, m, f, g]


def kdv_amounts(ctx: Context | None) -> dict:
    """Panel kutucukları için hesaplanan ve indirilecek KDV hareketleri (kuruş)."""
    if ctx is None:
        return {}
    mv = kdv.movements(ctx)
    if mv is None:
        return {}
    return {"hesaplanan": mv.total(("391",), "alacak"), "indirilecek": mv.total(("191",), "borc")}
