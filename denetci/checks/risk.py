"""Hata ve risk taraması: ters bakiye, kasadan yüksek tutarlı nakit hareketi, tatil günü gideri, mükerrer fatura.

Bu kontroller kesin hata göstermez; bakılması gereken kayıtları öne çıkarır.
"""

from collections import defaultdict
from datetime import date, timedelta

from ..efatura import fisler
from ..parametreler import DINI_BAYRAMLAR, SABIT_TATILLER, TEVSIK
from ..parsing import kurus_str
from .rules import D0, Context, Draft, Evidence, Result, Rule

# Aktifte yer alıp alacak bakiyesi vermesi olağan olan (düzenleyici) hesaplar.
AKTIF_ALACAK = {"103", "119", "122", "124", "129", "137", "139", "158", "199", "222", "224", "229", "237", "239", "241",
                "243", "244", "246", "247", "249", "257", "268", "278", "298", "299"}
# Pasifte yer alıp borç bakiyesi vermesi olağan olan hesaplar.
PASIF_BORC = {"302", "308", "322", "324", "337", "371", "402", "408", "422", "424", "437", "501", "503", "580", "591"}


def _kayitlar(ctx: Context):
    """Dönemin kayıt satırları ve hangi belgeden alındığı."""
    for doc, ad in (("yevmiye", "yevmiye"), ("kebir", "defteri kebir"), ("muavin", "muavin")):
        ls = getattr(ctx, doc)
        if ls:
            return [j for j in ls if ctx.period_start <= j.tarih <= ctx.period_end], ad
    return None, None


def _madde(j):
    return j.madde_no or j.fis_no


# ---- K-10 Ters bakiye -------------------------------------------------------

K10 = Rule("K-10", "1.0", "Ters bakiye veren hesaplar",
           "Aktif hesaplar (1 ve 2 ile başlayanlar) borç, pasif hesaplar (3, 4 ve 5 ile başlayanlar) alacak bakiyesi verir. "
           "Tersi olan hesaplar listelenir. Karşılık, birikmiş amortisman ve reeskont gibi düzenleyici hesaplar hariç tutulur.",
           "K", ("THP_VUK",), (), "Tekdüzen Hesap Planı'nda hesapların işleyişi (1 Sıra No.lu MSUGT).", False,
           "Alt hesapta ters bakiye, aynı cari için fazla ödeme veya avans anlamına gelebilir; bilançoda ilgili hesaba "
           "(ör. 120 alacak bakiyesi 340'a, 320 borç bakiyesi 159'a) aktarılması gerekip gerekmediğine bakılır.", D0, "Orta")


def _bakiyeler(ctx: Context):
    """Hesap kodu -> (net bakiye, ad, kanıt satırı) ve kaynağın adı. Net: borç +, alacak −."""
    if ctx.mizan and not ctx.mizan_hareket:
        out = {}
        for ln in ctx.leaves:
            if ln.bakiye_borc is not None or ln.bakiye_alacak is not None:
                net = (ln.bakiye_borc or 0) - (ln.bakiye_alacak or 0)
            else:
                net = (ln.acilis_borc or 0) - (ln.acilis_alacak or 0) + ln.borc - ln.alacak
            out[ln.hesap_kodu] = (net, ln.hesap_adi, ln.raw_row_id)
        return out, "mizan"
    if ctx.kebir:
        # Kebirde nakli yekün ana hesap düzeyindedir; ana hesap bazında bakılır.
        net, ad, row = defaultdict(int), {}, {}
        for o in ctx.openings.get("kebir", []):
            net[o.hesap_kodu[:3]] += o.borc - o.alacak
            row.setdefault(o.hesap_kodu[:3], o.raw_row_id)
        for j in ctx.kebir:
            if j.tarih <= ctx.period_end:
                net[j.hesap_kodu[:3]] += j.borc - j.alacak
                row[j.hesap_kodu[:3]] = j.raw_row_id
                if j.hesap_adi and j.hesap_kodu[:3] == j.hesap_kodu:
                    ad[j.hesap_kodu] = j.hesap_adi
        return {k: (v, ad.get(k), row[k]) for k, v in net.items()}, "defteri kebir (ana hesap bazında)"
    if ctx.ytd:
        net, ad, row = defaultdict(int), {}, {}
        for j in ctx.ytd:
            if j.tarih <= ctx.period_end:
                net[j.hesap_kodu] += j.borc - j.alacak
                row[j.hesap_kodu] = j.raw_row_id
                if j.hesap_adi:
                    ad[j.hesap_kodu] = j.hesap_adi
        return {k: (v, ad.get(k), row[k]) for k, v in net.items()}, "yılbaşından yevmiye"
    return None, None


