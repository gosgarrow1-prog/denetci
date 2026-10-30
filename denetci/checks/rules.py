"""Kontrol kuralları. Her kural saf bir fonksiyondur: veriyi alır, sonuç döner, hiçbir şeyi değiştirmez.

Bulgu türleri: A = doğrulanmış aritmetik/veri tutarsızlığı, K = kurala dayalı olası uygunsuzluk,
I = istatistiksel anomali, D = ek belge veya mesleki değerlendirme gerektiren durum.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from ..config import TOLERANS_KURUS, YUKSEK_ESIK_AKTIF_ORAN, YUKSEK_ESIK_TUTAR_KURUS
from ..parsing import code_segments, kurus_str


@dataclass(frozen=True)
class Rule:
    id: str
    version: str
    title: str
    description: str
    kind: str
    frameworks: tuple[str, ...]
    required: tuple[str, ...]  # "mizan", "yevmiye"
    basis: str
    basis_verified: bool
    exceptions: str
    valid_from: date
    default_severity: str


@dataclass
class Evidence:
    raw_row_id: int
    label: str


@dataclass
class Draft:
    title: str
    severity: str
    hesap_kodu: str | None
    amount: int | None
    key: str
    explanation: str
    certainty: str
    next_step: str
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class Result:
    rule: Rule
    # gecti, bulgu, calistirilamadi (gerekli veri yok), uygulanmadi (isteğe bağlı veri yok), bilgi (yalnızca ölçüm)
    status: str
    message: str
    drafts: list[Draft] = field(default_factory=list)


@dataclass
class Context:
    framework: str
    period_start: date
    period_end: date
    mizan: list | None  # TrialBalanceLine listesi
    yevmiye: list | None  # JournalLine listesi
    tolerance: int = TOLERANS_KURUS
    kebir: list | None = None  # JournalLine listesi (defteri kebirden)
    muavin: list | None = None  # JournalLine listesi (muavinden)
    openings: dict = field(default_factory=dict)  # {"kebir": [OpeningBalance], "muavin": [...]}
    ranges: dict = field(default_factory=dict)  # belge -> (ilk tarih, son tarih)
    # Aralığı belgenin kendi başlığında yazan belgeler (ör. Luca "Tarih Aralığı").
    # Diğerlerinde aralık yalnızca kayıt tarihlerinden çıkarılır ve kapsam için kanıt sayılmaz.
    declared: set = field(default_factory=set)
    # Mizan yalnızca bir tarih aralığının hareketlerini içeriyorsa (devir yok) bakiyeleri gerçek bakiye değildir.
    mizan_hareket: bool = False
    # Önceki dönemlerin bağlamları (en yakından uzağa). Dönemler arası kontroller kullanır.
    previous: list = field(default_factory=list)
    # Önceki aydan devreden KDV (kuruş), yoksa None. Kaynak: "girildi" (elle) veya "beyanname" (yüklenen PDF).
    devreden_kdv: int | None = None
    devreden_kaynak: str | None = None
    # Aynı dönemin yüklenmiş beyannameleri (alanlar sözlüğü, kuruş): KDV (aylık) ve geçici vergi.
    kdv_beyan: dict | None = None
    gv_beyan: dict | None = None

    @property
    def leaves(self):
        return [ln for ln in (self.mizan or []) if ln.is_leaf]

    def aktif_toplam(self) -> int:
        total = 0
        for ln in self.leaves:
            if ln.hesap_kodu[:1] in ("1", "2") and ln.bakiye_borc is not None:
                total += (ln.bakiye_borc or 0) - (ln.bakiye_alacak or 0)
        return max(total, 0)

    def rule_severity(self, amount: int) -> str:
        esik = max(YUKSEK_ESIK_TUTAR_KURUS, int(self.aktif_toplam() * YUKSEK_ESIK_AKTIF_ORAN))
        return "Yüksek" if abs(amount) >= esik else "Orta"


ARITMETIK = "Aritmetik zorunluluk (çift taraflı kayıt)."
D0 = date(2026, 1, 1)


DOC_NAMES = {"mizan": "mizan", "yevmiye": "yevmiye defteri", "kebir": "defteri kebir", "muavin": "muavin defter"}


def _missing(ctx: Context, rule: Rule) -> str | None:
    need = [r for r in rule.required if not getattr(ctx, r)]
    if need:
        names = DOC_NAMES
        return "Çalıştırılamadı: " + ", ".join(names[n] for n in need) + " yüklenmemiş."
    return None


# ---- K-01 Mizan denkliği ----------------------------------------------------

K01 = Rule("K-01", "1.0", "Mizan denkliği",
           "Yaprak hesaplarda toplam borç = toplam alacak; borç bakiyeleri toplamı = alacak bakiyeleri toplamı.",
           "A", ("THP_VUK",), ("mizan",), ARITMETIK, True,
           "Nazım hesaplar ayrıca değerlendirilmeli; yuvarlama toleransı 1 TL.", D0, "Kritik")


def k01(ctx: Context) -> Result:
    if (m := _missing(ctx, K01)):
        return Result(K01, "calistirilamadi", m)
    leaves = ctx.leaves
    drafts = []
    tb = sum(ln.borc for ln in leaves)
    ta = sum(ln.alacak for ln in leaves)
    if abs(tb - ta) > ctx.tolerance:
        drafts.append(Draft("Mizan dönem hareketleri denk değil", "Kritik", None, tb - ta, "hareket",
                            f"Yaprak hesaplarda toplam borç {kurus_str(tb)} TL, toplam alacak {kurus_str(ta)} TL. Fark {kurus_str(tb - ta)} TL.",
                            "Aritmetik olarak kesin. Fark mizanın eksik aktarılmasından veya dosyadaki bir hatadan kaynaklanabilir.",
                            "İçe aktarma raporunda atlanan veya hatalı satır olup olmadığını kontrol edin; Luca'daki mizan toplamıyla karşılaştırın."))
    if all(ln.bakiye_borc is not None for ln in leaves):
        bb = sum(ln.bakiye_borc or 0 for ln in leaves)
        ba = sum(ln.bakiye_alacak or 0 for ln in leaves)
        if abs(bb - ba) > ctx.tolerance:
            drafts.append(Draft("Mizan bakiyeleri denk değil", "Kritik", None, bb - ba, "bakiye",
                                f"Borç bakiyeleri toplamı {kurus_str(bb)} TL, alacak bakiyeleri toplamı {kurus_str(ba)} TL. Fark {kurus_str(bb - ba)} TL.",
                                "Aritmetik olarak kesin.",
                                "Bakiye kolonlarının doğru eşleştirildiğini ve bakiyesi hatalı hesapları (K-03) kontrol edin."))
    if drafts:
        return Result(K01, "bulgu", "Mizan denk değil.", drafts)
    return Result(K01, "gecti", f"Denk. Toplam borç = toplam alacak = {kurus_str(tb)} TL.")


# ---- K-02 Fiş denkliği ------------------------------------------------------

K02 = Rule("K-02", "1.0", "Fiş denkliği", "Her yevmiye fişinde borç toplamı = alacak toplamı.",
           "A", ("THP_VUK",), ("yevmiye",), ARITMETIK, True,
           "Aynı fiş numarası farklı tarihlerde tekrar kullanılmışsa önce veri sorunu olarak değerlendirilir.", D0, "Kritik")


def k02(ctx: Context) -> Result:
    if (m := _missing(ctx, K02)):
        return Result(K02, "calistirilamadi", m)
    groups = defaultdict(list)
    for ln in ctx.yevmiye:
        # Luca'da yevmiye madde numarası fişi tekil olarak tanımlar.
        groups[f"Madde {ln.madde_no}" if ln.madde_no else ln.fis_no].append(ln)
    drafts = []
    for fis, lines in groups.items():
        b = sum(ln.borc for ln in lines)
        a = sum(ln.alacak for ln in lines)
        if abs(b - a) > ctx.tolerance:
            label = fis if fis.startswith("Madde") else f"Fiş {fis}"
            drafts.append(Draft(f"{label} denk değil", "Kritik", None, b - a, f"fis:{fis}",
                                f"{label} ({lines[0].tarih:%d.%m.%Y}) borç toplamı {kurus_str(b)} TL, alacak toplamı {kurus_str(a)} TL. Fark {kurus_str(b - a)} TL.",
                                "Aritmetik olarak kesin. Fişin bir satırı eksik aktarılmış veya tutar yanlış girilmiş olabilir.",
                                "Fişi Luca'da açıp satırlarını karşılaştırın.",
                                [Evidence(ln.raw_row_id, f"{label} satırı, hesap {ln.hesap_kodu}") for ln in lines]))
    if drafts:
        return Result(K02, "bulgu", f"{len(drafts)} fiş denk değil.", drafts)
    return Result(K02, "gecti", f"{len(groups)} fişin tamamı denk.")


# ---- K-03 Açılış + hareket = kapanış ---------------------------------------

K03 = Rule("K-03", "1.0", "Bakiye ilişkisi",
           "Hesap bazında açılış + dönem borcu − dönem alacağı = kapanış bakiyesi. Açılış kolonu yoksa borç − alacak = bakiye.",
           "A", ("THP_VUK",), ("mizan",), ARITMETIK, True,
           "Mizandaki borç/alacak kolonlarının devri içerip içermediği programa göre değişir.", D0, "Yüksek")


def k03(ctx: Context) -> Result:
    if (m := _missing(ctx, K03)):
        return Result(K03, "calistirilamadi", m)
    if not all(ln.bakiye_borc is not None for ln in ctx.mizan):
        return Result(K03, "calistirilamadi", "Çalıştırılamadı: mizanda bakiye kolonları yok.")
    drafts = []
    for ln in ctx.leaves:
        acilis = (ln.acilis_borc or 0) - (ln.acilis_alacak or 0)
        beklenen = acilis + ln.borc - ln.alacak
        gercek = (ln.bakiye_borc or 0) - (ln.bakiye_alacak or 0)
        if abs(beklenen - gercek) > ctx.tolerance:
            drafts.append(Draft(f"{ln.hesap_kodu} bakiyesi hareketlerle uyuşmuyor", "Yüksek", ln.hesap_kodu, gercek - beklenen, ln.hesap_kodu,
                                f"{ln.hesap_kodu} {ln.hesap_adi or ''}: açılış {kurus_str(acilis)} + borç {kurus_str(ln.borc)} − alacak {kurus_str(ln.alacak)} = {kurus_str(beklenen)} TL olmalı; mizandaki bakiye {kurus_str(gercek)} TL. Fark {kurus_str(gercek - beklenen)} TL.",
                                "Aritmetik olarak kesin. Neden belirsiz: dosyadaki bakiye kolonu hatalı olabilir veya kolonlar yanlış eşleşmiş olabilir.",
                                "Hesabın Luca'daki muavin bakiyesini kontrol edin.",
                                [Evidence(ln.raw_row_id, f"Mizan satırı {ln.hesap_kodu}")]))
    if drafts:
        return Result(K03, "bulgu", f"{len(drafts)} hesapta bakiye ilişkisi tutmuyor.", drafts)
    return Result(K03, "gecti", "Tüm yaprak hesaplarda bakiye ilişkisi tutuyor.")


# ---- K-04 Ana/alt hesap tutarlılığı ----------------------------------------

K04 = Rule("K-04", "1.0", "Ana/alt hesap tutarlılığı", "Ana hesap satırındaki borç ve alacak, alt hesapların toplamına eşit olmalı.",
           "A", ("THP_VUK",), ("mizan",), ARITMETIK, True,
           "Ana hesap satırı bulunmayan mizanlarda kontrol yapılmaz.", D0, "Yüksek")


def k04(ctx: Context) -> Result:
    if (m := _missing(ctx, K04)):
        return Result(K04, "calistirilamadi", m)
    children = defaultdict(list)
    for ln in ctx.mizan:
        if ln.parent_kodu:
            children[ln.parent_kodu].append(ln)
    if not children:
        return Result(K04, "calistirilamadi", "Çalıştırılamadı: mizanda ana/alt hesap ilişkisi yok.")
    drafts = []
    for ln in ctx.mizan:
        kids = children.get(ln.hesap_kodu)
        if not kids:
            continue
        cb, ca = sum(k.borc for k in kids), sum(k.alacak for k in kids)
        fb, fa = ln.borc - cb, ln.alacak - ca
        if abs(fb) > ctx.tolerance or abs(fa) > ctx.tolerance:
            drafts.append(Draft(f"{ln.hesap_kodu} ana hesap toplamı alt hesaplarla uyuşmuyor", "Yüksek", ln.hesap_kodu, fb or fa, ln.hesap_kodu,
                                f"{ln.hesap_kodu} {ln.hesap_adi or ''}: ana hesap satırı borç {kurus_str(ln.borc)} / alacak {kurus_str(ln.alacak)} TL; alt hesapların toplamı borç {kurus_str(cb)} / alacak {kurus_str(ca)} TL. Fark borçta {kurus_str(fb)}, alacakta {kurus_str(fa)} TL.",
                                "Aritmetik olarak kesin. Analizlerde yalnızca alt hesaplar kullanıldığı için bu fark sonuçları çift saymaz.",
                                "Eksik alt hesap olup olmadığını ve mizanın tam alınıp alınmadığını kontrol edin.",
                                [Evidence(ln.raw_row_id, f"Ana hesap {ln.hesap_kodu}")] + [Evidence(k.raw_row_id, f"Alt hesap {k.hesap_kodu}") for k in kids]))
    if drafts:
        return Result(K04, "bulgu", f"{len(drafts)} ana hesapta tutarsızlık.", drafts)
    return Result(K04, "gecti", f"{len(children)} ana hesabın tamamı alt hesaplarıyla tutarlı.")


# ---- K-05 Yevmiye–mizan mutabakatı -----------------------------------------

K05 = Rule("K-05", "1.0", "Yevmiye–mizan mutabakatı",
           "Hesap bazında yevmiye borç/alacak toplamı, mizandaki dönem borç/alacak hareketine eşit olmalı.",
           "A", ("THP_VUK",), ("mizan", "yevmiye"), ARITMETIK, True,
           "Açılış fişinin mizanda açılış kolonunda mı hareket kolonunda mı gösterildiği programa göre değişir. Hiçbir belge otomatik doğru kabul edilmez.",
           D0, "Yüksek")


def k05(ctx: Context) -> Result:
    if (m := _missing(ctx, K05)):
        return Result(K05, "calistirilamadi", m)
    leaves = {ln.hesap_kodu: ln for ln in ctx.leaves}
    yb, ya = defaultdict(int), defaultdict(int)
    yrows = defaultdict(list)
    rng = ctx.ranges.get("mizan") if "mizan" in ctx.declared else None
    for ln in ctx.yevmiye:
        # Mizan tarih aralığını belirtiyorsa (Luca), yalnızca o aralıktaki yevmiye kayıtları karşılaştırılır.
        if rng and not (rng[0] <= ln.tarih <= rng[1]):
            continue
        yb[ln.hesap_kodu] += ln.borc
        ya[ln.hesap_kodu] += ln.alacak
        yrows[ln.hesap_kodu].append(ln)
    drafts = []
    for code in sorted(set(leaves) | set(yb)):
        tb = leaves.get(code)
        mb, ma = (tb.borc, tb.alacak) if tb else (0, 0)
        fb, fa = yb[code] - mb, ya[code] - ma
        if abs(fb) <= ctx.tolerance and abs(fa) <= ctx.tolerance:
            continue
        ev = [Evidence(tb.raw_row_id, f"Mizan satırı {code}")] if tb else []
        ev += [Evidence(j.raw_row_id, f"Yevmiye fiş {j.fis_no}, {j.tarih:%d.%m.%Y}") for j in yrows[code][:50]]
        if not tb:
            expl = f"{code} yevmiyede var (borç {kurus_str(yb[code])}, alacak {kurus_str(ya[code])} TL) ama mizanda yaprak hesap olarak yok."
        else:
            expl = (f"{code} {tb.hesap_adi or ''}: yevmiye borç {kurus_str(yb[code])} / alacak {kurus_str(ya[code])} TL; "
                    f"mizan borç {kurus_str(mb)} / alacak {kurus_str(ma)} TL. Fark borçta {kurus_str(fb)}, alacakta {kurus_str(fa)} TL.")
        drafts.append(Draft(f"{code} yevmiye ile mizan arasında fark", "Yüksek", code, fb or fa, code, expl,
                            "Fark aritmetik olarak kesin; hangi belgenin doğru olduğu bilinmiyor. Olası nedenler: farklı tarihlerde alınmış dosyalar, mükerrer veya eksik fiş, açılış fişinin farklı gösterimi.",
                            "İki dosyanın aynı tarihte alındığını doğrulayın; farkı oluşturan fişleri yevmiye satırlarından inceleyin.", ev))
    if drafts:
        return Result(K05, "bulgu", f"{len(drafts)} hesapta yevmiye ile mizan uyuşmuyor.", drafts)
    return Result(K05, "gecti", f"{len(leaves)} hesabın tamamında yevmiye ve mizan uyuşuyor.")


# ---- K-09 Negatif kasa ------------------------------------------------------

K09 = Rule("K-09", "1.0", "Negatif kasa",
           "100 Kasa grubundaki hesapların alacak bakiyesi vermesi (fiziken eksi nakit olamayacağı için) incelenmelidir.",
           "K", ("THP_VUK",), ("mizan",),
           "Tekdüzen Hesap Planı'nda 100 Kasa hesabının işleyişi. Dayanak kaynağı henüz kullanıcı tarafından doğrulanmadı.", False,
           "Hatalı hesap eşlemesi, döviz kasasında kur farkı, veri kesim zamanı.", D0, "Yüksek")


def _running_negative(lines, opening: int):
    """Satırları tarih sırasıyla izler; bakiyeyi ilk kez eksiye düşüren satırı döner."""
    running = opening
    for j in sorted(lines, key=lambda j: (j.tarih, j.madde_no or "", j.fis_no)):
        before = running
        running += j.borc - j.alacak
        if running < 0 <= before:
            return j, before
    return None, None


def k09(ctx: Context) -> Result:
    if ctx.mizan and not ctx.mizan_hareket:
        return _k09_mizan(ctx)
    for doc in ("kebir", "muavin"):
        if getattr(ctx, doc):
            return _k09_ledger(ctx, doc)
    if ctx.mizan:
        return Result(K09, "calistirilamadi", "Çalıştırılamadı: mizan yalnızca tarih aralığındaki hareketleri içeriyor, "
                                              "kasa bakiyesi bundan hesaplanamaz. Dönem başından alınmış mizan veya defteri kebir gerekli.")
    return Result(K09, "calistirilamadi", "Çalıştırılamadı: mizan, defteri kebir veya muavin yüklenmemiş.")


def _k09_draft(ctx, code, name, bakiye, when, ev, source):
    return Draft(f"{code} {name or 'Kasa'} alacak bakiyesi veriyor", ctx.rule_severity(bakiye), code, -bakiye, code,
                 f"{source} göre dönem sonunda {code} hesabı {kurus_str(-bakiye)} TL alacak bakiyesi veriyor.{when}",
                 "Bakiye aritmetik olarak kesin. Nedeni belirsiz: veri, ödemenin gerçekten kasadan yapılıp yapılmadığını göstermiyor.",
                 "Eksiye düşüren ödemenin belgesini ve aynı günün banka hareketlerini kontrol edin; öncesindeki kasa tahsilatlarının kaydedilip kaydedilmediğine bakın. Gerekli belgeler: ödeme belgesi, banka ekstresi, kasa defteri.",
                 ev)


def _when(j, before):
    if not j:
        return ""
    no = f"{j.madde_no} numaralı yevmiye maddesi" if j.madde_no else f"{j.fis_no} numaralı fiş"
    return (f" Bakiye ilk kez {j.tarih:%d.%m.%Y} tarihli {no} ile eksiye düştü "
            f"(önceki bakiye {kurus_str(before)} TL, bu satırda alacak {kurus_str(j.alacak)} TL).")


def _k09_mizan(ctx: Context) -> Result:
    kasa = [ln for ln in ctx.leaves if ln.hesap_kodu.startswith("100")]
    if not kasa:
        return Result(K09, "gecti", "Mizanda 100 Kasa hesabı yok.")
    drafts = []
    for ln in kasa:
        bakiye = (ln.bakiye_borc or 0) - (ln.bakiye_alacak or 0) if ln.bakiye_borc is not None else \
            (ln.acilis_borc or 0) - (ln.acilis_alacak or 0) + ln.borc - ln.alacak
        if bakiye >= 0:
            continue
        ev = [Evidence(ln.raw_row_id, f"Mizan satırı {ln.hesap_kodu}")]
        j, before = (None, None)
        if ctx.yevmiye:
            j, before = _running_negative([x for x in ctx.yevmiye if x.hesap_kodu == ln.hesap_kodu],
                                          (ln.acilis_borc or 0) - (ln.acilis_alacak or 0))
            if j:
                ev.append(Evidence(j.raw_row_id, f"Bakiyeyi eksiye düşüren satır: {j.madde_no or j.fis_no}"))
        drafts.append(_k09_draft(ctx, ln.hesap_kodu, ln.hesap_adi, bakiye, _when(j, before), ev, "Mizana"))
    if drafts:
        return Result(K09, "bulgu", f"{len(drafts)} kasa hesabı alacak bakiyesi veriyor.", drafts)
    return Result(K09, "gecti", "Kasa hesaplarında alacak bakiyesi yok.")


def _k09_ledger(ctx: Context, doc: str) -> Result:
    lines = [j for j in getattr(ctx, doc) if j.hesap_kodu.startswith("100")]
    opens = [o for o in ctx.openings.get(doc, []) if o.hesap_kodu.startswith("100")]
    if not lines and not opens:
        return Result(K09, "gecti", f"{DOC_NAMES[doc].capitalize()} içinde 100 Kasa hesabı yok.")
    # Kebirde açılış ana hesap (100), satırlar alt hesap düzeyindedir; ana hesap düzeyinde bakılır.
    key = (lambda c: c[:3]) if doc == "kebir" else (lambda c: c)
    groups = defaultdict(list)
    for j in lines:
        groups[key(j.hesap_kodu)].append(j)
    opening = defaultdict(int)
    open_rows = {}
    for o in opens:
        opening[key(o.hesap_kodu)] += o.borc - o.alacak
        open_rows[key(o.hesap_kodu)] = o
    drafts = []
    for code in sorted(set(groups) | set(opening)):
        bakiye = opening[code] + sum(j.borc - j.alacak for j in groups[code])
        if bakiye >= 0:
            continue
        ev = [Evidence(open_rows[code].raw_row_id, f"Nakli yekün {code}")] if code in open_rows else []
        j, before = _running_negative(groups[code], opening[code])
        if j:
            ev.append(Evidence(j.raw_row_id, f"Bakiyeyi eksiye düşüren satır: {j.madde_no or j.fis_no}"))
        name = next((x.hesap_adi for x in groups[code] if x.hesap_adi), None)
        drafts.append(_k09_draft(ctx, code, name, bakiye, _when(j, before), ev, DOC_NAMES[doc].capitalize() + "e"))
    if drafts:
        return Result(K09, "bulgu", f"{len(drafts)} kasa hesabı alacak bakiyesi veriyor ({DOC_NAMES[doc]}).", drafts)
    return Result(K09, "gecti", f"Kasa hesaplarında alacak bakiyesi yok ({DOC_NAMES[doc]}, nakli yekün dahil).")


# ---- K-00 Belge kapsamı -----------------------------------------------------

K00 = Rule("K-00", "1.0", "Belge kapsamı",
           "Yüklenen her kayıt belgesinin tarih aralığı analiz dönemini kapsamalı. Kısmi belgeyle yapılan karşılaştırmalar eksik kalır.",
           "A", ("THP_VUK",), (), "Veri bütünlüğü.", True,
           "Dönem içinde hiç hareketi olmayan günler aralığı kısa gösterebilir; Luca'daki 'Tarih Aralığı' bilgisi varsa o kullanılır.",
           D0, "Yüksek")


def k00(ctx: Context) -> Result:
    declared = {d: r for d, r in ctx.ranges.items() if d in ctx.declared}
    if not declared:
        return Result(K00, "uygulanmadi", "Uygulanmadı: belgelerde tarih aralığı bilgisi yok; kapsam kayıt tarihlerinden kesin anlaşılamaz.")
    drafts = []
    for doc, (start, end) in sorted(declared.items()):
        if start <= ctx.period_start and end >= ctx.period_end:
            continue
        drafts.append(Draft(f"{DOC_NAMES[doc].capitalize()} dönemin tamamını kapsamıyor", "Yüksek", None, None, f"kapsam:{doc}",
                            f"{DOC_NAMES[doc].capitalize()} {start:%d.%m.%Y} – {end:%d.%m.%Y} aralığını içeriyor; analiz dönemi "
                            f"{ctx.period_start:%d.%m.%Y} – {ctx.period_end:%d.%m.%Y}.",
                            "Tarih aralığı kesin. Bu belgeyle yapılan mutabakat ve bakiye kontrolleri dönemin yalnızca bir kısmını yansıtır.",
                            "Belgeyi Luca'dan dönemin tamamı için yeniden alın veya dönemi belgeye göre daraltın."))
    if drafts:
        return Result(K00, "bulgu", f"{len(drafts)} belge dönemi tam kapsamıyor.", drafts)
    return Result(K00, "gecti", "Tüm belgeler dönemi kapsıyor.")


# ---- K-05K / K-05M Yevmiye ile kebir ve muavin mutabakatı -------------------

K05K = Rule("K-05K", "1.0", "Yevmiye–kebir mutabakatı",
            "Ana hesap bazında, ortak tarih aralığında yevmiye borç/alacak toplamı defteri kebirdeki hareketlere eşit olmalı.",
            "A", ("THP_VUK",), ("yevmiye", "kebir"), ARITMETIK, True,
            "Kebirdeki nakli yekün (önceki aylar) karşılaştırmaya girmez. Hiçbir belge otomatik doğru kabul edilmez.", D0, "Yüksek")
K05M = Rule("K-05M", "1.0", "Yevmiye–muavin mutabakatı",
            "Muavindeki her hesap için, ortak tarih aralığında yevmiye borç/alacak toplamı muavin hareketlerine eşit olmalı.",
            "A", ("THP_VUK",), ("yevmiye", "muavin"), ARITMETIK, True,
            "Muavin yalnızca seçilen hesapları içerebilir; yalnızca muavinde bulunan hesaplar karşılaştırılır.", D0, "Yüksek")


def _overlap(ctx, a, b):
    ra, rb = ctx.ranges.get(a), ctx.ranges.get(b)
    if not ra or not rb:
        return None
    start, end = max(ra[0], rb[0]), min(ra[1], rb[1])
    return (start, end) if start <= end else None


def _ledger_compare(ctx, rule, other: str, key, only_other_accounts: bool) -> Result:
    if (m := _missing(ctx, rule)):
        # Kebir ve muavin isteğe bağlı belgeler; yüklenmemeleri veri güvenilirliğini düşürmez.
        return Result(rule, "uygulanmadi", m.replace("Çalıştırılamadı", "Uygulanmadı"))
    win = _overlap(ctx, "yevmiye", other)
    if not win:
        return Result(rule, "calistirilamadi", "Çalıştırılamadı: belgelerin ortak tarih aralığı yok.")
    inside = lambda j: win[0] <= j.tarih <= win[1]
    yb, ya, ob, oa = defaultdict(int), defaultdict(int), defaultdict(int), defaultdict(int)
    yrows, orows = defaultdict(list), defaultdict(list)
    for j in filter(inside, ctx.yevmiye):
        k = key(j.hesap_kodu)
        yb[k] += j.borc
        ya[k] += j.alacak
        yrows[k].append(j)
    for j in filter(inside, getattr(ctx, other)):
        k = key(j.hesap_kodu)
        ob[k] += j.borc
        oa[k] += j.alacak
        orows[k].append(j)
    accounts = set(orows) | {k for o in ctx.openings.get(other, []) for k in [key(o.hesap_kodu)]} if only_other_accounts \
        else set(yrows) | set(orows)
    name = DOC_NAMES[other]
    drafts = []
    for k in sorted(accounts):
        fb, fa = yb[k] - ob[k], ya[k] - oa[k]
        if abs(fb) <= ctx.tolerance and abs(fa) <= ctx.tolerance:
            continue
        ev = [Evidence(j.raw_row_id, f"{name.capitalize()} {j.tarih:%d.%m.%Y} {j.madde_no or j.fis_no}") for j in orows[k][:25]]
        ev += [Evidence(j.raw_row_id, f"Yevmiye madde {j.madde_no or j.fis_no}, {j.tarih:%d.%m.%Y}") for j in yrows[k][:25]]
        drafts.append(Draft(f"{k} yevmiye ile {name} arasında fark", "Yüksek", k, fb or fa, f"{other}:{k}",
                            f"{win[0]:%d.%m.%Y} – {win[1]:%d.%m.%Y} aralığında {k}: yevmiye borç {kurus_str(yb[k])} / alacak {kurus_str(ya[k])} TL; "
                            f"{name} borç {kurus_str(ob[k])} / alacak {kurus_str(oa[k])} TL. Fark borçta {kurus_str(fb)}, alacakta {kurus_str(fa)} TL.",
                            "Fark aritmetik olarak kesin; hangi belgenin doğru olduğu bilinmiyor. Olası nedenler: belgelerin farklı zamanlarda alınması, eksik veya fazla kayıt.",
                            "İki belgeyi Luca'dan aynı anda yeniden alın; farkı oluşturan maddeleri kanıt satırlarından inceleyin.", ev))
    if drafts:
        return Result(rule, "bulgu", f"{len(drafts)} hesapta yevmiye ile {name} uyuşmuyor.", drafts)
    return Result(rule, "gecti", f"{len(accounts)} hesabın tamamında yevmiye ve {name} uyuşuyor "
                                 f"({win[0]:%d.%m.%Y} – {win[1]:%d.%m.%Y}).")


def k05k(ctx: Context) -> Result:
    return _ledger_compare(ctx, K05K, "kebir", lambda c: code_segments(c)[0], only_other_accounts=False)


def k05m(ctx: Context) -> Result:
    return _ledger_compare(ctx, K05M, "muavin", lambda c: c, only_other_accounts=True)


ALL_RULES = [(K00, k00), (K01, k01), (K02, k02), (K03, k03), (K04, k04), (K05, k05), (K05K, k05k), (K05M, k05m), (K09, k09)]
