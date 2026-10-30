"""KDV ön kontrolleri (Aşama 1): K-21, K-22, K-22D, K-23.

Bunlar beyanname hazırlığına ön kontroldür; beyan, hesaplama veya vergi görüşü yerine geçmez.
Hesap kodları Tekdüzen'deki genel kullanımdır (190 Devreden KDV, 191 İndirilecek KDV, 391 Hesaplanan KDV,
360 Ödenecek Vergi ve Fonlar, 600-602 satışlar, 610-611 satış indirimleri). Oranlar koda gömülmez.
"""

from calendar import monthrange
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from ..config import DEGISIM_ORAN, DEGISIM_TUTAR_KURUS
from ..parsing import fold, kurus_str
from .rules import D0, Context, Draft, Evidence, Result, Rule

KDV_DAYANAK = ("Tekdüzen Hesap Planı'nda 190/191/360/391 hesaplarının işleyişi ve KDV Kanunu'ndaki indirim mekanizması. "
               "Dayanak kaynağı henüz kullanıcı tarafından doğrulanmadı.")
SATIS = ("600", "601", "602")
SATIS_INDIRIM = ("610", "611")


def _month_end(d: date) -> bool:
    return d.day == monthrange(d.year, d.month)[1]


def _single_month(ctx: Context) -> bool:
    s, e = ctx.period_start, ctx.period_end
    return s.day == 1 and (s.year, s.month) == (e.year, e.month) and _month_end(e)


def _pct(x: float) -> str:
    return f"%{x * 100:.2f}".replace(".", ",")


def _is_kdv_payable(code: str, name: str | None) -> bool:
    """360 altında KDV'ye ait alt hesap; tevkifat (2 no.lu beyan) hesapları hariç."""
    n = fold(name)
    return code.startswith("360") and "kdv" in n and "tevk" not in n


# ---- Veri yardımcıları --------------------------------------------------------

@dataclass
class Moves:
    """Bir pencere içindeki hesap hareketleri."""
    source: str
    window: tuple[date, date]
    borc: dict = field(default_factory=lambda: defaultdict(int))
    alacak: dict = field(default_factory=lambda: defaultdict(int))
    names: dict = field(default_factory=dict)
    rows: dict = field(default_factory=lambda: defaultdict(list))  # kod -> [Evidence]

    def total(self, prefixes, side: str) -> int:
        d = self.borc if side == "borc" else self.alacak
        return sum(v for k, v in d.items() if k.startswith(prefixes))

    def evidence(self, prefixes, limit: int = 30) -> list[Evidence]:
        out = []
        for k in sorted(self.rows):
            if k.startswith(prefixes):
                out += self.rows[k]
        return out[:limit]


def _covers(ctx: Context, doc: str) -> bool:
    rng = ctx.ranges.get(doc)
    if doc not in ctx.declared or not rng:
        return True
    return rng[0] <= ctx.period_start and rng[1] >= ctx.period_end


def movements(ctx: Context) -> Moves | None:
    """Dönem hareketleri. Önce yevmiye, sonra kebir (dönemi kapsıyorsa), en son mizan kullanılır."""
    for doc in ("yevmiye", "kebir"):
        lines = getattr(ctx, doc)
        if lines and _covers(ctx, doc):
            label = "yevmiye" if doc == "yevmiye" else "defteri kebir"
            m = Moves(label, (ctx.period_start, ctx.period_end))
            for j in lines:
                if ctx.period_start <= j.tarih <= ctx.period_end:
                    m.borc[j.hesap_kodu] += j.borc
                    m.alacak[j.hesap_kodu] += j.alacak
                    if j.hesap_adi:
                        m.names[j.hesap_kodu] = j.hesap_adi
                    m.rows[j.hesap_kodu].append(Evidence(j.raw_row_id, f"{label.capitalize()} {j.tarih:%d.%m.%Y} "
                                                                       f"{j.madde_no or j.fis_no}, {j.hesap_kodu}"))
            return m
    if ctx.mizan:
        m = Moves("mizan", ctx.ranges.get("mizan") or (ctx.period_start, ctx.period_end))
        for ln in ctx.leaves:
            m.borc[ln.hesap_kodu] += ln.borc
            m.alacak[ln.hesap_kodu] += ln.alacak
            m.names[ln.hesap_kodu] = ln.hesap_adi
            m.rows[ln.hesap_kodu].append(Evidence(ln.raw_row_id, f"Mizan satırı {ln.hesap_kodu}"))
        return m
    return None


