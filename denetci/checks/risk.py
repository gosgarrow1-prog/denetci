"""Hata ve risk taraması: ters bakiye, kasadan yüksek tutarlı nakit hareketi, tatil günü gideri, mükerrer fatura.

Bu kontroller kesin hata göstermez; bakılması gereken kayıtları öne çıkarır.
"""

from collections import defaultdict
from datetime import date, timedelta

from ..efatura import fisler
from ..parametreler import BINEK_OTO, DINI_BAYRAMLAR, ORTULU_SERMAYE, SABIT_TATILLER, TEVSIK
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


# ---- Ortak yardımcı: yılbaşından satırlar -----------------------------------

def _defter(ctx: Context):
    """(açılışlar {ana hesap: net}, dönem sonuna kadar satırlar, kaynak adı). Bakiye izlemek için; yoksa (None, None, None)."""
    if ctx.ytd:
        return {}, [j for j in ctx.ytd if j.tarih <= ctx.period_end], "yılbaşından yevmiye"
    if ctx.kebir:
        op = defaultdict(int)
        for o in ctx.openings.get("kebir", []):
            op[o.hesap_kodu[:3]] += o.borc - o.alacak
        return op, [j for j in ctx.kebir if j.tarih <= ctx.period_end], "defteri kebir"
    return None, None, None


def _gunluk(ctx: Context, hesaplar: tuple):
    """Dönemin her günü için verilen ana hesapların toplam net bakiyesi (borç +). [(gün, bakiye)] ya da None."""
    op, lines, kaynak = _defter(ctx)
    if lines is None:
        return None, None
    bakiye = sum(v for k, v in op.items() if k in hesaplar)
    gun = defaultdict(int)
    for j in lines:
        if j.hesap_kodu[:3] in hesaplar:
            if j.tarih < ctx.period_start:
                bakiye += j.borc - j.alacak
            else:
                gun[j.tarih] += j.borc - j.alacak
    out, d = [], ctx.period_start
    while d <= ctx.period_end:
        bakiye += gun.get(d, 0)
        out.append((d, bakiye))
        d += timedelta(days=1)
    return out, kaynak


# ---- K-14 Ortaklardan alacaklar ve adat -------------------------------------

K14 = Rule("K-14", "1.0", "Ortaklardan alacaklar ve adat (faiz) hesabı",
           "131 ve 231 Ortaklardan Alacaklar hesabı dönem içinde borç bakiyesi verdiyse şirket ortağa para kullandırmış "
           "sayılır; bu tutar için faiz (adat) hesaplanıp fatura edilmesi beklenir. Günlük bakiyelerden adat tutarı hesaplanır.",
           "K", ("THP_VUK",), (), "KVK 13/1-2: ortaklar ilişkili kişidir ve ödünç para verilmesi mal veya hizmet satımı sayılır; "
           "emsallere uygun faiz alınmazsa kazanç örtülü olarak dağıtılmış sayılır. Faiz oranı emsale göre belirlenir. "
           "Vergino'dan 11.11.2026'da kontrol edildi.", True,
           "Faiz dönem sonunda toplu hesaplanıyor olabilir; bakiye ortağın sermaye taahhüdünden ya da kısa süreli bir "
           "avanstan kaynaklanıyor olabilir.", D0, "Orta")

ORTAK_ALACAK = ("131", "231")


def k14(ctx: Context) -> Result:
    gunler, kaynak = _gunluk(ctx, ORTAK_ALACAK)
    if gunler is None:
        return Result(K14, "uygulanmadi", "Uygulanmadı: günlük bakiye için defteri kebir veya yılbaşından yevmiye gerekli.")
    adat = sum(b for _, b in gunler if b > ctx.tolerance)
    if not adat:
        return Result(K14, "gecti", f"131/231 Ortaklardan Alacaklar dönem içinde borç bakiyesi vermedi ({kaynak}).")
    n = len(gunler)
    ort, tepe = adat // n, max(gunler, key=lambda g: g[1])
    lines, _ = _kayitlar(ctx)
    faiz = [j for j in (lines or []) if j.hesap_kodu[:3] in ORTAK_ALACAK and j.borc
            and any(w in (j.aciklama or "").lower() for w in ("faiz", "adat"))]
    ornek = adat * 10 // 100 // 365
    if faiz:
        return Result(K14, "gecti", f"131/231 ortalama bakiyesi {kurus_str(ort)} TL; dönemde ortağa {len(faiz)} faiz/adat kaydı var.")
    op, hepsi, _ = _defter(ctx)
    ev = [Evidence(j.raw_row_id, f"{j.tarih:%d.%m.%Y} {j.hesap_kodu} borç {kurus_str(j.borc)}")
          for j in sorted((j for j in hepsi if j.hesap_kodu[:3] in ORTAK_ALACAK and j.borc), key=lambda j: -j.borc)[:10]]
    d = Draft("Ortaklardan alacak için faiz (adat) kaydı yok", "Orta", "131", ort, "ortak-adat",
              f"131/231 Ortaklardan Alacaklar dönemin {sum(1 for _, b in gunler if b > ctx.tolerance)} gününde borç bakiyesi verdi "
              f"({kaynak}). Ortalama bakiye {kurus_str(ort)} TL, en yüksek bakiye {tepe[0]:%d.%m.%Y} tarihinde {kurus_str(tepe[1])} TL. "
              f"Dönemde ortağa yansıtılmış faiz ya da adat kaydı bulunamadı. Fikir vermesi için: yıllık her %10 faiz oranı "
              f"bu dönem için yaklaşık {kurus_str(ornek)} TL faiz eder (KDV hariç).",
              "Bakiyeler kesin. Faiz kaydının olmadığı, açıklamasında 'faiz' ya da 'adat' geçen kayıt aranarak anlaşıldı; "
              "farklı açıklamayla yapılmış olabilir. " + K14.exceptions,
              "Ortak cari hesabı için adat hesaplayın, emsal faiz oranıyla faiz faturası düzenleyin (KDV'li) ve 131 hesabına "
              "borç, 642 hesabına alacak kaydedin.", ev)
    return Result(K14, "bulgu", f"131/231 ortalama {kurus_str(ort)} TL borç bakiyesi verdi; faiz (adat) kaydı yok.", [d])


