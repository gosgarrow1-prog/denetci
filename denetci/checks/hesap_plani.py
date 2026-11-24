"""Tekdüzen Hesap Planı uyumu: e-Defter ve defterlerde kullanılan hesap kodlarının Tekdüzen'e uygunluğu.

e-Defter'de her kaydın ana hesap kodu (accountMainID) Tekdüzen Hesap Planı'ndaki 3 haneli koddur; planda olmayan
veya boş bırakılmış kodlar, hesap grubuna (1–2 haneli) yapılan kayıtlar, ana hesap adının Tekdüzen'deki adından
farklı olması ve hesapların işleyişine aykırı bakiyeler incelemelerde uyumsuzluk olarak ele alınabilir.
Kontroller mizan, yevmiye, defteri kebir, muavin ve e-Defter (XML) kayıtlarının hepsine uygulanır.
"""

import re
from collections import defaultdict

from ..parsing import fold, kurus_str
from ..thp import GELIR_ALACAK, GIDER_BORC, THP, YEDI_A, YEDI_B, serbest
from .rules import D0, Context, Draft, Evidence, Result, Rule

DAYANAK = ("1 Sıra No.lu Muhasebe Sistemi Uygulama Genel Tebliği (Tekdüzen Hesap Planı ve hesapların işleyişi), "
           "e-Defter uygulamasında hesap kodlarının bu plana göre kullanılması.")
CERCEVE = ("THP_VUK",)
BELGE_ADI = {"mizan": "mizan", "yevmiye": "yevmiye", "kebir": "defteri kebir", "muavin": "muavin"}


def _ana(kod: str) -> str:
    """Ana hesap kısmı: ilk ayraçtan önceki rakamlar (ayraç yoksa ilk üç rakam)."""
    bas = re.split(r"[.\- /]", kod.strip(), maxsplit=1)[0]
    return bas[:3] if len(bas) > 3 else bas


def _satirlar(ctx: Context):
    """(belge, kod, ad, tutar, satır no, kayıt satırı mı) — dönemin tüm hesap satırları."""
    for ln in ctx.mizan or []:
        yield "mizan", ln.hesap_kodu, ln.hesap_adi, max(abs(ln.borc or 0), abs(ln.alacak or 0)), ln.raw_row_id, ln.is_leaf
    for doc in ("yevmiye", "kebir", "muavin"):
        for j in getattr(ctx, doc) or []:
            if ctx.period_start <= j.tarih <= ctx.period_end:
                yield doc, j.hesap_kodu, j.hesap_adi, (j.borc or 0) + (j.alacak or 0), j.raw_row_id, True


def _yok_veri(ctx: Context):
    return not (ctx.mizan or ctx.yevmiye or ctx.kebir or ctx.muavin)


CALISMADI = "Çalıştırılamadı: mizan, yevmiye, defteri kebir, muavin veya e-Defter dosyası gerekli."


# ---- K-40 Tekdüzen'de olmayan hesap kodları ---------------------------------

K40 = Rule("K-40", "1.0", "Tekdüzen'de olmayan hesap kodları",
           "Kullanılan her ana hesap kodu Tekdüzen Hesap Planı'nda bulunmalı. Planda olmayan veya boş bırakılmış kodlar "
           "(ör. 104, 125, 330), harf içeren kodlar ve doğrudan hesap grubuna (1–2 haneli kod) yapılan kayıtlar listelenir. "
           "8 (serbest) ve 9 (nazım) ile başlayan hesaplar işletmeye bırakıldığı için sayılmaz.",
           "K", CERCEVE, (), DAYANAK, False,
           "Programda özel açılmış bir ana hesap, e-Defter'e aktarılırken başka bir koda eşleniyor olabilir; e-Defter "
           "dosyasındaki kodla karşılaştırın.", D0, "Yüksek")