@dataclass
class Balance:
    net: int  # borç bakiyesi pozitif, alacak bakiyesi negatif
    source: str
    evidence: list


def _kebir_balance(ctx: Context, prefix: str, until: date, before: bool) -> Balance | None:
    ana = prefix[:3]
    if not ctx.kebir or prefix != ana:
        return None  # kebirde nakli yekün ana hesap düzeyinde; alt hesaba bölünemez
    opens = [o for o in ctx.openings.get("kebir", []) if o.hesap_kodu == ana]
    lines = [j for j in ctx.kebir if j.hesap_kodu.startswith(ana) and (j.tarih < until if before else j.tarih <= until)]
    # Kebirde hiç görünmeyen hesabın bakiyesi sıfırdır.
    net = sum(o.borc - o.alacak for o in opens) + sum(j.borc - j.alacak for j in lines)
    ev = [Evidence(o.raw_row_id, f"Kebir nakli yekün {ana}") for o in opens]
    ev += [Evidence(j.raw_row_id, f"Kebir {j.tarih:%d.%m.%Y} {j.madde_no or j.fis_no}, {j.hesap_kodu}") for j in lines[-10:]]
    return Balance(net, "defteri kebir, nakli yekün dahil", ev)


def closing(ctx: Context, prefix: str) -> Balance | None:
    """Dönem sonu bakiyesi. Yalnızca hareket içeren (devirsiz) mizandan bakiye alınmaz."""
    if ctx.mizan and not ctx.mizan_hareket:
        rng = ctx.ranges.get("mizan")
        if not rng or rng[1] == ctx.period_end:
            leaves = [ln for ln in ctx.leaves if ln.hesap_kodu.startswith(prefix)]
            net = 0
            for ln in leaves:
                if ln.bakiye_borc is not None or ln.bakiye_alacak is not None:
                    net += (ln.bakiye_borc or 0) - (ln.bakiye_alacak or 0)
                else:
                    net += (ln.acilis_borc or 0) - (ln.acilis_alacak or 0) + ln.borc - ln.alacak
            return Balance(net, "mizan", [Evidence(ln.raw_row_id, f"Mizan satırı {ln.hesap_kodu}") for ln in leaves])
    return _kebir_balance(ctx, prefix, ctx.period_end, before=False)


def opening(ctx: Context, prefix: str) -> Balance | None:
    """Dönem başı bakiyesi: kebirdeki nakli yekün (+ dönem öncesi satırlar) veya mizandaki açılış kolonları."""
    b = _kebir_balance(ctx, prefix, ctx.period_start, before=True)
    if b:
        return b
    if ctx.mizan and not ctx.mizan_hareket and any(ln.acilis_borc is not None for ln in ctx.leaves):
        leaves = [ln for ln in ctx.leaves if ln.hesap_kodu.startswith(prefix)]
        return Balance(sum((ln.acilis_borc or 0) - (ln.acilis_alacak or 0) for ln in leaves), "mizan açılış kolonları",
                       [Evidence(ln.raw_row_id, f"Mizan satırı {ln.hesap_kodu}") for ln in leaves])
    return None


# ---- K-21 KDV mahsup kaydı ----------------------------------------------------

K21 = Rule("K-21", "1.0", "KDV mahsup kaydı",
           "Ay sonunda 191 İndirilecek KDV ve 391 Hesaplanan KDV birlikte bakiye veriyorsa ay sonu mahsup kaydı yapılmamış olabilir.",
           "K", ("THP_VUK",), (), KDV_DAYANAK, False,
           "Ay sonu kayıtları henüz girilmemiş ara veri; büronun farklı mahsup pratiği (ör. üç aylık).", D0, "Orta")