# ---- K-15 Örtülü sermaye ----------------------------------------------------

K15 = Rule("K-15", "1.0", "Ortaklara borçlar ve örtülü sermaye",
           "331 ve 431 Ortaklara Borçlar hesabının dönem içindeki en yüksek bakiyesi, dönem başı öz sermayenin üç katıyla "
           "karşılaştırılır. Aşan kısım örtülü sermaye sayılabilir; buna isabet eden faiz ve kur farkı gider yazılamaz.",
           "K", ("THP_VUK",), (), ORTULU_SERMAYE["dayanak"], ORTULU_SERMAYE["dogrulandi"],
           "Öz sermaye, yılbaşı açılış kaydındaki 5 ile başlayan hesaplardan alınır; kanundaki öz sermaye tanımıyla küçük "
           "farklar olabilir. İlişkili kişilerden alınan ve 331/431 dışında izlenen borçlar hesaba girmez. Borca faiz "
           "işletilmiyorsa gider reddi doğmaz.", D0, "Orta")

ORTAK_BORC = ("331", "431")


def _donem_basi_ozkaynak(ctx: Context):
    """Yılbaşı öz sermayesi (alacak +). Yılbaşından yevmiyede ilk maddeden (açılış), 1 Ocak'tan başlayan kebirde nakli yekünden."""
    if ctx.ytd:
        ilk = min(ctx.ytd, key=lambda j: (j.tarih, j.madde_no or "", j.fis_no))
        acilis = [j for j in ctx.ytd if (j.fis_no, j.tarih) == (ilk.fis_no, ilk.tarih)]
        if any(j.hesap_kodu.startswith("5") for j in acilis):
            return sum(j.alacak - j.borc for j in acilis if j.hesap_kodu.startswith("5"))
    if ctx.kebir and (ctx.period_start.month, ctx.period_start.day) == (1, 1):
        ops = [o for o in ctx.openings.get("kebir", []) if o.hesap_kodu.startswith("5")]
        if ops:
            return sum(o.alacak - o.borc for o in ops)
    return None


def k15(ctx: Context) -> Result:
    gunler, kaynak = _gunluk(ctx, ORTAK_BORC)
    if gunler is None:
        return Result(K15, "uygulanmadi", "Uygulanmadı: günlük bakiye için defteri kebir veya yılbaşından yevmiye gerekli.")
    tarih, enyuksek = max(((d, -b) for d, b in gunler), key=lambda g: g[1])
    if enyuksek <= ctx.tolerance:
        return Result(K15, "gecti", f"331/431 Ortaklara Borçlar dönem içinde alacak bakiyesi vermedi ({kaynak}).")
    oz = _donem_basi_ozkaynak(ctx)
    if oz is None:
        return Result(K15, "uygulanmadi", f"Uygulanmadı: ortaklara borç en yüksek {kurus_str(enyuksek)} TL; karşılaştırma için "
                                              "yılbaşı öz sermayesi gerekli (açılış fişini içeren yılbaşından yevmiye).")
    sinir = max(oz, 0) * ORTULU_SERMAYE["kat"]
    if enyuksek <= sinir:
        return Result(K15, "gecti", f"Ortaklara borç en yüksek {kurus_str(enyuksek)} TL; sınır {kurus_str(sinir)} TL "
                                    f"(dönem başı öz sermaye {kurus_str(oz)} TL × {ORTULU_SERMAYE['kat']}).")
    asan = enyuksek - sinir
    _, hepsi, _ = _defter(ctx)
    ev = [Evidence(j.raw_row_id, f"{j.tarih:%d.%m.%Y} {j.hesap_kodu} alacak {kurus_str(j.alacak)}")
          for j in sorted((j for j in hepsi if j.hesap_kodu[:3] in ORTAK_BORC and j.alacak), key=lambda j: -j.alacak)[:10]]
    d = Draft("Ortaklara borç öz sermayenin üç katını aşıyor", ctx.rule_severity(asan), "331", asan, "ortulu-sermaye",
              f"331/431 Ortaklara Borçlar {tarih:%d.%m.%Y} tarihinde {kurus_str(enyuksek)} TL'ye ulaştı ({kaynak}). Dönem başı öz sermaye "
              f"{kurus_str(oz)} TL" + (" (eksi; sınır sıfır alındı)" if oz < 0 else "") + f", üç katı {kurus_str(sinir)} TL. "
              f"Aşan kısım {kurus_str(asan)} TL örtülü sermaye sayılabilir.",
              "Bakiyeler kesin. Vergisel sonuç belirsiz: " + K15.exceptions,
              "Bu borca faiz ya da kur farkı işletilip işletilmediğine bakın; işletildiyse aşan kısma isabet eden tutarı "
              "kanunen kabul edilmeyen gider olarak ayırın. Sermaye artırımı ya da borcun azaltılması değerlendirilebilir.", ev)
    return Result(K15, "bulgu", f"Ortaklara borç sınırı {kurus_str(asan)} TL aşıyor.", [d])


