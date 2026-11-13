"""Stopaj (gelir vergisi tevkifatı) kontrolleri: kira ve serbest meslek ödemelerinde kesinti ve 360 hesabının kapanması.

Muhtasar beyannamesiyle karşılaştırma için beyanname örneği bekleniyor; şimdilik defter içi tutarlılığa bakılır.
"""

from collections import defaultdict

from ..parametreler import STOPAJ
from ..parsing import fold, kurus_str
from .rules import D0, Context, Draft, Evidence, Result, Rule

K34 = Rule("K-34", "1.0", "Kira ve serbest meslek ödemelerinde stopaj",
           "Adında kira ya da serbest meslek geçen gider kayıtlarının yevmiye maddesine bakılır. Maddede 360 (ödenecek "
           "vergi) alacak kaydı varsa tutarın brüt giderin %20'si olup olmadığı; KDV (191) ve 360 kaydı hiç yoksa kesinti "
           "yapılmamış olabileceği gösterilir.",
           "D", ("THP_VUK",), ("yevmiye",), STOPAJ["dayanak"], STOPAJ["dogrulandi"],
           "Şirketlerden ve KDV mükelleflerinden kiralanan yerler için stopaj yapılmaz (fatura, KDV ile gelir). Kira "
           "tahakkuku ve ödeme ayrı maddelerde yapılmış olabilir; net tutar üzerinden brütleştirme de farklı görünebilir.",
           D0, "Orta")

K35 = Rule("K-35", "1.0", "Stopajların izleyen ayda ödenmesi",
           "360 hesabının stopaj (gelir vergisi kesintisi, muhtasar) alt hesaplarında bir ay içinde tahakkuk eden tutarın "
           "izleyen ay içinde ödenip (360 borç) ödenmediğine bakılır. Dönemin son ayı, izleyen ayın kaydı olmadığı için "
           "değerlendirilmez.",
           "D", ("THP_VUK",), ("yevmiye",), STOPAJ["dayanak"], STOPAJ["dogrulandi"],
           "Ödeme 360 dışındaki bir hesaptan (ör. 336) yapılmışsa ya da birkaç ay birlikte ödenmişse fark görünebilir.",
           D0, "Orta")

KIRA = ("kira",)
KIRA_HARIC = ("kiralama geliri", "kira geliri", "finansal kiralama", "leasing")
SMM = ("serbest meslek", "avukat", "musavir", "müşavir", "danisman", "danışman", "noter", "doktor", "hekim")
STOPAJ_AD = ("stopaj", "gelir vergisi", "muhtasar", "tevkifat", "kesinti")


def _adlar(ctx: Context) -> dict:
    ad = {}
    for src in (ctx.ytd, ctx.yevmiye, ctx.mizan):
        for x in src or []:
            if x.hesap_adi:
                ad[x.hesap_kodu] = x.hesap_adi
    return ad


def _tam_ad(ad: dict, kod: str) -> str:
    p = kod.split(".")
    return " ".join(fold(ad.get(".".join(p[:i]), "")) for i in range(1, len(p) + 1))


def _gider(kod: str) -> bool:
    return kod[:1] == "7" and not kod.startswith("79") or kod.startswith(("63", "65", "66", "68"))


def _tur(isim: str) -> str | None:
    if any(fold(w) in isim for w in KIRA_HARIC):
        return None
    if any(fold(w) in isim for w in SMM):
        return "serbest_meslek"
    if any(fold(w) in isim for w in KIRA):
        return "kira"
    return None


def _stopaj_hesabi(ad: dict, kod: str) -> bool:
    if not kod.startswith("360"):
        return False
    isim = _tam_ad(ad, kod)
    return any(fold(w) in isim for w in STOPAJ_AD) and "kdv" not in isim.split() and "sgk" not in isim


def _maddeler(ctx: Context):
    lines = [j for j in ctx.yevmiye or [] if ctx.period_start <= j.tarih <= ctx.period_end]
    g = defaultdict(list)
    for j in lines:
        g[(j.madde_no or j.fis_no, j.tarih)].append(j)
    return g