def k21(ctx: Context) -> Result:
    if not _month_end(ctx.period_end):
        return Result(K21, "uygulanmadi", "Uygulanmadı: dönem ay sonunda bitmiyor.")
    c191, c391 = closing(ctx, "191"), closing(ctx, "391")
    if c191 and c391:
        b191, b391, source, kind = c191.net, c391.net, c191.source, "bakiye"
        ev = c191.evidence + c391.evidence
    else:
        m = movements(ctx)
        if m is None:
            return Result(K21, "calistirilamadi", "Çalıştırılamadı: mizan, yevmiye defteri veya defteri kebir yüklenmemiş.")
        b191 = m.total(("191",), "borc") - m.total(("191",), "alacak")
        b391 = m.total(("391",), "borc") - m.total(("391",), "alacak")
        source, kind = f"{m.source}, {m.window[0]:%d.%m.%Y} – {m.window[1]:%d.%m.%Y} hareketleri", "hareket"
        ev = m.evidence(("191", "391"))
    if b191 > ctx.tolerance and b391 < -ctx.tolerance:
        what = "bakiye veriyor" if kind == "bakiye" else "hareketlerinin farkı kapanmamış"
        expl = (f"{ctx.period_end:%d.%m.%Y} itibarıyla 191 İndirilecek KDV {kurus_str(b191)} TL borç, "
                f"391 Hesaplanan KDV {kurus_str(-b391)} TL alacak {what} ({source}).")
        o = opening(ctx, "191")
        if o and o.net > ctx.tolerance:
            expl += (f" Dönem başında da 191'de {kurus_str(o.net)} TL borç bakiyesi vardı; "
                     "önceki ayların mahsubu da yapılmamış olabilir.")
        certainty = ("Bakiyeler aritmetik olarak kesin. Mahsup kaydının yapılmadığı kesin değil: kayıt bu veri alındıktan sonra "
                     "girilmiş veya büro mahsubu farklı bir sıklıkla yapıyor olabilir.")
        if kind == "hareket":
            certainty += " Devir bilinmediği için yalnızca dönem içi hareketlere bakıldı."
        return Result(K21, "bulgu", "191 ve 391 dönem sonunda birlikte bakiye veriyor.", [Draft(
            "KDV mahsup kaydı yapılmamış olabilir", K21.default_severity, "191", min(b191, -b391), "mahsup", expl, certainty,
            "Ay sonu KDV mahsup fişinin (391 borç, 191 alacak, fark 190 veya 360'a) Luca'da girilip girilmediğini kontrol edin.",
            ev)])
    return Result(K21, "gecti", "Dönem sonunda 191 ve 391 birlikte bakiye vermiyor.")


# ---- K-22 Devreden KDV sürekliliği -----------------------------------------------

K22 = Rule("K-22", "1.0", "Devreden KDV sürekliliği",
           "Önceki ay sonundaki 190 Devreden KDV bakiyesi, bu dönemin başındaki 190 bakiyesine eşit olmalı.",
           "A", ("THP_VUK",), (), KDV_DAYANAK, False,
           "Önceki dönemin verisi sonradan değişmiş olabilir; her iki dönemin belgeleri aynı anda alınmalı.", D0, "Orta")
K22D = Rule("K-22D", "1.0", "Devreden KDV varken ödenecek KDV",
            "Aynı ay içinde hem 190'da devreden KDV hem de 360'ta ödenecek KDV oluşması incelenmeli.",
            "D", ("THP_VUK",), (), KDV_DAYANAK, False,
            "360 altındaki KDV hesabı adından tanınır ('KDV' geçen, 'tevkifat' geçmeyen); tevkifat (2 no.lu) ayrı ödenir. "
            "İade ve mahsup talepleri.", D0, "Orta")


def _adjacent_previous(ctx: Context) -> Context | None:
    if ctx.previous and ctx.previous[0].period_end == ctx.period_start - timedelta(days=1):
        return ctx.previous[0]
    return None