# ---- K-16 Binek otomobil giderleri ------------------------------------------

K16 = Rule("K-16", "1.0", "Binek otomobil gider kısıtlaması",
           "Adında binek, otomobil, taşıt, araç veya akaryakıt geçen gider hesaplarının dönem tutarı bulunur; bu giderlerin "
           "%30'unun kanunen kabul edilmeyen gider (KKEG) olarak ayrılıp ayrılmadığına bakılır.",
           "D", ("THP_VUK",), (), BINEK_OTO["dayanak"], BINEK_OTO["dogrulandi"],
           "Hesap adından anlaşılır; kamyonet, kamyon gibi ticari araçların ve taşımacılık/kiralama işinde kullanılan "
           "binek araçların giderlerinde kısıtlama yoktur. KKEG nazım hesaplarda ya da beyannamede izleniyor olabilir.",
           D0, "Orta")

BINEK_KELIME = ("binek", "otomobil", "taşıt", "tasit", "araç", "arac", "akaryakıt", "akaryakit")
KKEG_KELIME = ("kkeg", "k.k.e.g", "kabul edilmeyen")


def _adlar(ctx: Context) -> dict:
    ad = {}
    for src in (ctx.ytd, ctx.yevmiye, ctx.kebir, ctx.muavin, ctx.mizan):
        for x in src or []:
            if x.hesap_adi:
                ad[x.hesap_kodu] = x.hesap_adi
    return ad


def _kucuk(s: str) -> str:
    return (s or "").replace("İ", "i").replace("I", "ı").lower()


