"""KDV oranlarına göre döküm ve kontroller: K-25 (hesaplanan KDV), K-26 (indirilecek KDV).

Oran, Luca'daki alt hesabın adından okunur (ör. "600.01.020 %20 SATIŞLAR", "191.01.010 %10 İNDİRİLECEK KDV").
Hesap kodu kullanılmaz (600.01.001 gibi bir kod %1 anlamına gelmeyebilir). Oranı anlaşılamayan hesaplar "oranı belirsiz"
satırında gösterilir ve karşılaştırılmaz.

Defter tarafı: 600-602 alacak hareketi (matrah), 391 alacak hareketi (hesaplanan KDV), 191 borç hareketi
(indirilecek KDV). Beyanname tarafı: matrah ve vergi bildirimi, indirilecek KDV'nin oranlara göre dağılımı ve tam
istisna tablosu. Tutarlar kuruştur.
"""

import re
from dataclasses import dataclass, field

from ..parsing import fold, kurus_str
from .kdv import KDV_DAYANAK, movements
from .rules import D0, Context, Draft, Result, Rule

ORANLAR = (1, 8, 10, 18, 20)
_YUZDE_RE = re.compile(r"%\s*(\d{1,2})\b|\b(\d{1,2})\s*%")


def oran(code: str, name: str | None) -> int | None:
    m = _YUZDE_RE.search(name or "")
    if m:
        v = int(m.group(1) or m.group(2))
        return v if v in ORANLAR else None
    return None


def tur(code: str, name: str | None) -> str:
    n = fold(name)
    if code.startswith("601"):
        return "istisna"
    if "ihrac" in n and "kayit" in n:
        return "ihrac"
    if code.startswith("191") and ("tevkif" in n or "sorumlu" in n):
        return "sorumlu"
    if "ithal" in n:
        return "ithal"
    return "normal"


@dataclass
class Satir:
    anahtar: tuple
    ad: str
    oran: int | None = None
    matrah: int = 0  # defter: satış (hesaplanan) veya KDV/oran ile bulunan bedel (indirilecek)
    kdv: int = 0  # defter: 391 alacak / 191 borç
    beyan_matrah: int | None = None
    beyan_kdv: int | None = None
    hesaplar: list = field(default_factory=list)
    kanit: list = field(default_factory=list)
    karsilastir: bool = True
    taraf: str = "hes"  # hes: hesaplanan (satış), ind: indirilecek (alış)

    @property
    def beklenen(self) -> int | None:
        """Satış matrahı × oran (yalnızca hesaplanan tarafta, oranı bilinen normal satırlarda)."""
        if self.taraf == "hes" and self.anahtar[0] == "normal" and self.oran and self.matrah:
            return round(self.matrah * self.oran / 100)
        return None

    @property
    def fark(self) -> int | None:
        return self.kdv - self.beklenen if self.beklenen is not None else None

    @property
    def beyan_fark(self) -> int | None:
        return self.kdv - self.beyan_kdv if self.beyan_kdv is not None and self.karsilastir else None

    @property
    def beyan_matrah_fark(self) -> int | None:
        return self.matrah - self.beyan_matrah if self.beyan_matrah is not None and self.karsilastir else None


@dataclass
class Dokum:
    kaynak: str
    pencere: tuple
    hesaplanan: list[Satir]
    indirilecek: list[Satir]
    beyan: bool
    oran_bulundu: bool
    diger_hesaplanan: int | None = None  # beyannamede orana bağlı olmayan hesaplanan KDV (kur farkı, yansıtma vb.)
    sorumlu_beyan: int | None = None

    @property
    def toplam(self):
        return {
            "matrah": sum(r.matrah for r in self.hesaplanan),
            "hes": sum(r.kdv for r in self.hesaplanan),
            "beklenen": sum(r.beklenen or 0 for r in self.hesaplanan),
            "ind": sum(r.kdv for r in self.indirilecek),
        }


def _satir(rows: dict, key: tuple, ad: str, o, taraf: str = "hes") -> Satir:
    if key not in rows:
        rows[key] = Satir(key, ad, o, taraf=taraf)
    return rows[key]


def _ad(t: str, o) -> str:
    return {"normal": f"%{o}" if o else "Oranı belirsiz", "ihrac": "İhraç kayıtlı teslim", "istisna": "İhracat / tam istisna",
            "ithal": f"İthalat %{o}" if o else "İthalat", "sorumlu": "Sorumlu sıfatıyla (tevkifat)"}[t]


