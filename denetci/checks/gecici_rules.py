"""Geçici vergi ön kontrolleri: K-27 dönem sonu hazırlığı, K-29 peşin ödenen vergilerin mahsubu.

Bunlar hatırlatma niteliğindedir: kayıt "izinin" olup olmadığına bakılır. Yokluk her zaman hata değildir
(ör. amortismana tabi varlık yoksa amortisman kaydı da olmaz).
"""

import re

from ..finance import ana_balances
from ..gecici import donem_no
from ..parsing import fold, kurus_str
from .kdv import movements
from .rules import D0, Context, Draft, Result, Rule

GV_DAYANAK = ("VUK değerleme hükümleri ve geçici vergi uygulaması (GVK mük. 120). Hesap kodları Tekdüzen genel kullanımıdır. "
              "Dayanak kaynağı henüz kullanıcı tarafından doğrulanmadı.")
DOVIZ_RE = re.compile(r"\b(usd|eur|euro|doviz|dolar|gbp|sterlin|chf|\$|€)\b")

K27 = Rule("K-27", "1.0", "Geçici vergi dönem sonu hazırlığı",
           "Geçici vergi dönem sonunda amortisman, kur değerlemesi ve satılan malın maliyeti kayıtlarının izine bakılır.",
           "D", ("THP_VUK",), (), GV_DAYANAK, False,
           "Amortismana tabi varlık, dövizli hesap veya stoklu satış yoksa ilgili kayıt gerekmez. Kayıt başka hesaplarla "
           "veya yıl sonunda yapılıyor olabilir.", D0, "Orta")
K29 = Rule("K-29", "1.0", "Peşin ödenen vergilerin mahsubu",
           "Yıl sonunda 193 Peşin Ödenen Vergiler hesabında kalan tutar, kapanışta 371 ile mahsup edilmeli veya iade/mahsup "
           "talebine konu edilmeli.",
           "K", ("THP_VUK",), (), GV_DAYANAK, False,
           "Kapanış kayıtları yapılmadan önce 193'te bakiye olması olağandır; iade ve mahsup talepleri.", D0, "Orta")


def _names(ctx: Context) -> dict:
    out = {}
    for src in (ctx.mizan or []), (ctx.kebir or []), (ctx.muavin or []), (ctx.yevmiye or []):
        for ln in src:
            if getattr(ln, "hesap_adi", None):
                out[ln.hesap_kodu] = ln.hesap_adi
    return out


def k27(ctx: Context) -> Result:
    if donem_no(ctx.period_end) is None:
        return Result(K27, "uygulanmadi", "Uygulanmadı: dönem sonu bir geçici vergi dönem sonu (Mart, Haziran, Eylül) değil.")
    m = movements(ctx)
    bal, _ = ana_balances(ctx)
    if m is None:
        return Result(K27, "calistirilamadi", "Çalıştırılamadı: dönem hareketleri yok.")
    bal = bal or {}
    drafts, ok = [], []
    # Amortisman: maddi/maddi olmayan duran varlık varsa birikmiş amortisman (257/268) alacak hareketi beklenir.
    if (bal.get("253", 0) + bal.get("254", 0) + bal.get("255", 0) + bal.get("260", 0) + bal.get("264", 0)) > 0:
        if m.total(("257", "268"), "alacak") > 0:
            ok.append("amortisman")
        else:
            drafts.append(Draft("Dönem sonu amortisman kaydı görünmüyor", K27.default_severity, "257", None, "amortisman",
                                f"Duran varlık hesaplarında bakiye var ama {m.window[0]:%d.%m.%Y} – {m.window[1]:%d.%m.%Y} "
                                f"arasında 257/268 hesaplarına alacak kaydı yok ({m.source}).",
                                "Amortisman yıl sonunda topluca ayrılıyor olabilir; geçici vergi döneminde ayrılması kâr tahminini etkiler.",
                                "Dönem amortismanının (kıst dahil) kayda alınıp alınmadığını kontrol edin."))
    # Kur değerlemesi: dövizli hesap varsa 646/656 hareketi beklenir.
    names = _names(ctx)
    doviz = sorted(k for k, v in names.items() if DOVIZ_RE.search(fold(v)) and k[:1] in ("1", "3", "4", "2"))
    if doviz:
        if m.total(("646", "656"), "alacak") + m.total(("646", "656"), "borc") > 0:
            ok.append("kur değerlemesi")
        else:
            drafts.append(Draft("Dövizli hesaplar var, kur değerlemesi kaydı görünmüyor", K27.default_severity, doviz[0], None, "kur",
                                f"Adında döviz geçen {len(doviz)} hesap var (ör. {doviz[0]} {names[doviz[0]]}); dönemde 646/656 "
                                f"hareketi yok ({m.source}).",
                                "Dövizli hesaplar adlarından tanındı; yanılabilir.",
                                "Dönem sonu kuruyla değerleme yapılıp yapılmadığını kontrol edin."))
    # Satılan malın maliyeti: satış varken 62x borç hareketi beklenir.
    satis = m.total(("600", "601"), "alacak")
    if satis > 0:
        if m.total(("620", "621", "622", "623"), "borc") > 0:
            ok.append("satış maliyeti")
        else:
            drafts.append(Draft("Satış var, satışların maliyeti kaydı görünmüyor", K27.default_severity, "620", None, "smm",
                                f"Dönemde {kurus_str(satis)} TL satış var; 620-623 hesaplarına borç kaydı yok ({m.source}).",
                                "Maliyet yıl sonunda veya aralıklı envanterle hesaplanıyor olabilir; hizmet işletmelerinde 622 kullanılır.",
                                "Geçici vergi kârının doğru çıkması için dönem sonu maliyet kaydını kontrol edin."))
    if drafts:
        return Result(K27, "bulgu", f"{len(drafts)} dönem sonu kaydının izi bulunamadı.", drafts)
    if not ok:
        return Result(K27, "bilgi", "Amortisman, kur değerlemesi veya maliyet kaydı gerektiren bir durum görülmedi.")
    return Result(K27, "gecti", "Dönem sonu kayıtlarının izi var: " + ", ".join(ok) + ".")


def k29(ctx: Context) -> Result:
    if not (ctx.period_end.month == 12 and ctx.period_end.day == 31):
        return Result(K29, "uygulanmadi", "Uygulanmadı: yalnızca yıl sonu (31 Aralık) döneminde çalışır.")
    bal, src = ana_balances(ctx)
    if bal is None:
        return Result(K29, "calistirilamadi", "Çalıştırılamadı: yıl sonu bakiyeleri yok (kümülatif mizan veya defteri kebir gerekli).")
    b193 = bal.get("193", 0)
    if b193 > ctx.tolerance:
        return Result(K29, "bulgu", "193'te yıl sonunda bakiye var.", [Draft(
            "193 Peşin ödenen vergiler yıl sonunda mahsup edilmemiş", K29.default_severity, "193", b193, "193",
            f"31.12 itibarıyla 193 hesabında {kurus_str(b193)} TL borç bakiyesi var ({src}).",
            "Kapanış kayıtları henüz yapılmadıysa olağandır.",
            "Kapanışta 371 ile mahsup edildiğini veya iade/mahsup talebine konu edildiğini kontrol edin.")])
    return Result(K29, "gecti", "193 hesabında yıl sonu bakiyesi yok.")


GECICI_RULES = [(K27, k27), (K29, k29)]