def k10(ctx: Context) -> Result:
    bak, kaynak = _bakiyeler(ctx)
    if bak is None:
        return Result(K10, "calistirilamadi", "Çalıştırılamadı: dönem sonu bakiyeleri için yılbaşından alınmış mizan, "
                                              "defteri kebir veya yılbaşından yevmiye gerekli.")
    ters = defaultdict(list)
    for kod, (net, ad, row) in bak.items():
        ana, sinif = kod[:3], kod[:1]
        if abs(net) <= ctx.tolerance or len(ana) < 3 or ana == "100":  # 100 Kasa K-09 kontrolünde
            continue
        if sinif in "12" and ana not in AKTIF_ALACAK and net < 0:
            ters[ana].append((kod, ad, net, row))
        elif sinif in "345" and ana not in PASIF_BORC and net > 0:
            ters[ana].append((kod, ad, net, row))
    if not ters:
        return Result(K10, "gecti", f"Ters bakiye veren hesap yok ({kaynak}).")
    drafts = []
    for ana in sorted(ters):
        rows = sorted(ters[ana], key=lambda r: -abs(r[2]))
        toplam = sum(abs(r[2]) for r in rows)
        yon = "alacak" if ana[0] in "12" else "borç"
        liste = "; ".join(f"{k} {a or ''}".strip() + f": {kurus_str(abs(n))} TL" for k, a, n, _ in rows[:12])
        drafts.append(Draft(
            f"{ana} hesabında {len(rows)} hesap {yon} bakiyesi veriyor" if len(rows) > 1 or rows[0][0] != ana
            else f"{ana} hesabı {yon} bakiyesi veriyor",
            ctx.rule_severity(toplam), ana, toplam, f"ters-{ana}",
            f"{'Aktif' if ana[0] in '12' else 'Pasif'} karakterli {ana} hesabında {yon} bakiyesi veren hesaplar ({kaynak}): {liste}"
            + (" …" if len(rows) > 12 else "") + f". Toplam {kurus_str(toplam)} TL.",
            "Bakiye aritmetik olarak kesin. Hata olup olmadığı belirsiz: " + K10.exceptions,
            "Hesabın muavinini açın; yanlış hesaba ya da yanlış cariye işlenmiş kayıt, eksik fatura veya mükerrer ödeme "
            "olup olmadığına bakın. Gerçekten avans ya da fazla ödeme ise dönem sonunda ilgili hesaba aktarın.",
            [Evidence(r, f"{k} bakiyesi") for k, _, _, r in rows[:12]]))
    return Result(K10, "bulgu", f"{len(drafts)} ana hesapta ters bakiye var ({kaynak}).", drafts)


# ---- K-11 Kasadan yüksek tutarlı tahsilat ve ödeme ---------------------------

K11 = Rule("K-11", "1.0", "Kasadan yüksek tutarlı tahsilat ve ödeme",
           "100 Kasa hesabından tek kayıtta sınırı aşan tahsilat ve ödemeler listelenir. Bankadan çekilen veya bankaya "
           "yatırılan tutarlar (karşı hesabı yalnızca banka olan kayıtlar) sayılmaz.",
           "K", ("THP_VUK",), (), TEVSIK["dayanak"], TEVSIK["dogrulandi"],
           "Sınır işlem bazındadır; aynı gün aynı kişiyle yapılan işlemler toplanarak değerlendirilir, bu kontrol kişiyi "
           "bilmediği için yalnızca tek kayıtları gösterir. Ücret ödemeleri gibi ayrı kurala bağlı ödemeler de listeye girebilir.",
           D0, "Orta")

NAKIT_DISI = ("100", "101", "102", "103", "108")