def k22(ctx: Context) -> Result:
    if ctx.devreden_kdv is not None:
        return _k22_declared(ctx)
    prev = _adjacent_previous(ctx)
    if prev is None:
        return Result(K22, "uygulanmadi", "Uygulanmadı: önceki ayın devreden KDV tutarı girilmemiş ve önceki ayın "
                                          "dosyaları yüklenmemiş.")
    pc, co = closing(prev, "190"), opening(ctx, "190")
    if pc is None or co is None:
        need = "önceki dönemin sonu" if pc is None else "bu dönemin başı"
        return Result(K22, "uygulanmadi", f"Uygulanmadı: {need} için 190 bakiyesi bulunamadı "
                                          "(dönem başından alınmış mizan veya defteri kebir gerekli).")
    diff = co.net - pc.net
    if abs(diff) > ctx.tolerance:
        return Result(K22, "bulgu", "Devreden KDV önceki dönemle uyuşmuyor.", [Draft(
            "Devreden KDV önceki dönemin sonuyla uyuşmuyor", K22.default_severity, "190", diff, "sureklilik",
            f"{prev.period_end:%d.%m.%Y} sonunda 190 bakiyesi {kurus_str(pc.net)} TL ({pc.source}); "
            f"{ctx.period_start:%d.%m.%Y} başında {kurus_str(co.net)} TL ({co.source}). Fark {kurus_str(diff)} TL.",
            "Fark aritmetik olarak kesin. Önceki dönemde sonradan kayıt değişmiş veya belgeler farklı zamanlarda alınmış olabilir.",
            "Önceki ayın KDV beyannamesindeki devreden KDV ile iki tutarı karşılaştırın; önceki dönemin belgelerini güncel haliyle yeniden yükleyin.",
            pc.evidence + co.evidence)])
    return Result(K22, "gecti", f"190 devreden KDV önceki dönemden doğru devretmiş ({kurus_str(co.net)} TL).")


def _k22_declared(ctx: Context) -> Result:
    """Beyannameden girilen devreden KDV ile defterdeki dönem başı 190 bakiyesini karşılaştırır."""
    co = opening(ctx, "190")
    if co is None:
        return Result(K22, "uygulanmadi", "Uygulanmadı: bu dönemin başı için 190 bakiyesi bulunamadı "
                                          "(dönem başından alınmış mizan veya defteri kebir gerekli).")
    diff = co.net - ctx.devreden_kdv
    src = ("Önceki ayın beyannamesindeki (PDF) devreden KDV" if ctx.devreden_kaynak == "beyanname"
           else "Girilen beyanname tutarı")
    if abs(diff) > ctx.tolerance:
        return Result(K22, "bulgu", "Devreden KDV beyannameyle uyuşmuyor.", [Draft(
            "Defterdeki devreden KDV beyannameyle uyuşmuyor", K22.default_severity, "190", diff, "beyan",
            f"{src} {kurus_str(ctx.devreden_kdv)} TL; {ctx.period_start:%d.%m.%Y} başında 190 bakiyesi "
            f"{kurus_str(co.net)} TL ({co.source}). Fark {kurus_str(diff)} TL.",
            "Tutar yüklenen beyanname PDF'inden okundu." if ctx.devreden_kaynak == "beyanname"
            else "Tutar elle girildi; yazım hatası olmadığını kontrol edin.",
            "Önceki ayın KDV beyannamesindeki 'sonraki döneme devreden KDV' satırını ve 190 hesabının devir kaydını karşılaştırın.",
            co.evidence)])
    return Result(K22, "gecti", f"190 dönem başı bakiyesi beyannamedeki devreden KDV ile aynı ({kurus_str(co.net)} TL).")