def k34(ctx: Context) -> Result:
    if not ctx.yevmiye:
        return Result(K34, "calistirilamadi", "Çalıştırılamadı: yevmiye yüklenmemiş.")
    ad = _adlar(ctx)
    tutmayan, kesintisiz, bakilan = [], [], 0
    for (madde, tarih), satirlar in _maddeler(ctx).items():
        giderler = defaultdict(int)
        for j in satirlar:
            if _gider(j.hesap_kodu) and j.borc:
                t = _tur(_tam_ad(ad, j.hesap_kodu) + " " + fold(j.aciklama))
                if t:
                    giderler[t] += j.borc - j.alacak
        if not giderler:
            continue
        bakilan += 1
        stopaj = sum(j.alacak - j.borc for j in satirlar if _stopaj_hesabi(ad, j.hesap_kodu))
        if not stopaj:
            stopaj = sum(j.alacak - j.borc for j in satirlar if j.hesap_kodu.startswith("360"))
        kdv = any(j.hesap_kodu.startswith("191") and j.borc for j in satirlar)
        brut = sum(giderler.values())
        ilk = satirlar[0]
        if stopaj > 0:
            oran = STOPAJ[max(giderler, key=giderler.get)]
            beklenen = round(brut * oran)
            if abs(stopaj - beklenen) > max(ctx.tolerance, beklenen // 100):
                tutmayan.append((madde, tarih, brut, stopaj, beklenen, ilk))
        elif not kdv:
            kesintisiz.append((madde, tarih, brut, "+".join(sorted(giderler)), ilk))
    if not bakilan:
        return Result(K34, "gecti", "Dönemde adında kira veya serbest meslek geçen gider kaydı yok.")
    drafts = []
    if tutmayan:
        fark = sum(abs(s - b) for _, _, _, s, b, _ in tutmayan)
        ornek = "; ".join(f"madde {m} ({t:%d.%m.%Y}): gider {kurus_str(g)} TL, stopaj {kurus_str(s)} TL, %20'si {kurus_str(b)} TL"
                          for m, t, g, s, b, _ in tutmayan[:8])
        drafts.append(Draft("Stopaj tutarı brüt giderin %20'si değil", "Orta", "360", fark, "stopaj-oran",
                            f"{len(tutmayan)} maddede kira/serbest meslek giderine karşılık 360'a alınan stopaj %20'den farklı. {ornek}.",
                            "Tutarlar kesin. " + K34.exceptions,
                            "Sözleşmedeki tutarın brüt mü net mi olduğuna bakın; netse brüt = net / 0,80. Farklı oran "
                            "uygulanan özel durum yoksa stopajı düzeltin ve muhtasar beyanla karşılaştırın.",
                            [Evidence(j.raw_row_id, f"{t:%d.%m.%Y} madde {m}") for m, t, _, _, _, j in tutmayan[:10]]))
    if kesintisiz:
        toplam = sum(g for _, _, g, _, _ in kesintisiz)
        ornek = "; ".join(f"madde {m} ({t:%d.%m.%Y}) {tur.replace('_', ' ')} {kurus_str(g)} TL" for m, t, g, tur, _ in kesintisiz[:8])
        drafts.append(Draft("Kira veya serbest meslek giderinde stopaj kaydı yok", "Orta", None, round(toplam * 0.20),
                            "stopaj-yok",
                            f"{len(kesintisiz)} maddede kira/serbest meslek gideri var; aynı maddede ne KDV (191) ne de "
                            f"stopaj (360) kaydı var. Toplam {kurus_str(toplam)} TL. {ornek}.",
                            "Gider tutarı kesin; karşı tarafın şirket mi şahıs mı olduğu veriden anlaşılamaz. " + K34.exceptions,
                            "Kiraya veren/hizmeti veren gerçek kişiyse %20 stopaj kesilip muhtasarla beyan edilmeliydi; "
                            "kira sözleşmesine ve ödeme belgesine bakın.",
                            [Evidence(j.raw_row_id, f"{t:%d.%m.%Y} madde {m}") for m, t, _, _, j in kesintisiz[:10]]))
    if drafts:
        return Result(K34, "bulgu", f"{bakilan} kira/serbest meslek maddesinden {len(tutmayan) + len(kesintisiz)} tanesine bakılmalı.", drafts)
    return Result(K34, "gecti", f"{bakilan} kira/serbest meslek maddesinde stopaj kaydı tutarlı ya da fatura (KDV) ile.")


def k35(ctx: Context) -> Result:
    if not ctx.yevmiye:
        return Result(K35, "calistirilamadi", "Çalıştırılamadı: yevmiye yüklenmemiş.")
    ad = _adlar(ctx)
    kaynak = ctx.ytd or ctx.yevmiye
    tah, ode, rows = defaultdict(int), defaultdict(int), defaultdict(list)
    for j in kaynak:
        if not _stopaj_hesabi(ad, j.hesap_kodu):
            continue
        ay = (j.tarih.year, j.tarih.month)
        tah[ay] += j.alacak
        ode[ay] += j.borc
        rows[ay].append(j)
    aylar = sorted(a for a in tah if tah[a] and (ctx.period_start.year, ctx.period_start.month) <= a
                   < (ctx.period_end.year, ctx.period_end.month))
    if not tah:
        return Result(K35, "gecti", "360 hesabında adında stopaj/gelir vergisi geçen alt hesap hareketi yok.")
    if not aylar:
        return Result(K35, "bilgi", "Stopaj tahakkuku yalnızca dönemin son ayında; ödemesi izleyen ayda görüleceği için değerlendirilmedi.")
    eksik = []
    for y, m in aylar:
        sonraki = (y + (m == 12), 1 if m == 12 else m + 1)
        if ode[sonraki] + ctx.tolerance < tah[(y, m)]:
            eksik.append(((y, m), tah[(y, m)], ode[sonraki]))
    if not eksik:
        return Result(K35, "gecti", f"{len(aylar)} ayın stopaj tahakkuku izleyen ayda 360 hesabından ödenmiş görünüyor.")
    fark = sum(t - o for _, t, o in eksik)
    ornek = "; ".join(f"{m:02d}/{y}: tahakkuk {kurus_str(t)} TL, izleyen ay ödeme {kurus_str(o)} TL" for (y, m), t, o in eksik)
    ev = [Evidence(j.raw_row_id, f"{j.tarih:%d.%m.%Y} {j.hesap_kodu} {kurus_str(j.alacak)} TL")
          for a, _, _ in eksik for j in rows[a] if j.alacak][:10]
    d = Draft("Stopajlar izleyen ayda ödenmemiş görünüyor", "Orta", "360", fark, "stopaj-odeme",
              f"360 stopaj alt hesaplarında: {ornek}.", "Tutarlar kesin. " + K35.exceptions,
              "Muhtasar beyannamesinin verilip tahakkukun ödendiğini kontrol edin; ödendiyse ödeme kaydını 360'a yapın, "
              "ödenmediyse gecikme zammı doğar.", ev)
    return Result(K35, "bulgu", f"{len(eksik)} ayın stopajı izleyen ayda tam ödenmemiş görünüyor.", [d])


STOPAJ_RULES = [(K34, k34), (K35, k35)]