def k16(ctx: Context) -> Result:
    lines, kaynak = _kayitlar(ctx)
    if lines is None:
        return Result(K16, "calistirilamadi", "Çalıştırılamadı: yevmiye, defteri kebir veya muavin yüklenmemiş.")
    ad = _adlar(ctx)

    def adi(kod):  # alt hesap adı ve üst hesap adları birlikte
        parca = kod.split(".")
        return " ".join(_kucuk(ad.get(".".join(parca[:i]), "")) for i in range(1, len(parca) + 1))

    gider, kkeg = defaultdict(int), 0
    for j in lines:
        k = j.hesap_kodu
        isim = adi(k)
        binek = any(w in isim for w in BINEK_KELIME)
        if any(w in isim for w in KKEG_KELIME) or k.startswith(("689", "950", "951", "900", "901")):
            if binek or any(w in _kucuk(j.aciklama) for w in BINEK_KELIME):
                kkeg += abs(j.borc - j.alacak) if k.startswith(("9",)) else j.borc - j.alacak
        elif k.startswith("7") and not k.startswith("79") and binek:
            gider[k] += j.borc - j.alacak
    toplam = sum(v for v in gider.values() if v > 0)
    if toplam <= ctx.tolerance:
        return Result(K16, "gecti", f"Adında binek, taşıt, araç ya da akaryakıt geçen gider hesabında hareket yok ({kaynak}).")
    oran = BINEK_OTO["kkeg_orani"]
    # Gider hesabında tutarın tamamı duruyorsa beklenen KKEG %30'dur; KKEG ayrı hesaba alınmışsa gider hesabında %70 kalır.
    beklenen = round((toplam + kkeg) * oran)
    if kkeg >= beklenen - max(ctx.tolerance, beklenen // 100):
        return Result(K16, "gecti", f"Taşıt giderleri {kurus_str(toplam)} TL; KKEG olarak ayrılan {kurus_str(kkeg)} TL (%{oran * 100:.0f} karşılanıyor).")
    eksik = beklenen - kkeg
    liste = "; ".join(f"{k} {ad.get(k, '')}".strip() + f": {kurus_str(v)} TL" for k, v in sorted(gider.items(), key=lambda x: -x[1])[:10] if v > 0)
    rows = sorted((j for j in lines if j.hesap_kodu in gider and j.borc), key=lambda j: -j.borc)[:10]
    d = Draft("Taşıt giderlerinde KKEG ayrımı eksik görünüyor", "Orta", None, eksik, "binek-kkeg",
              f"Adından taşıt gideri olduğu anlaşılan hesaplar ({kaynak}): {liste}. Toplam {kurus_str(toplam)} TL. "
              f"Araçlar binek otomobilse giderin %{oran * 100:.0f}'u olan {kurus_str(beklenen)} TL KKEG olmalı; "
              f"defterde taşıtla ilgili KKEG kaydı {kurus_str(kkeg)} TL. Eksik görünen {kurus_str(eksik)} TL.",
              "Gider tutarları kesin. Aracın binek olup olmadığı ve KKEG'in nerede izlendiği veriden anlaşılamaz: " + K16.exceptions,
              "Araçların ruhsatına bakın (binek mi, ticari mi). Binekse giderin %30'unu KKEG olarak ayırın; kiralama ve "
              "amortisman için yıllık tutar sınırlarını da kontrol edin. Geçici vergi sayfasındaki KKEG tutarına ekleyin.",
              [Evidence(j.raw_row_id, f"{j.tarih:%d.%m.%Y} {j.hesap_kodu} {kurus_str(j.borc)} TL") for j in rows])
    return Result(K16, "bulgu", f"Taşıt giderleri {kurus_str(toplam)} TL; KKEG ayrımı {kurus_str(eksik)} TL eksik görünüyor.", [d])


# ---- K-17 Birikmiş amortisman ile duran varlık uyumu ------------------------

K17 = Rule("K-17", "1.0", "Birikmiş amortisman ile duran varlık uyumu",
           "257 Birikmiş Amortismanlar, maddi duran varlıkların (252–256) toplamını; 268 Birikmiş Amortismanlar, maddi olmayan "
           "duran varlıkların (260–267) toplamını aşamaz. Varlık yokken birikmiş amortisman kalması da işaretlenir.",
           "A", ("THP_VUK",), (), "VUK 313 ve devamı: amortisman, varlığın değeri üzerinden ayrılır; birikmiş amortisman "
           "varlığın kayıtlı değerini aşamaz.", False,
           "Satılan ya da hurdaya ayrılan varlığın birikmiş amortismanı henüz kapatılmamış olabilir; yeniden değerleme "
           "farkları ayrı alt hesaplarda izleniyor olabilir.", D0, "Orta")

AMORTISMAN_CIFT = (("257", ("252", "253", "254", "255", "256"), "maddi duran varlıklar (252–256)"),
                   ("268", ("260", "261", "262", "263", "264", "267"), "maddi olmayan duran varlıklar (260–267)"))


def k17(ctx: Context) -> Result:
    from ..finance import ana_balances
    net, kaynak = ana_balances(ctx)
    if net is None:
        return Result(K17, "uygulanmadi", "Uygulanmadı: dönem sonu bakiyeleri için yılbaşından mizan, defteri kebir veya "
                                          "yılbaşından yevmiye gerekli.")
    drafts, bakilan = [], 0
    for amort, varliklar, ad in AMORTISMAN_CIFT:
        birikmis = -net.get(amort, 0)
        maliyet = sum(net.get(v, 0) for v in varliklar)
        if birikmis <= ctx.tolerance:
            continue
        bakilan += 1
        if birikmis > maliyet + ctx.tolerance:
            asan = birikmis - max(maliyet, 0)
            drafts.append(Draft(f"{amort} birikmiş amortisman varlık tutarını aşıyor", ctx.rule_severity(asan), amort, asan,
                                f"amortisman-{amort}",
                                f"{amort} Birikmiş Amortismanlar {kurus_str(birikmis)} TL; {ad} toplamı {kurus_str(maliyet)} TL "
                                f"({kaynak}). Birikmiş amortisman varlık tutarını {kurus_str(asan)} TL aşıyor.",
                                "Bakiyeler aritmetik olarak kesin. Neden belirsiz: " + K17.exceptions,
                                "Satılan veya kayıttan çıkan varlıkların birikmiş amortismanının kapatılıp kapatılmadığına ve "
                                "amortisman tablosunun defterle uyumuna bakın."))
    if drafts:
        return Result(K17, "bulgu", f"{len(drafts)} hesapta birikmiş amortisman varlık tutarını aşıyor.", drafts)
    if not bakilan:
        return Result(K17, "gecti", f"Birikmiş amortisman bakiyesi yok ({kaynak}).")
    return Result(K17, "gecti", f"Birikmiş amortismanlar varlık tutarlarını aşmıyor ({kaynak}).")


# ---- K-18 Gider hesaplarının yansıtılması (7/A) -----------------------------

K18 = Rule("K-18", "1.0", "Gider hesaplarının yansıtılması",
           "7/A seçeneğinde gider hesapları (710, 720, 730, 740, 750, 760, 770, 780) dönem sonunda yansıtma hesaplarıyla "
           "(711, 721, 731, 741, 751, 761, 771, 781) gelir tablosuna aktarılır. Yansıtılan tutar gider tutarından farklıysa "
           "gelir tablosu eksik ya da fazla gider gösterir.",
           "A", ("THP_VUK",), (), "Tekdüzen Hesap Planı 7/A seçeneği, maliyet hesaplarının işleyişi (1 Sıra No.lu MSUGT).", False,
           "Yansıtma yalnızca yıl sonunda yapılıyorsa ara dönemlerde fark olağandır; bu durumda ara dönemde uyarı verilmez. "
           "Yansıtmadan sonra girilen gider kayıtları fark yaratır.", D0, "Orta")

YANSITMA = (("710", "711"), ("720", "721"), ("730", "731"), ("740", "741"), ("750", "751"), ("760", "761"),
            ("770", "771"), ("780", "781"))


def k18(ctx: Context) -> Result:
    from ..finance import ana_balances
    net, kaynak = ana_balances(ctx)
    if net is None:
        return Result(K18, "uygulanmadi", "Uygulanmadı: yılbaşından bakiyeler için mizan, defteri kebir veya yılbaşından "
                                          "yevmiye gerekli.")
    gider = {g: net.get(g, 0) for g, _ in YANSITMA}
    yans = {y: -net.get(y, 0) for _, y in YANSITMA}
    if not any(abs(v) > ctx.tolerance for v in gider.values()):
        return Result(K18, "gecti", f"7/A gider hesaplarında bakiye yok ({kaynak}).")
    yil_sonu = (ctx.period_end.month, ctx.period_end.day) == (12, 31)
    if not any(abs(v) > ctx.tolerance for v in yans.values()) and not yil_sonu:
        return Result(K18, "bilgi", f"Yansıtma kaydı henüz yok; gider hesaplarında {kurus_str(sum(gider.values()))} TL duruyor "
                                    f"({kaynak}). Yansıtma yıl sonunda yapılıyorsa olağandır.")
    drafts = []
    for g, y in YANSITMA:
        fark = gider[g] - yans[y]
        if abs(fark) <= ctx.tolerance:
            continue
        drafts.append(Draft(f"{g} gider hesabı tam yansıtılmamış", ctx.rule_severity(fark), g, fark, f"yansitma-{g}",
                            f"{g} hesabında yılbaşından {kurus_str(gider[g])} TL gider var; {y} yansıtma hesabıyla aktarılan "
                            f"{kurus_str(yans[y])} TL ({kaynak}). Fark {kurus_str(fark)} TL gelir tablosuna "
                            + ("aktarılmamış." if fark > 0 else "fazla aktarılmış."),
                            "Bakiyeler aritmetik olarak kesin. " + K18.exceptions,
                            f"Yansıtma kaydını güncelleyin: {g} hesabının bakiyesi kadar {y} hesabı alacaklandırılmalı."))
    if drafts:
        return Result(K18, "bulgu", f"{len(drafts)} gider hesabında yansıtma tutarı gider tutarından farklı.", drafts)
    return Result(K18, "gecti", f"Gider hesapları tam yansıtılmış ({kaynak}).")


# ---- K-19 Dönemsellik (180/280, 380/480) ------------------------------------

K19 = Rule("K-19", "1.0", "Gelecek dönemlere ait gider ve gelirlerin dönemselliği",
           "180 Gelecek Aylara Ait Giderler bakiyesi varken dönem içinde gidere aktarım (180 alacak hareketi) yoksa, "
           "dönemin payına düşen gider kaydedilmemiş olabilir; 380 Gelecek Aylara Ait Gelirler için de aynı şekilde "
           "gelire aktarım aranır. Yıl sonunda 280 ve 480'de kalan tutarların gelecek yıla düşen kısmı 180 ve 380'e aktarılmalıdır.",
           "K", ("THP_VUK",), (), "Dönemsellik ilkesi (Muhasebenin Temel Kavramları, 1 Sıra No.lu MSUGT); 180, 280, 380, "
           "480 hesaplarının işleyişi.", False,
           "Peşin ödenen tutar dönem sonunda ilk kez kaydedilmişse aktarım henüz gerekmez; aktarım yalnızca yıl sonunda "
           "yapılıyorsa ara dönemde olağandır.", D0, "Orta")

DONEMSELLIK = (("180", "alacak", "gidere", "Gelecek Aylara Ait Giderler", "kira, sigorta gibi peşin ödenen giderlerin bu döneme düşen payı"),
               ("380", "borc", "gelire", "Gelecek Aylara Ait Gelirler", "peşin tahsil edilen gelirlerin bu döneme düşen payı"))


def k19(ctx: Context) -> Result:
    from ..finance import ana_balances
    from .kdv import movements
    net, kaynak = ana_balances(ctx)
    m = movements(ctx)
    if net is None or m is None:
        return Result(K19, "uygulanmadi", "Uygulanmadı: dönem sonu bakiyeleri ve dönem hareketleri gerekli "
                                          "(yılbaşından mizan, defteri kebir veya yevmiye).")
    drafts, bakilan = [], 0
    for hesap, yon, nereye, ad, aciklama in DONEMSELLIK:
        bakiye = net.get(hesap, 0) * (1 if hesap == "180" else -1)
        if bakiye <= ctx.tolerance:
            continue
        bakilan += 1
        aktarim = m.total((hesap,), yon)
        if aktarim <= ctx.tolerance:
            drafts.append(Draft(f"{hesap} {ad} dönem içinde {nereye} aktarılmamış", "Orta", hesap, bakiye, f"donemsellik-{hesap}",
                                f"{hesap} {ad} hesabında {kurus_str(bakiye)} TL bakiye var ({kaynak}); "
                                f"{m.window[0]:%d.%m.%Y} – {m.window[1]:%d.%m.%Y} arasında bu hesaptan {nereye} aktarım kaydı yok.",
                                "Bakiye ve hareketler kesin. Hata olup olmadığı belirsiz: " + K19.exceptions,
                                f"Sözleşme sürelerine göre {aciklama} hesaplanıp {hesap} hesabından "
                                f"{'ilgili gider' if hesap == '180' else 'ilgili gelir'} hesabına aktarılmalı."))
    yil_sonu = (ctx.period_end.month, ctx.period_end.day) == (12, 31)
    if yil_sonu:
        for uzun, kisa, ad in (("280", "180", "Gelecek Yıllara Ait Giderler"), ("480", "380", "Gelecek Yıllara Ait Gelirler")):
            bakiye = abs(net.get(uzun, 0))
            if bakiye > ctx.tolerance and m.total((uzun,), "alacak" if uzun == "280" else "borc") <= ctx.tolerance:
                bakilan += 1
                drafts.append(Draft(f"{uzun} {ad}: gelecek yıla düşen kısım {kisa}'e aktarılmamış olabilir", "Orta", uzun, bakiye,
                                    f"donemsellik-{uzun}",
                                    f"{uzun} hesabında yıl sonunda {kurus_str(bakiye)} TL bakiye var ve yıl içinde {kisa} hesabına aktarım yok.",
                                    "Bakiye kesin. " + K19.exceptions,
                                    f"{uzun} bakiyesinin önümüzdeki 12 aya düşen kısmını {kisa} hesabına aktarın."))
    if drafts:
        return Result(K19, "bulgu", f"{len(drafts)} hesapta dönemsellik aktarımı eksik görünüyor.", drafts)
    if not bakilan:
        return Result(K19, "gecti", f"180, 380 (ve yıl sonunda 280, 480) hesaplarında bakiye yok ({kaynak}).")
    return Result(K19, "gecti", f"Gelecek dönem hesaplarından dönem içinde aktarım yapılmış ({kaynak}).")


# ---- K-20 Şüpheli ticari alacaklar ve karşılık ------------------------------

K20 = Rule("K-20", "1.0", "Şüpheli alacaklar ve karşılığı",
           "129 Şüpheli Ticari Alacaklar Karşılığı, 128 Şüpheli Ticari Alacaklar tutarını aşamaz. 128'de bakiye varken "
           "karşılık ayrılmamışsa bilgi verilir: dava veya icra aşamasındaki alacaklar için karşılık ayrılarak gider yazılabilir.",
           "K", ("THP_VUK",), (), "VUK 323 (şüpheli alacaklar); 128 ve 129 hesaplarının işleyişi.", False,
           "Karşılık ayırmak isteğe bağlıdır; alacak teminatlıysa teminat dışındaki kısım için karşılık ayrılır.", D0, "Orta")


def k20(ctx: Context) -> Result:
    from ..finance import ana_balances
    net, kaynak = ana_balances(ctx)
    if net is None:
        return Result(K20, "uygulanmadi", "Uygulanmadı: dönem sonu bakiyeleri gerekli.")
    s128, k129 = net.get("128", 0), -net.get("129", 0)
    if s128 <= ctx.tolerance and k129 <= ctx.tolerance:
        return Result(K20, "gecti", f"Şüpheli alacak (128) ve karşılık (129) bakiyesi yok ({kaynak}).")
    if k129 > s128 + ctx.tolerance:
        fark = k129 - max(s128, 0)
        return Result(K20, "bulgu", "Karşılık şüpheli alacaktan fazla.", [Draft(
            "Şüpheli alacak karşılığı (129) şüpheli alacaktan (128) fazla", ctx.rule_severity(fark), "129", fark, "supheli-fazla",
            f"129 karşılık {kurus_str(k129)} TL, 128 şüpheli alacak {kurus_str(s128)} TL ({kaynak}). Fark {kurus_str(fark)} TL.",
            "Bakiyeler kesin. Tahsil edilen veya silinen şüpheli alacağın karşılığı kapatılmamış olabilir.",
            "Tahsil edilen şüpheli alacakların karşılığını 644 Konusu Kalmayan Karşılıklar hesabına, değersiz hale gelenleri "
            "karşılıkla kapatın.")])
    if s128 > ctx.tolerance and k129 < s128 - ctx.tolerance:
        eksik = s128 - k129
        return Result(K20, "bilgi", f"128 Şüpheli Ticari Alacaklar {kurus_str(s128)} TL, ayrılan karşılık {kurus_str(k129)} TL; "
                                    f"{kurus_str(eksik)} TL için karşılık ayrılmamış ({kaynak}). Dava veya icra aşamasındaysa "
                                    "VUK 323'e göre karşılık ayrılıp gider yazılabilir.")
    return Result(K20, "gecti", f"Şüpheli alacakların tamamı için karşılık ayrılmış ({kaynak}).")


# ---- K-31 Hareketsiz avans bakiyeleri ---------------------------------------

K31 = Rule("K-31", "1.0", "Hareketsiz avans bakiyeleri",
           "Verilen ve alınan avanslar (159, 195, 196, 340, 440) faturaya veya harcama belgesine bağlanarak kapatılır. "
           "Bakiyesi olup dönem içinde hiç hareket görmeyen avans hesapları listelenir.",
           "K", ("THP_VUK",), (), "159, 195, 196, 340, 440 hesaplarının işleyişi (1 Sıra No.lu MSUGT); avans tahsilatında "
           "KDV doğmaz, fatura teslimde düzenlenir.", False,
           "Uzun vadeli sipariş ve inşaat işlerinde avans bakiyesinin uzun süre kalması olağandır.", D0, "Orta")

AVANSLAR = {"159": "Verilen Sipariş Avansları", "195": "İş Avansları", "196": "Personel Avansları",
            "340": "Alınan Sipariş Avansları", "440": "Alınan Sipariş Avansları (uzun vadeli)"}


def k31(ctx: Context) -> Result:
    from ..finance import ana_balances
    from .kdv import movements
    net, kaynak = ana_balances(ctx)
    m = movements(ctx)
    if net is None or m is None:
        return Result(K31, "uygulanmadi", "Uygulanmadı: dönem sonu bakiyeleri ve dönem hareketleri gerekli.")
    drafts, bakilan = [], 0
    for hesap, ad in AVANSLAR.items():
        bakiye = abs(net.get(hesap, 0))
        if bakiye <= ctx.tolerance:
            continue
        bakilan += 1
        if m.total((hesap,), "borc") + m.total((hesap,), "alacak") == 0:
            drafts.append(Draft(f"{hesap} {ad} dönem içinde hiç hareket görmemiş", "Orta", hesap, bakiye, f"avans-{hesap}",
                                f"{hesap} {ad} hesabında {kurus_str(bakiye)} TL bakiye var ({kaynak}); "
                                f"{m.window[0]:%d.%m.%Y} – {m.window[1]:%d.%m.%Y} arasında hareket yok.",
                                "Bakiye kesin. " + K31.exceptions,
                                "Avansın karşılığı olan fatura veya harcama belgesinin gelip gelmediğine bakın; gelmişse avansı "
                                "kapatın, gelmemişse cari mutabakatıyla teyit edin. Personel ve iş avansları uzun süre açık "
                                "kalırsa ortak/personel borcu gibi değerlendirilebilir."))
    if drafts:
        return Result(K31, "bulgu", f"{len(drafts)} avans hesabı dönem içinde hareketsiz.", drafts)
    if not bakilan:
        return Result(K31, "gecti", f"Avans hesaplarında bakiye yok ({kaynak}).")
    return Result(K31, "gecti", f"Avans hesapları dönem içinde hareket görmüş ({kaynak}).")


# ---- K-32 Dönem kârı kapanış kayıtları (690–692, 370, 590/591) --------------

K32 = Rule("K-32", "1.0", "Dönem kârı kapanış kayıtlarının tutarlılığı",
           "Kapanış yapıldıysa 692 Dönem Net Kârı = 690 Dönem Kârı − 691 Vergi Karşılığı olmalı; 691 ile 370 Dönem Kârı "
           "Vergi Karşılığı eşit olmalı; 692 bilançoya aktarıldıysa 590 (veya 591) aynı tutarı göstermeli.",
           "A", ("THP_VUK",), (), "690, 691, 692, 370, 590, 591 hesaplarının işleyişi (1 Sıra No.lu MSUGT).", False,
           "Kapanış kayıtları kısmen yapılmışsa (ör. yalnızca 690'a aktarım) sonraki adımlar henüz eksik olabilir.", D0, "Orta")


def k32(ctx: Context) -> Result:
    from ..finance import ana_balances
    net, kaynak = ana_balances(ctx)
    if net is None:
        return Result(K32, "uygulanmadi", "Uygulanmadı: dönem sonu bakiyeleri gerekli.")
    g = lambda h: -net.get(h, 0)  # noqa: E731  alacak yönü +
    v690, v691, v692 = g("690"), -g("691"), g("692")  # 691 borç bakiyeli (vergi gideri)
    if all(abs(x) <= ctx.tolerance for x in (v690, v691, v692)):
        return Result(K32, "uygulanmadi", "Kapanış kaydı yok (690–692 boş); kontrol yıl sonu kapanışından sonra çalışır.")
    drafts = []
    tol = ctx.tolerance

    def ekle(baslik, tutar, kod, aciklama, ne):
        drafts.append(Draft(baslik, ctx.rule_severity(tutar), kod, tutar, f"kapanis-{kod}", aciklama,
                            "Bakiyeler kesin. " + K32.exceptions, ne))
    if abs(v692) > tol and abs(v692 - (v690 - v691)) > tol:
        ekle("692 dönem net kârı, 690 − 691 ile tutmuyor", v692 - (v690 - v691), "692",
             f"690 Dönem Kârı {kurus_str(v690)} TL, 691 Vergi Karşılığı {kurus_str(v691)} TL; 692 Dönem Net Kârı "
             f"{kurus_str(v692)} TL olmalıydı {kurus_str(v690 - v691)} TL ({kaynak}).",
             "690'dan 692'ye aktarım kaydını ve 691 tutarını kontrol edin.")
    v370 = g("370")
    if abs(v691) > tol and abs(v370) > tol and abs(v370 - v691) > tol:
        ekle("370 vergi karşılığı ile 691 tutmuyor", v370 - v691, "370",
             f"691 Dönem Kârı Vergi Karşılığı {kurus_str(v691)} TL, 370 Dönem Kârı Vergi ve Diğer Yasal Yükümlülük "
             f"Karşılıkları {kurus_str(v370)} TL ({kaynak}).",
             "Vergi karşılığı kaydının iki tarafını kontrol edin (691 borç / 370 alacak). 371 ile mahsup sonrası fark olağandır.")
    v59 = g("590") - net.get("591", 0)
    if abs(v692) > tol and abs(v59) > tol and abs(v59 - v692) > tol:
        ekle("590/591 dönem sonucu ile 692 tutmuyor", v59 - v692, "590",
             f"692 Dönem Net Kârı {kurus_str(v692)} TL, bilançodaki 590/591 {kurus_str(v59)} TL ({kaynak}).",
             "692'den 590 (zarar ise 591) hesabına aktarım kaydını kontrol edin.")
    if drafts:
        return Result(K32, "bulgu", f"Kapanış kayıtlarında {len(drafts)} tutarsızlık var.", drafts)
    return Result(K32, "gecti", f"Kapanış kayıtları birbiriyle tutarlı ({kaynak}).")


# ---- K-33 Benford analizi ---------------------------------------------------

K33 = Rule("K-33", "1.0", "Tutarların ilk rakam dağılımı (Benford)",
           "Kayıt tutarlarının ilk rakamları Benford dağılımıyla karşılaştırılır (10 TL altı tutarlar hariç, en az 300 kayıt). "
           "Belirgin sapma, elle girilmiş, uydurulmuş ya da bir sınırın altında tutulmaya çalışılmış tutarlara işaret edebilir.",
           "I", ("THP_VUK",), (), "İstatistiksel analitik inceleme (Benford yasası; Nigrini MAD eşikleri).", True,
           "Sabit fiyatlı satışlar, abonelik, kira ve maaş gibi tekrar eden tutarlar dağılımı doğal olarak bozar; "
           "kayıt sayısı azsa sapma rastlantısal olabilir.", D0, "Orta")


def k33(ctx: Context) -> Result:
    from .. import benford
    lines, ad = _kayitlar(ctx)
    if not lines:
        return Result(K33, "uygulanmadi", "Uygulanmadı: yevmiye, kebir veya muavin gerekli.")
    b = benford.analiz([max(j.borc, j.alacak) for j in lines])
    if b is None:
        return Result(K33, "uygulanmadi", "Uygulanmadı: 10 TL üzerinde kayıt yok.")
    mad, ki = f"{b.mad:.4f}".replace(".", ","), f"{b.ki_kare:.1f}".replace(".", ",")
    ozet = f"{b.n} kayıt ({ad}); ortalama mutlak sapma {mad}, ki-kare {ki}: {benford.UYGUNLUK_ADI[b.uygunluk].lower()}."
    if not b.yeterli:
        return Result(K33, "bilgi", f"Kayıt sayısı az ({b.n}); Benford sonucu yalnızca bilgi amaçlıdır. {ozet}")
    if b.uygunluk != "uygun_degil":
        return Result(K33, "gecti", ozet)
    rakam = ", ".join(str(d) for d in b.fazla) or "—"
    return Result(K33, "bulgu", ozet, [Draft(
        "Kayıt tutarları Benford dağılımına uymuyor", "Orta", None, None, "benford",
        f"{ozet} Beklenenden fazla görülen ilk rakam(lar): {rakam}."
        + (f" En çok tekrar eden tutar {kurus_str(b.tekrar[0][0])} TL ({b.tekrar[0][1]} kez)." if b.tekrar else ""),
        "İstatistiksel bulgu; tek başına hata göstermez. " + K33.exceptions,
        f"İlk rakamı {rakam} olan büyük tutarlı kayıtları ve sık tekrar eden tutarları belgeleriyle karşılaştırın.")])


RISK_RULES = [(K10, k10), (K11, k11), (K12, k12), (K13, k13), (K14, k14), (K15, k15), (K16, k16), (K17, k17), (K18, k18),
              (K19, k19), (K20, k20), (K31, k31), (K32, k32), (K33, k33)]

from .stopaj import STOPAJ_RULES  # noqa: E402

RISK_RULES += STOPAJ_RULES