def k22d(ctx: Context) -> Result:
    if _single_month(ctx):
        m = movements(ctx)
        if m is None or m.window != (ctx.period_start, ctx.period_end):
            return Result(K22D, "uygulanmadi", "Uygulanmadı: bu ayın hareketleri dönemle aynı aralıkta değil.")
        dev = m.total(("190",), "borc")
        payable = {k: v for k, v in m.alacak.items() if _is_kdv_payable(k, m.names.get(k)) and v > 0}
        basis = f"{m.source} hareketleri"
        ev = m.evidence(("190",)) + [e for k in payable for e in m.rows[k]][:20]
    else:
        c190 = closing(ctx, "190")
        if c190 is None or not ctx.mizan or ctx.mizan_hareket:
            return Result(K22D, "uygulanmadi", "Uygulanmadı: tek aylık dönem veya dönem sonu bakiyeli mizan gerekli.")
        dev = c190.net
        payable = {}
        for ln in ctx.leaves:
            net = (ln.bakiye_alacak or 0) - (ln.bakiye_borc or 0)
            if _is_kdv_payable(ln.hesap_kodu, ln.hesap_adi) and net > 0:
                payable[ln.hesap_kodu] = net
        basis = "dönem sonu bakiyeleri"
        ev = c190.evidence + [Evidence(ln.raw_row_id, f"Mizan satırı {ln.hesap_kodu}") for ln in ctx.leaves if ln.hesap_kodu in payable]
    total = sum(payable.values())
    if dev > ctx.tolerance and total > ctx.tolerance:
        return Result(K22D, "bulgu", "Devreden KDV ile ödenecek KDV aynı anda oluşmuş.", [Draft(
            "Devreden KDV varken ödenecek KDV oluşmuş", K22D.default_severity, "360", total, "devreden-odenecek",
            f"Aynı dönemde 190 Devreden KDV {kurus_str(dev)} TL borç ve 360 altındaki KDV hesaplarında "
            f"({', '.join(sorted(payable))}) {kurus_str(total)} TL alacak oluşmuş ({basis}).",
            "Normalde devreden KDV varken ödenecek KDV çıkmaz. Hesap eşlemesi, iade/mahsup talebi veya farklı bir KDV türü "
            "(ör. ayrı beyan edilen bir vergi) açıklayabilir; mesleki değerlendirme gerekir.",
            "Ayın KDV beyannamesindeki devreden ve ödenecek tutarları bu iki hesapla karşılaştırın.", ev)])
    return Result(K22D, "gecti", "Devreden KDV ile ödenecek KDV aynı anda oluşmamış.")


# ---- K-23 Hesaplanan KDV / hasılat oranı -----------------------------------------

K23 = Rule("K-23", "1.0", "Hesaplanan KDV / satış oranı",
           "Etkin oran = 391 alacak hareketi / net satış (600-602 alacak − 610-611 borç). Önceki dönemlerin ortalamasından "
           f"hem %{int(DEGISIM_ORAN * 100)} hem {kurus_str(DEGISIM_TUTAR_KURUS)} TL'den fazla sapma incelenir.",
           "I", ("THP_VUK",), (), "İstatistiksel karşılaştırma; bir kural ihlali değildir.", True,
           "Farklı KDV oranları, istisnalı ve tevkifatlı satışlar, ihracat, iadeler. Tek başına hata göstermez.", D0, "Orta")


def kdv_rate(ctx: Context):
    """(hesaplanan KDV, net satış, pencere, kaynak) veya None."""
    m = movements(ctx)
    if m is None:
        return None
    hes = m.total(("391",), "alacak")
    sales = m.total(SATIS, "alacak") - m.total(SATIS_INDIRIM, "borc")
    if sales <= 0:
        return None
    return hes, sales, m.window, m.source, m


def k23(ctx: Context) -> Result:
    cur = kdv_rate(ctx)
    if cur is None:
        if movements(ctx) is None:
            return Result(K23, "calistirilamadi", "Çalıştırılamadı: mizan, yevmiye defteri veya defteri kebir yüklenmemiş.")
        return Result(K23, "uygulanmadi", "Uygulanmadı: dönemde net satış hasılatı yok.")
    hes, sales, win, source, m = cur
    rate = hes / sales
    head = (f"Etkin oran {_pct(rate)} (hesaplanan KDV {kurus_str(hes)} / net satış {kurus_str(sales)} TL; "
            f"{source}, {win[0]:%d.%m.%Y} – {win[1]:%d.%m.%Y}).")
    if win != (ctx.period_start, ctx.period_end):
        return Result(K23, "bilgi", head + " Pencere dönemle aynı olmadığı için önceki dönemlerle karşılaştırılmadı.")
    prev = []
    for p in ctx.previous:
        r = kdv_rate(p)
        if r and r[2] == (p.period_start, p.period_end):
            prev.append((p, r[0] / r[1]))
    if not prev:
        return Result(K23, "bilgi", head + " Karşılaştırılacak önceki dönem yok.")
    avg = sum(r for _, r in prev) / len(prev)
    hist = ", ".join(f"{p.period_start:%m.%Y}: {_pct(r)}" for p, r in prev)
    dev = rate - avg
    impact = round(dev * sales)
    if avg > 0 and abs(dev) / avg > DEGISIM_ORAN and abs(impact) > DEGISIM_TUTAR_KURUS:
        return Result(K23, "bulgu", "Etkin KDV oranı önceki dönemlerden belirgin farklı.", [Draft(
            "Hesaplanan KDV / satış oranı önceki dönemlerden farklı", K23.default_severity, "391", impact, "oran",
            f"{head} Önceki dönemlerin ortalaması {_pct(avg)} ({hist}). Ortalama oranla hesaplanan KDV "
            f"{kurus_str(round(avg * sales))} TL olurdu; fark {kurus_str(impact)} TL.",
            "İstatistiksel bir sapmadır, hata kanıtı değildir. Satış karmasındaki değişim (ihracat, istisnalı veya farklı oranlı "
            "satışlar), iadeler ya da eksik/fazla KDV kaydı açıklayabilir.",
            "Dönemin satış faturalarını KDV oranına göre gruplayın; istisnalı satışları ve 391 alt hesaplarını kontrol edin.",
            m.evidence(("391",)) + m.evidence(SATIS, 10))])
    return Result(K23, "gecti", f"{head} Önceki dönemlerin ortalaması {_pct(avg)} ({hist}); belirgin sapma yok.")