def k11(ctx: Context) -> Result:
    lines, kaynak = _kayitlar(ctx)
    if lines is None:
        return Result(K11, "calistirilamadi", "Çalıştırılamadı: yevmiye, defteri kebir veya muavin yüklenmemiş.")
    limit = TEVSIK["limit"]
    karsi = defaultdict(set)
    if kaynak == "yevmiye":
        for j in lines:
            karsi[(j.fis_no, j.tarih)].add(j.hesap_kodu[:3])
    bulunan = {"tahsilat": [], "odeme": []}
    for j in lines:
        if not j.hesap_kodu.startswith("100"):
            continue
        tutar, tur = (j.borc, "tahsilat") if j.borc >= j.alacak else (j.alacak, "odeme")
        if tutar <= limit:
            continue
        diger = karsi.get((j.fis_no, j.tarih), set()) - {"100"}
        if diger and all(k in NAKIT_DISI for k in diger):
            continue  # kasa–banka virmanı
        bulunan[tur].append((j, tutar))
    drafts = []
    for tur, ad in (("tahsilat", "tahsilat"), ("odeme", "ödeme")):
        rows = sorted(bulunan[tur], key=lambda r: -r[1])
        if not rows:
            continue
        toplam = sum(t for _, t in rows)
        liste = "; ".join(f"{j.tarih:%d.%m.%Y} madde {_madde(j)}: {kurus_str(t)} TL"
                          + (f" ({j.aciklama.strip()[:50]})" if j.aciklama and j.aciklama.strip() else "") for j, t in rows[:15])
        drafts.append(Draft(f"Kasadan {kurus_str(limit)} TL üstü {len(rows)} {ad}", "Orta", "100", toplam, f"kasa-{tur}",
                            f"100 Kasa hesabında tek kayıtta {kurus_str(limit)} TL'yi aşan {ad} kayıtları ({kaynak}): {liste}"
                            + (" …" if len(rows) > 15 else "") + f". Toplam {kurus_str(toplam)} TL.",
                            "Kayıtlar kesin. Uygunsuzluk olup olmadığı belirsiz: " + K11.exceptions,
                            "Bu tahsilat ve ödemelerin gerçekten elden mi yapıldığını kontrol edin. Banka üzerinden yapıldıysa "
                            "kaydı banka hesabına düzeltin; elden yapıldıysa mükellefi bilgilendirin.",
                            [Evidence(j.raw_row_id, f"{j.tarih:%d.%m.%Y} madde {_madde(j)}, {kurus_str(t)} TL") for j, t in rows[:20]]))
    if drafts:
        return Result(K11, "bulgu", f"Kasada sınırı aşan {sum(len(v) for v in bulunan.values())} kayıt var.", drafts)
    return Result(K11, "gecti", f"Kasada tek kayıtta {kurus_str(limit)} TL'yi aşan tahsilat veya ödeme yok ({kaynak}).")


# ---- K-12 Tatil günü gider kayıtları ----------------------------------------

K12 = Rule("K-12", "1.0", "Tatil günü gider kayıtları",
           "Pazar günlerine ve resmî/dinî bayram günlerine tarihlenmiş gider kayıtları (7 ile başlayan hesaplara borç) "
           "listelenir. Ay sonu günleri ile finansman giderleri (banka masrafı, faiz) sayılmaz.",
           "I", ("THP_VUK",), (), "İstatistiksel gösterge; mevzuat hükmü değildir. İşle ilgisi olmayan giderler "
           "gider yazılamaz (GVK 40, 41; KVK 8, 11).", False,
           "Hafta sonu ve tatilde çalışan işletmelerde (perakende, lokanta, turizm, nakliye) olağandır; fatura tarihi tatil "
           "gününe denk gelen abonelik ve otomatik faturalar da listeye girer.", D0, "Düşük")


def tatil_adi(d: date) -> str | None:
    if (d.month, d.day) in SABIT_TATILLER:
        return "resmî tatil"
    for bas, gun in DINI_BAYRAMLAR.get(d.year, []):
        b = date.fromisoformat(bas)
        if b <= d < b + timedelta(days=gun):
            return "bayram"
    if d.weekday() == 6:
        return "pazar"
    return None


def _ay_sonu(d: date) -> bool:
    return (d + timedelta(days=1)).month != d.month


def k12(ctx: Context) -> Result:
    lines, kaynak = _kayitlar(ctx)
    if lines is None:
        return Result(K12, "calistirilamadi", "Çalıştırılamadı: yevmiye, defteri kebir veya muavin yüklenmemiş.")
    rows = []
    for j in lines:
        k = j.hesap_kodu
        if not k.startswith("7") or k.startswith(("78", "79")) or k[:3] in ("711", "721", "731", "741", "751", "761", "771"):
            continue
        if j.borc <= j.alacak or _ay_sonu(j.tarih):
            continue
        ad = tatil_adi(j.tarih)
        if ad:
            rows.append((j, j.borc - j.alacak, ad))
    if not rows:
        return Result(K12, "gecti", f"Pazar ve tatil günlerine tarihlenmiş gider kaydı yok ({kaynak}).")
    rows.sort(key=lambda r: -r[1])
    toplam = sum(t for _, t, _ in rows)
    liste = "; ".join(f"{j.tarih:%d.%m.%Y} ({ad}) {j.hesap_kodu} {kurus_str(t)} TL"
                      + (f" ({j.aciklama.strip()[:40]})" if j.aciklama and j.aciklama.strip() else "") for j, t, ad in rows[:15])
    d = Draft(f"Pazar ve tatil günlerinde {len(rows)} gider kaydı", K12.default_severity, None, toplam, "tatil-gider",
              f"Pazar ve tatil günlerine tarihlenmiş gider kayıtları ({kaynak}), büyükten küçüğe: {liste}"
              + (" …" if len(rows) > 15 else "") + f". Toplam {kurus_str(toplam)} TL.",
              "Yalnızca bir göstergedir. " + K12.exceptions,
              "Büyük tutarlı olanların belgesine bakın; yemek, akaryakıt, konaklama gibi harcamaların işle ilgisini "
              "mükellefe sorun. İşle ilgili değilse kanunen kabul edilmeyen gider olarak ayırın.",
              [Evidence(j.raw_row_id, f"{j.tarih:%d.%m.%Y} {j.hesap_kodu}, {kurus_str(t)} TL") for j, t, _ in rows[:20]])
    return Result(K12, "bulgu", f"Pazar ve tatil günlerinde {len(rows)} gider kaydı var, toplam {kurus_str(toplam)} TL.", [d])