def k40(ctx: Context) -> Result:
    if _yok_veri(ctx):
        return Result(K40, "calistirilamadi", CALISMADI)
    sorun = defaultdict(lambda: {"tur": None, "kodlar": set(), "ad": None, "tutar": 0, "satir": 0, "belge": set(), "ev": []})
    for doc, kod, ad, tutar, row, kayit in _satirlar(ctx):
        kod = (kod or "").strip()
        if not kod:
            continue
        ana = _ana(kod)
        if not ana.isdigit():
            tur = "harf"
        elif len(ana) < 3:
            if not kayit:  # mizanın grup başlık satırları olağandır
                continue
            tur = "grup"
        elif ana in THP or serbest(ana):
            continue
        else:
            tur = "yok"
        s = sorun[ana]
        s["tur"], s["ad"] = tur, s["ad"] or ad
        s["kodlar"].add(kod)
        s["tutar"] += tutar
        s["satir"] += 1
        s["belge"].add(BELGE_ADI[doc])
        if len(s["ev"]) < 10:
            s["ev"].append(Evidence(row, f"{kod} ({BELGE_ADI[doc]})"))
    if not sorun:
        return Result(K40, "gecti", "Kullanılan tüm ana hesap kodları Tekdüzen Hesap Planı'nda var.")
    drafts = []
    for ana in sorted(sorun):
        s = sorun[ana]
        kodlar = ", ".join(sorted(s["kodlar"])[:8]) + (" …" if len(s["kodlar"]) > 8 else "")
        baslik = {"yok": f"{ana} kodu Tekdüzen Hesap Planı'nda yok",
                  "grup": f"{ana} hesap grubuna doğrudan kayıt yapılmış",
                  "harf": f"{ana} hesap kodu rakamla başlamıyor"}[s["tur"]]
        neden = {"yok": "Bu ana hesap kodu Tekdüzen'de tanımlı değil (boş bırakılmış ya da programda özel açılmış).",
                 "grup": "1–2 haneli kodlar hesap sınıfı/grubudur; kayıt 3 haneli bir ana hesaba (veya alt hesabına) yapılmalı.",
                 "harf": "Ana hesap kodu 3 haneli rakam olmalı."}[s["tur"]]
        drafts.append(Draft(
            baslik, "Yüksek", ana, s["tutar"], f"thp-kod-{ana}",
            f"{neden} Kullanılan kodlar: {kodlar}" + (f" ({s['ad']})" if s["ad"] else "") +
            f". {s['satir']} satırda, toplam {kurus_str(s['tutar'])} TL hareket; görüldüğü belge: {', '.join(sorted(s['belge']))}.",
            "Kodun Tekdüzen'de olup olmadığı kesin. " + K40.exceptions,
            "Hesabın içeriğine uygun Tekdüzen ana hesabını belirleyin (ör. ortak cari 131/331, personel 135/335, "
            "diğer borçlar 336), hesap planında kodu değiştirin ve kayıtları o hesaba aktarın. e-Defter gönderilmeden "
            "önce düzeltilmesi gerekir.",
            s["ev"]))
    return Result(K40, "bulgu", f"{len(drafts)} ana hesap kodu Tekdüzen Hesap Planı'na uymuyor.", drafts)


# ---- K-41 Ana hesap adı Tekdüzen adından farklı -----------------------------

K41 = Rule("K-41", "1.0", "Ana hesap adı Tekdüzen'deki adından farklı",
           "3 haneli ana hesabın adı Tekdüzen'deki adıyla aynı anlamı taşımalı. Adı tamamen farklı olan ana hesaplar "
           "(ör. 336 kodunun 'Ortaklara Borçlar' diye kullanılması) yanlış hesabın kullanıldığını gösterebilir.",
           "K", CERCEVE, (), DAYANAK, False,
           "Kısaltılmış ya da farklı yazılmış adlar sayılmaz; yalnızca Tekdüzen adıyla hiçbir ortak kelimesi olmayan adlar "
           "listelenir. Programın ana hesaba verdiği özel ad tek başına hata değildir.", D0, "Düşük")

_BOS = {"ve", "ile", "hesabi", "hesap", "hesaplari", "diger", "cesitli", "kisa", "uzun", "vadeli", "tl", "doviz"}


# Pek çok hesabın adında geçen genel kelimeler (ilk dört harfi): tek başına ortaklık sayılmaz.
_GENEL = {"borc", "alac", "gide", "geli", "kar", "kari", "zara", "vade", "senet", "sene"}


def _kokler(ad) -> set:
    return {w[:4] for w in fold(ad).split() if len(w) >= 3 and w not in _BOS and not w.isdigit()} - _GENEL


_BASKA = {k for ad in THP.values() for k in _kokler(ad)}