def dokum(ctx: Context) -> Dokum | None:
    m = movements(ctx)
    if m is None:
        return None
    b = ctx.kdv_beyan or {}
    hes: dict = {}
    ind: dict = {}
    oran_var = False
    for code in sorted(set(m.borc) | set(m.alacak)):
        name = m.names.get(code)
        if code.startswith(("600", "601", "602", "391")):
            t, o = tur(code, name), oran(code, name)
            if t == "normal" and o is None:
                t = "belirsiz"
            oran_var |= o is not None
            key = (t, o) if t == "normal" else (t,)
            r = _satir(hes, key, "Oranı belirsiz" if t == "belirsiz" else _ad(t, o), o)
            if t == "belirsiz":
                r.karsilastir = False
            if code.startswith("391"):
                r.kdv += m.alacak.get(code, 0)
            else:
                r.matrah += m.alacak.get(code, 0)
            if m.alacak.get(code):
                r.hesaplar.append(code)
                r.kanit += m.rows[code][:10]
        elif code.startswith("191"):
            t, o = tur(code, name), oran(code, name)
            oran_var |= o is not None
            if t == "sorumlu":
                key = ("sorumlu",)
            elif o is None:
                key = ("belirsiz",)
            else:
                key = ("normal", o)  # ithalat KDV'si de oranının satırına eklenir
            r = _satir(ind, key, "Oranı belirsiz" if key == ("belirsiz",) else _ad(key[0], o), o, "ind")
            if key == ("belirsiz",):
                r.karsilastir = False
            r.kdv += m.borc.get(code, 0)
            if m.borc.get(code):
                r.hesaplar.append(code + (" (ithalat)" if t == "ithal" else ""))
                r.kanit += m.rows[code][:10]
    for r in ind.values():
        if r.oran:
            r.matrah = round(r.kdv * 100 / r.oran)

    sorumlu_beyan = None
    if b:
        for x in b.get("hesaplanan_oranlar", []):
            key = ("ihrac",) if x["tur"] == "ihrac" else ("normal", x["oran"])
            if x["tur"] == "tevkifatli":
                key = ("normal", x["oran"])
            r = _satir(hes, key, _ad(key[0], x["oran"]), x["oran"])
            r.beyan_matrah = (r.beyan_matrah or 0) + x["matrah"]
            r.beyan_kdv = (r.beyan_kdv or 0) + x["vergi"]
            if r.oran is None:
                r.oran = x["oran"]
        for x in b.get("istisnalar", []):
            r = _satir(hes, ("istisna",), _ad("istisna", None), None)
            r.beyan_matrah = (r.beyan_matrah or 0) + x["tutar"]
            r.beyan_kdv = 0
        for x in b.get("indirim_oranlar", []):
            r = _satir(ind, ("normal", x["oran"]), _ad("normal", x["oran"]), x["oran"], "ind")
            r.beyan_matrah = (r.beyan_matrah or 0) + x["bedel"]
            r.beyan_kdv = (r.beyan_kdv or 0) + x["kdv"]
        if b.get("sorumlu") is not None:
            sorumlu_beyan = b["sorumlu"]
            if b["sorumlu"] or ("sorumlu",) in ind:
                r = _satir(ind, ("sorumlu",), _ad("sorumlu", None), None, "ind")
                r.beyan_kdv = b["sorumlu"]
        # Beyanname okunduysa, beyannamede olmayan oranlar beyanda sıfırdır.
        if b.get("hesaplanan_oranlar") is not None:
            for r in hes.values():
                if r.karsilastir and r.beyan_kdv is None and r.anahtar[0] != "istisna":
                    r.beyan_kdv, r.beyan_matrah = 0, 0
        if b.get("indirim_oranlar") is not None:
            for r in ind.values():
                if r.karsilastir and r.beyan_kdv is None:
                    r.beyan_kdv, r.beyan_matrah = 0, None
    diger = None
    if b.get("hesaplanan") is not None and b.get("hesaplanan_oranlar") is not None:
        diger = b["hesaplanan"] - sum(x["vergi"] for x in b["hesaplanan_oranlar"])
    for r in ind.values():
        if r.anahtar == ("sorumlu",):
            r.beyan_matrah = None

    sira = {"normal": 0, "ihrac": 1, "istisna": 2, "sorumlu": 3, "belirsiz": 4}

    def sk(r):
        return (sira.get(r.anahtar[0], 5), -(r.oran or 0))

    return Dokum(m.source, m.window, sorted(hes.values(), key=sk), sorted(ind.values(), key=sk),
                 bool(b.get("hesaplanan_oranlar") or b.get("indirim_oranlar")), oran_var, diger, sorumlu_beyan)