# ---- K-13 Mükerrer fatura kaydı ---------------------------------------------

K13 = Rule("K-13", "1.0", "Mükerrer fatura kaydı",
           "Aynı fatura numarası birden fazla yevmiye maddesinde fatura kaydı olarak (KDV, gelir, gider veya stok hesabıyla) "
           "geçiyorsa listelenir. Fatura numarası yevmiye açıklamasından okunur.",
           "K", ("THP_VUK",), ("yevmiye",), "Aynı belgenin iki kez kaydı indirilecek KDV'yi ve gideri (ya da hasılatı) "
           "mükerrer gösterir (KDVK 29, VUK 227).", False,
           "İade, düzeltme ve ters kayıt aynı numarayı taşıyabilir; tek fatura birden fazla maddeye bölünerek de "
           "kaydedilmiş olabilir. Açıklamasında fatura numarası olmayan kayıtlar bu kontrole girmez.", D0, "Yüksek")

FATURA_HESAP = ("191", "391", "15", "6", "7")


def k13(ctx: Context) -> Result:
    if not ctx.yevmiye:
        return Result(K13, "calistirilamadi", "Çalıştırılamadı: yevmiye defteri yüklenmemiş.")
    by_no = defaultdict(list)
    numarali = 0
    for f in fisler(ctx.yevmiye):
        if not f.tokens or not any(j.hesap_kodu.startswith(FATURA_HESAP) for j in f.lines):
            continue
        numarali += 1
        for no in f.tokens:
            by_no[no].append(f)
    drafts = []
    for no in sorted(by_no):
        fs = sorted(by_no[no], key=lambda f: (f.tarih, f.no))
        if len(fs) < 2:
            continue
        # Aynı yönde (ikisi de borç ya da ikisi de alacak KDV/gelir/gider) kaydedilenler mükerrer adayıdır; ters kayıt değildir.
        def tutar(f):
            return sum(j.borc - j.alacak for j in f.lines if j.hesap_kodu.startswith(FATURA_HESAP))
        ayni = [f for f in fs if (tutar(f) > 0) == (tutar(fs[0]) > 0) and tutar(f) != 0]
        if len(ayni) < 2:
            continue
        esit = len({abs(tutar(f)) for f in ayni}) == 1
        liste = "; ".join(f"{f.tarih:%d.%m.%Y} madde {f.no} ({kurus_str(abs(tutar(f)))} TL)" for f in ayni[:6])
        fazla = sum(abs(tutar(f)) for f in ayni[1:])
        drafts.append(Draft(f"{no} numaralı fatura {len(ayni)} maddede kayıtlı", "Yüksek" if esit else "Orta", None, fazla,
                            f"mukerrer-{no}",
                            f"{no} numaralı fatura şu yevmiye maddelerinde geçiyor: {liste}. "
                            + ("Tutarlar aynı; kayıt mükerrer olabilir." if esit else
                               "Tutarlar farklı; fatura parçalı kaydedilmiş ya da biri düzeltme kaydı olabilir."),
                            "Numaranın birden fazla maddede geçtiği kesin. Mükerrer olup olmadığı belirsiz: " + K13.exceptions,
                            "Maddeleri Luca'da açıp aynı faturanın iki kez işlenip işlenmediğine bakın; mükerrerse birini iptal edin.",
                            [Evidence(j.raw_row_id, f"{f.tarih:%d.%m.%Y} madde {f.no}, {j.hesap_kodu}")
                             for f in ayni[:4] for j in f.lines[:4]]))
    if drafts:
        drafts.sort(key=lambda d: -(d.amount or 0))
        return Result(K13, "bulgu", f"{len(drafts)} fatura numarası birden fazla maddede kayıtlı.", drafts[:50])
    if not numarali:
        return Result(K13, "uygulanmadi", "Uygulanmadı: yevmiye açıklamalarında fatura numarası bulunamadı.")
    return Result(K13, "gecti", f"Numarası okunan {numarali} fatura kaydında mükerrer yok.")


RISK_RULES = [(K10, k10), (K11, k11), (K12, k12), (K13, k13)]