def k41(ctx: Context) -> Result:
    if _yok_veri(ctx):
        return Result(K41, "calistirilamadi", CALISMADI)
    farkli = {}
    for doc, kod, ad, _, row, _k in _satirlar(ctx):
        kod = (kod or "").strip()
        if kod != _ana(kod) or kod not in THP or not ad or kod in farkli:
            continue
        kok, thp_kok = _kokler(ad), _kokler(THP[kod])
        if not kok or kok & thp_kok:
            continue
        # "Diğer çeşitli borçlar" gibi genel adlı hesaplarda yalnızca başka bir Tekdüzen hesabının adı kullanılmışsa.
        if thp_kok or any(k in _BASKA for k in kok):
            farkli[kod] = (ad, doc, row)
    if not farkli:
        return Result(K41, "gecti", "Ana hesap adları Tekdüzen'deki adlarla uyumlu.")
    drafts = [Draft(f"{kod} hesabı '{ad}' adıyla kullanılmış", "Düşük", kod, None, f"thp-ad-{kod}",
                    f"{kod} Tekdüzen'de '{THP[kod]}' hesabıdır; {BELGE_ADI[doc]} kaydında adı '{ad}'.",
                    "Ad karşılaştırması kelime düzeyindedir; " + K41.exceptions,
                    f"Hesabın içeriği '{THP[kod]}' değilse kayıtları doğru ana hesaba aktarın; içerik doğruysa ana hesabın "
                    "adını Tekdüzen'deki adla düzeltin.",
                    [Evidence(row, f"{kod} {ad}")]) for kod, (ad, doc, row) in sorted(farkli.items())]
    return Result(K41, "bulgu", f"{len(drafts)} ana hesabın adı Tekdüzen'deki adından farklı.", drafts)


# ---- K-42 Alt hesap düzeni ----------------------------------------------------

K42 = Rule("K-42", "1.0", "Ana hesaba doğrudan kayıt (alt hesap düzeni)",
           "Alt hesabı açılmış bir ana hesaba aynı dönemde alt hesap kullanılmadan doğrudan kayıt yapılmışsa listelenir "
           "(ör. hem 120 hem 120.01 kullanılmış). Bu kayıtlar muavin dökümünde ve e-Defter'de cari bazında izlenemez.",
           "K", CERCEVE, (), DAYANAK, False,
           "Alt hesabı hiç olmayan ana hesaplara doğrudan kayıt olağandır ve sayılmaz.", D0, "Orta")


def k42(ctx: Context) -> Result:
    if not (ctx.yevmiye or ctx.kebir or ctx.muavin):
        return Result(K42, "calistirilamadi", "Çalıştırılamadı: yevmiye, defteri kebir, muavin veya e-Defter dosyası gerekli.")
    dogrudan, altli = defaultdict(list), set()
    for doc, kod, _ad, tutar, row, _k in _satirlar(ctx):
        if doc == "mizan":
            continue
        kod = (kod or "").strip()
        ana = _ana(kod)
        if len(ana) != 3:
            continue
        if kod == ana:
            dogrudan[ana].append((tutar, row, doc))
        else:
            altli.add(ana)
    sorunlu = {a: v for a, v in dogrudan.items() if a in altli}
    if not sorunlu:
        return Result(K42, "gecti", "Alt hesabı olan ana hesaplara doğrudan kayıt yapılmamış.")
    drafts = []
    for ana in sorted(sorunlu):
        v = sorunlu[ana]
        toplam = sum(t for t, _, _ in v)
        drafts.append(Draft(f"{ana} ana hesabına alt hesap kullanılmadan kayıt yapılmış", "Orta", ana, toplam, f"thp-alt-{ana}",
                            f"{ana} hesabının alt hesapları kullanılıyor, ama {len(v)} satır doğrudan {ana} koduna işlenmiş "
                            f"(toplam {kurus_str(toplam)} TL).",
                            "Kayıt yeri kesin. " + K42.exceptions,
                            f"Bu satırları ilgili alt hesaba (cari, banka vb.) aktaran düzeltme kaydı yapın.",
                            [Evidence(r, f"{ana} ({BELGE_ADI[d]})") for _, r, d in v[:10]]))
    return Result(K42, "bulgu", f"{len(drafts)} ana hesaba alt hesap kullanılmadan kayıt yapılmış.", drafts)


# ---- K-43 Gelir tablosu hesaplarında ters bakiye ----------------------------

K43 = Rule("K-43", "1.0", "Gelir ve gider hesaplarında ters bakiye",
           "Gelir hesapları (600–602, 640–649, 671, 679) alacak, indirim, maliyet ve gider hesapları (610–612, 620–632, "
           "653–661, 680–689) borç bakiyesi verir. Tersi, iade, iskonto veya gider düzeltmelerinin Tekdüzen'deki hesabı "
           "yerine gelir/gider hesabının karşı tarafına işlendiğini gösterebilir (ör. satış iadesinin 610 yerine 600'ün "
           "borcuna yazılması).",
           "K", CERCEVE, (), DAYANAK, False,
           "Yıl içinde kapanış kaydı (690'a devir) yapılmışsa bakiyeler sıfırlanır; ters bakiye yalnızca kapanış öncesi "
           "anlamlıdır.", D0, "Orta")