# ---- K-24 Beyanname ile defter karşılaştırması ----------------------------------

K24 = Rule("K-24", "1.0", "KDV beyannamesi ile defter",
           "Ayın KDV beyannamesindeki hesaplanan KDV, bu döneme ait indirilecek KDV ve sonraki döneme devreden KDV; "
           "391 alacak, 191 borç hareketleri ve ay sonu 190 bakiyesiyle karşılaştırılır.",
           "K", ("THP_VUK",), (), KDV_DAYANAK, False,
           "391/191'e beyannameye girmeyen kayıtlar (iade, iptal, KKEG'e atılan KDV, tevkifat) veya beyannameden sonra "
           "yapılan düzeltmeler fark yaratabilir.", D0, "Orta")


def k24(ctx: Context) -> Result:
    b = ctx.kdv_beyan
    if not b:
        return Result(K24, "uygulanmadi", "Uygulanmadı: bu ayın KDV beyannamesi (PDF) yüklenmemiş.")
    m = movements(ctx)
    if m is None:
        return Result(K24, "calistirilamadi", "Çalıştırılamadı: dönem hareketleri yok.")
    rows = []
    if b.get("hesaplanan") is not None:
        rows.append(("391", "Hesaplanan KDV", b["hesaplanan"], m.total(("391",), "alacak"), m.source, "391 alacak hareketi",
                     m.evidence(("391",))))
    if b.get("indirimler_toplami") is not None:
        bu_donem = b["indirimler_toplami"] - (b.get("onceki_devreden") or 0)
        rows.append(("191", "Bu döneme ait indirilecek KDV", bu_donem, m.total(("191",), "borc"), m.source,
                     "191 borç hareketi", m.evidence(("191",))))
    c190 = closing(ctx, "190")
    if b.get("sonraki_devreden") is not None and c190 is not None:
        rows.append(("190", "Sonraki döneme devreden KDV", b["sonraki_devreden"], c190.net, c190.source,
                     "ay sonu 190 bakiyesi", c190.evidence))
    drafts, ok = [], []
    for kod, ad, beyan, defter, src, nerede, ev in rows:
        diff = defter - beyan
        if abs(diff) > ctx.tolerance:
            drafts.append(Draft(f"{ad} beyannameyle defterde farklı", K24.default_severity, kod, diff, f"beyan-{kod}",
                                f"Beyannamede {kurus_str(beyan)} TL; defterde {nerede} {kurus_str(defter)} TL ({src}). "
                                f"Fark {kurus_str(diff)} TL.",
                                K24.exceptions,
                                f"{kod} hesabının ay içindeki kayıtlarını beyannameyle satır satır karşılaştırın.", ev))
        else:
            ok.append(ad.lower())
    if not rows:
        return Result(K24, "calistirilamadi", "Çalıştırılamadı: beyannamede karşılaştırılacak tutar okunamadı.")
    if drafts:
        return Result(K24, "bulgu", f"{len(drafts)} tutar beyannameyle uyuşmuyor.", drafts)
    return Result(K24, "gecti", "Beyanname ile defter uyumlu: " + ", ".join(ok) + ".")


KDV_RULES = [(K21, k21), (K22, k22), (K22D, k22d), (K23, k23), (K24, k24)]
