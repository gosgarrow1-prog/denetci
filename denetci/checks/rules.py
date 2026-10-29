"""Kontrol kuralları. Her kural saf bir fonksiyondur: veriyi alır, sonuç döner, hiçbir şeyi değiştirmez.

Bulgu türleri: A = doğrulanmış aritmetik/veri tutarsızlığı, K = kurala dayalı olası uygunsuzluk,
I = istatistiksel anomali, D = ek belge veya mesleki değerlendirme gerektiren durum.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from ..config import TOLERANS_KURUS, YUKSEK_ESIK_AKTIF_ORAN, YUKSEK_ESIK_TUTAR_KURUS
from ..parsing import kurus_str


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
    status: str  # gecti, bulgu, calistirilamadi
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


def _missing(ctx: Context, rule: Rule) -> str | None:
    need = [r for r in rule.required if (r == "mizan" and not ctx.mizan) or (r == "yevmiye" and not ctx.yevmiye)]
    if need:
        names = {"mizan": "mizan", "yevmiye": "yevmiye defteri"}
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
        groups[ln.fis_no].append(ln)
    drafts = []
    for fis, lines in groups.items():
        b = sum(ln.borc for ln in lines)
        a = sum(ln.alacak for ln in lines)
        if abs(b - a) > ctx.tolerance:
            drafts.append(Draft(f"Fiş {fis} denk değil", "Kritik", None, b - a, f"fis:{fis}",
                                f"Fiş {fis} ({lines[0].tarih:%d.%m.%Y}) borç toplamı {kurus_str(b)} TL, alacak toplamı {kurus_str(a)} TL. Fark {kurus_str(b - a)} TL.",
                                "Aritmetik olarak kesin. Fişin bir satırı eksik aktarılmış veya tutar yanlış girilmiş olabilir.",
                                "Fişi Luca'da açıp satırlarını karşılaştırın.",
                                [Evidence(ln.raw_row_id, f"Fiş {fis} satırı, hesap {ln.hesap_kodu}") for ln in lines]))
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
    for ln in ctx.yevmiye:
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


def k09(ctx: Context) -> Result:
    if (m := _missing(ctx, K09)):
        return Result(K09, "calistirilamadi", m)
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
        when = ""
        if ctx.yevmiye:
            running = (ln.acilis_borc or 0) - (ln.acilis_alacak or 0)
            lines = sorted((j for j in ctx.yevmiye if j.hesap_kodu == ln.hesap_kodu), key=lambda j: (j.tarih, j.fis_no))
            for j in lines:
                before = running
                running += j.borc - j.alacak
                if running < 0 <= before:
                    when = (f" Bakiye ilk kez {j.tarih:%d.%m.%Y} tarihli {j.fis_no} numaralı fişle eksiye düştü "
                            f"(önceki bakiye {kurus_str(before)} TL, bu satırda alacak {kurus_str(j.alacak)} TL).")
                    ev.append(Evidence(j.raw_row_id, f"Bakiyeyi eksiye düşüren satır: fiş {j.fis_no}"))
                    break
        drafts.append(Draft(f"{ln.hesap_kodu} {ln.hesap_adi or 'Kasa'} alacak bakiyesi veriyor", ctx.rule_severity(bakiye), ln.hesap_kodu,
                            -bakiye, ln.hesap_kodu,
                            f"Dönem sonunda {ln.hesap_kodu} hesabı {kurus_str(-bakiye)} TL alacak bakiyesi veriyor.{when}",
                            "Bakiye aritmetik olarak kesin. Nedeni belirsiz: veri, ödemenin gerçekten kasadan yapılıp yapılmadığını göstermiyor.",
                            "Eksiye düşüren ödemenin belgesini ve aynı günün banka hareketlerini kontrol edin; öncesindeki kasa tahsilatlarının kaydedilip kaydedilmediğine bakın. Gerekli belgeler: ödeme belgesi, banka ekstresi, kasa defteri.",
                            ev))
    if drafts:
        return Result(K09, "bulgu", f"{len(drafts)} kasa hesabı alacak bakiyesi veriyor.", drafts)
    return Result(K09, "gecti", "Kasa hesaplarında alacak bakiyesi yok.")


ALL_RULES = [(K01, k01), (K02, k02), (K03, k03), (K04, k04), (K05, k05), (K09, k09)]