def k43(ctx: Context) -> Result:
    from ..finance import ana_balances
    net, kaynak = ana_balances(ctx)
    if net is None and (ctx.mizan or ctx.yevmiye):
        # Yalnızca dönem hareketleri varsa (devirsiz mizan, aylık yevmiye) gelir tablosu hesaplarının yönü yine okunur.
        net = defaultdict(int)
        if ctx.mizan:
            for ln in ctx.leaves:
                net[ln.hesap_kodu[:3]] += (ln.borc or 0) - (ln.alacak or 0)
            kaynak = "mizan, dönem hareketleri"
        else:
            for j in ctx.yevmiye:
                if ctx.period_start <= j.tarih <= ctx.period_end:
                    net[j.hesap_kodu[:3]] += j.borc - j.alacak
            kaynak = "yevmiye, dönem hareketleri"
    if net is None:
        return Result(K43, "calistirilamadi", "Çalıştırılamadı: mizan, defteri kebir veya yevmiye gerekli.")
    drafts = []
    for ana in sorted(net):
        n = net[ana]
        if abs(n) <= ctx.tolerance:
            continue
        if ana in GELIR_ALACAK and n > 0:
            yon, olagan = "borç", "alacak"
        elif ana in GIDER_BORC and n < 0:
            yon, olagan = "alacak", "borç"
        else:
            continue
        drafts.append(Draft(f"{ana} {THP[ana]} hesabı {yon} bakiyesi veriyor", ctx.rule_severity(n), ana, abs(n),
                            f"thp-ters6-{ana}",
                            f"{ana} {THP[ana]} hesabı {olagan} bakiyesi vermeli; {kurus_str(abs(n))} TL {yon} bakiyesi var "
                            f"({kaynak}).",
                            "Bakiye aritmetik olarak kesin. " + K43.exceptions,
                            "Hesabın karşı tarafına yazılmış kayıtları inceleyin: satış iadesi 610, iskonto 611, gider "
                            "iadesi/düzeltmesi ilgili gider hesabının alacağı yerine gelir hesabına mı işlenmiş bakın."))
    if not drafts:
        return Result(K43, "gecti", f"Gelir ve gider hesaplarının bakiye yönü olağan ({kaynak}).")
    return Result(K43, "bulgu", f"{len(drafts)} gelir tablosu hesabında ters bakiye var.", drafts)


# ---- K-44 7/A ve 7/B birlikte kullanımı --------------------------------------

K44 = Rule("K-44", "1.0", "7/A ve 7/B maliyet hesaplarının birlikte kullanılması",
           "İşletme maliyet hesapları için ya 7/A (710–782, fonksiyon esası) ya da 7/B (790–799, gider çeşitleri esası) "
           "seçeneğini kullanır. Aynı dönemde ikisine birden kayıt yapılmışsa listelenir.",
           "K", CERCEVE, (), DAYANAK, False,
           "Seçenek değişikliği yalnızca yıl başında yapılır; yıl içinde geçiş varsa kayıtların tümü yeni seçeneğe "
           "aktarılmalıdır.", D0, "Yüksek")


def k44(ctx: Context) -> Result:
    if _yok_veri(ctx):
        return Result(K44, "calistirilamadi", CALISMADI)
    a, b = defaultdict(list), defaultdict(list)
    for doc, kod, _ad, tutar, row, kayit in _satirlar(ctx):
        if not kayit or not tutar:
            continue
        ana = _ana((kod or "").strip())
        if ana in YEDI_A:
            a[ana].append(row)
        elif ana in YEDI_B:
            b[ana].append(row)
    if not (a and b):
        secenek = "7/A" if a else "7/B" if b else None
        return Result(K44, "gecti", f"Maliyet hesaplarında tek seçenek kullanılmış ({secenek})." if secenek
                      else "Maliyet (7) hesaplarına kayıt yok.")
    return Result(K44, "bulgu", "7/A ve 7/B maliyet hesapları birlikte kullanılmış.", [Draft(
        "7/A ve 7/B maliyet hesapları birlikte kullanılmış", "Yüksek", None, None, "thp-7ab",
        f"7/A hesapları: {', '.join(sorted(a))}; 7/B hesapları: {', '.join(sorted(b))}.",
        "Hesap kodları kesin. " + K44.exceptions,
        "Mükellefin seçtiği seçeneği belirleyin ve diğer seçenekteki kayıtları ona aktarın.",
        [Evidence(r, f"{k} (7/A)") for k in sorted(a) for r in a[k][:2]][:5] +
        [Evidence(r, f"{k} (7/B)") for k in sorted(b) for r in b[k][:2]][:5])])


HESAP_PLANI_RULES = [(K40, k40), (K41, k41), (K42, k42), (K43, k43), (K44, k44)]