def durum(ctx: Context, r: Satir) -> str | None:
    """Tablo için satır durumu: 'fark', 'ok' veya None (karşılaştırılacak bir şey yok)."""
    checks = []
    if r.fark is not None:
        checks.append(abs(r.fark) <= _tol(ctx, r.beklenen))
    if r.anahtar[0] == "istisna":
        if r.beyan_matrah_fark is not None:
            checks.append(abs(r.beyan_matrah_fark) <= _tol(ctx, r.beyan_matrah))
    elif r.beyan_fark is not None:
        checks.append(abs(r.beyan_fark) <= _tol(ctx, r.beyan_kdv))
    if r.taraf == "hes" and r.anahtar[0] == "normal" and r.oran and not r.matrah and r.kdv > ctx.tolerance:
        checks.append(False)
    if not checks:
        return None
    return "ok" if all(checks) else "fark"


def ozet(ctx: Context) -> dict | None:
    """KDV hesap özeti: defterden hesaplanan sonuç ve (varsa) beyannamedeki karşılıkları."""
    m = movements(ctx)
    if m is None:
        return None
    hes, ind = m.total(("391",), "alacak"), m.total(("191",), "borc")
    dev = ctx.devreden_kdv
    sonuc = hes - ind - (dev or 0)
    b = ctx.kdv_beyan or {}
    beyan = None
    if b:
        beyan = {"hes": b.get("hesaplanan"), "ind": (b.get("indirimler_toplami") or 0) - (b.get("onceki_devreden") or 0),
                 "dev": b.get("onceki_devreden"), "odenecek": b.get("odenecek"), "devreden": b.get("sonraki_devreden")}
    return {"hes": hes, "ind": ind, "dev": dev, "dev_kaynak": ctx.devreden_kaynak, "sonuc": sonuc, "beyan": beyan,
            "kaynak": m.source, "pencere": m.window}


def _tol(ctx: Context, base: int) -> int:
    # Fatura bazında yuvarlama farkları için tutarın binde biri kadar pay bırakılır.
    return max(ctx.tolerance, round(abs(base) * 0.001))


# ---- K-25 Oranlara göre hesaplanan KDV ----------------------------------------------

K25 = Rule("K-25", "1.0", "Oranlara göre hesaplanan KDV",
           "Her KDV oranı için satış hesabındaki matrah × oran, aynı oranlı 391 alt hesabındaki KDV ile karşılaştırılır. "
           "Ayın KDV beyannamesi yüklüyse, beyannamedeki oran bazında matrah ve vergi de defterle karşılaştırılır.",
           "K", ("THP_VUK",), (), KDV_DAYANAK, False,
           "Oran, alt hesap adından okunur; hesap adı yanlışsa sonuç da yanlış olur. Sabit kıymet satışı, kur farkı, "
           "gider yansıtması gibi 600-602 dışında kaydedilen matrahlar, KDV'si ayrıca hesaplanan işlemler, tevkifatlı "
           "satışlar ve iadeler fark yaratabilir.", D0, "Orta")

K26 = Rule("K-26", "1.0", "Oranlara göre indirilecek KDV",
           "191 alt hesaplarındaki KDV oran bazında toplanır; ayın KDV beyannamesindeki 'bu döneme ait indirilecek KDV "
           "tutarının oranlara göre dağılımı' ve sorumlu sıfatıyla ödenen KDV ile karşılaştırılır.",
           "K", ("THP_VUK",), (), KDV_DAYANAK, False,
           "İthalatta ödenen KDV'nin beyannamede hangi satırda gösterildiği, KKEG'e atılan KDV, iade faturaları ve "
           "beyannameden sonra yapılan düzeltmeler fark yaratabilir. Oran alt hesap adından okunur.", D0, "Orta")


def k25(ctx: Context) -> Result:
    d = dokum(ctx)
    if d is None:
        return Result(K25, "calistirilamadi", "Çalıştırılamadı: dönem hareketleri yok.")
    if not d.oran_bulundu:
        return Result(K25, "uygulanmadi", "Uygulanmadı: 391 ve 600 alt hesaplarının adında KDV oranı yok "
                                          "(ör. '%20 HESAPLANAN KDV', '%20 SATIŞLAR').")
    drafts, ok = [], []
    for r in d.hesaplanan:
        if r.fark is not None and abs(r.fark) > _tol(ctx, r.beklenen):
            drafts.append(Draft(f"{r.ad} satışlarda KDV tutarı matrahla uyuşmuyor", K25.default_severity, "391", r.fark,
                                f"oran-{r.oran}",
                                f"{r.ad} oranlı satış hesaplarında matrah {kurus_str(r.matrah)} TL; bu matrahla KDV "
                                f"{kurus_str(r.beklenen)} TL olmalı. Aynı oranlı 391 alt hesabında {kurus_str(r.kdv)} TL var "
                                f"({d.kaynak}). Fark {kurus_str(r.fark)} TL. Hesaplar: {', '.join(r.hesaplar) or '—'}.",
                                K25.exceptions,
                                "Bu orandaki satış faturalarını Luca'da listeleyip KDV'nin doğru oranlı 391 alt hesabına "
                                "kaydedildiğini kontrol edin.", r.kanit[:30]))
        elif r.fark is not None:
            ok.append(r.ad)
        if r.anahtar[0] == "normal" and r.oran and not r.matrah and r.kdv > ctx.tolerance:
            drafts.append(Draft(f"{r.ad} KDV kaydı var, aynı oranlı satış yok", "Düşük", "391", r.kdv, f"oran-satissiz-{r.oran}",
                                f"391'de {r.ad} oranlı {kurus_str(r.kdv)} TL KDV var ama aynı oranlı satış hesabında hareket yok.",
                                "Sabit kıymet satışı veya diğer gelirler gibi 600 dışı işlemler olabilir.",
                                "KDV'nin hangi gelir kaydına ait olduğunu kontrol edin.", r.kanit[:30]))
        bf = r.beyan_fark
        if bf is not None and abs(bf) > _tol(ctx, r.beyan_kdv or 0) and r.anahtar[0] != "istisna":
            drafts.append(Draft(f"{r.ad} hesaplanan KDV beyannameyle defterde farklı", K25.default_severity, "391", bf,
                                f"beyan-oran-{r.anahtar}",
                                f"Beyannamede {r.ad} için matrah {kurus_str(r.beyan_matrah or 0)} TL, vergi "
                                f"{kurus_str(r.beyan_kdv)} TL; defterde matrah {kurus_str(r.matrah)} TL, KDV "
                                f"{kurus_str(r.kdv)} TL. KDV farkı {kurus_str(bf)} TL.",
                                K25.exceptions, "Beyannamedeki oran satırını aynı oranlı 391 ve satış alt hesaplarıyla "
                                                "karşılaştırın.", r.kanit[:30]))
        mf = r.beyan_matrah_fark
        if r.anahtar[0] == "istisna" and mf is not None and abs(mf) > _tol(ctx, r.beyan_matrah):
            drafts.append(Draft("İhracat (istisna) tutarı beyannameyle defterde farklı", "Düşük", "601", mf, "beyan-istisna",
                                f"Beyannamedeki tam istisna teslim tutarı {kurus_str(r.beyan_matrah)} TL; 601 Yurtdışı "
                                f"Satışlar alacak hareketi {kurus_str(r.matrah)} TL. Fark {kurus_str(mf)} TL.",
                                "Gümrük beyannamesi tarihi ile fatura tarihi farklı aylarda olabilir; kur farkı ve "
                                "navlun gibi kalemler de fark yaratabilir.",
                                "İhracat faturalarını gümrük beyannamesi tarihleriyle karşılaştırın.", r.kanit[:30]))
    if drafts:
        return Result(K25, "bulgu", f"{len(drafts)} oran satırında fark var.", drafts)
    msg = "Oranlara göre KDV tutarları matrahla uyumlu" + (f" ({', '.join(ok)})" if ok else "")
    return Result(K25, "gecti", msg + (" ve beyannameyle aynı." if d.beyan else "."))


def k26(ctx: Context) -> Result:
    d = dokum(ctx)
    if d is None:
        return Result(K26, "calistirilamadi", "Çalıştırılamadı: dönem hareketleri yok.")
    if not (ctx.kdv_beyan or {}).get("indirim_oranlar"):
        return Result(K26, "uygulanmadi", "Uygulanmadı: bu ayın KDV beyannamesi (PDF) yüklenmemiş veya oran dağılımı okunamadı.")
    drafts, ok = [], []
    for r in d.indirilecek:
        bf = r.beyan_fark
        if bf is None:
            continue
        if abs(bf) > _tol(ctx, r.beyan_kdv):
            drafts.append(Draft(f"{r.ad} indirilecek KDV beyannameyle defterde farklı", K26.default_severity, "191", bf,
                                f"ind-{r.anahtar}",
                                f"Beyannamede {r.ad} için indirilecek KDV {kurus_str(r.beyan_kdv)} TL; defterde 191 "
                                f"alt hesaplarında {kurus_str(r.kdv)} TL ({', '.join(r.hesaplar) or 'hareket yok'}). "
                                f"Fark {kurus_str(bf)} TL.",
                                K26.exceptions, "Bu orandaki alış faturalarını ve 191 alt hesabını beyannameyle "
                                                "karşılaştırın; yanlış oranlı alt hesaba kayıt olup olmadığına bakın.", r.kanit[:30]))
        else:
            ok.append(r.ad)
    if drafts:
        return Result(K26, "bulgu", f"{len(drafts)} oran satırında fark var.", drafts)
    return Result(K26, "gecti", "İndirilecek KDV oran dağılımı beyannameyle aynı" + (f" ({', '.join(ok)})." if ok else "."))


ORAN_RULES = [(K25, k25), (K26, k26)]
