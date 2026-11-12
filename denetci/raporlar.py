"""Konu başlıklarına göre raporlar: kapsamlı genel rapor ve her konu için ayrıntılı rapor.

Kullanıcı rapor sayfasında istediği başlıkları seçer; seçilen her konu raporda ayrı bir bölüm (yazdırınca ayrı
sayfa) olarak yer alır. Bu modül konuların listesini ve finansal analiz raporunun ek hesaplarını (aylık gelişim,
devir süreleri, kârlılık, DuPont, gider dağılımı) içerir. Tutarlar kuruştur.

Oran yorumlarındaki aralıklar genel kabul gören yaklaşık değerlerdir; sektöre göre değişir. Yorumlar "iyi/kötü"
hükmü değil, okuyana yol gösteren açıklamalardır.
"""

from dataclasses import dataclass, field
from datetime import date

from .parsing import kurus_str


@dataclass(frozen=True)
class Konu:
    key: str
    baslik: str
    aciklama: str


KONULAR = [
    Konu("genel", "Genel rapor", "Tüm konuların özeti, değerlendirme ve öneriler, bulgular, kontrol sonuçları."),
    Konu("finansal", "Finansal analiz", "Grafiklerle gelir tablosu, bilanço yapısı, aylık gelişim, oranlar ve yorumları."),
    Konu("kdv", "KDV", "KDV hesap özeti, oranlara göre matrah, beyan–defter karşılaştırması, KDV kontrolleri."),
    Konu("gecici", "Geçici vergi", "Yılbaşından ticari kâr, matrah ve vergi hesabı, dönem sonu hazırlığı."),
    Konu("ceyrek", "Çeyrek analizi", "Çeyreğin ay ay gelir tablosu, önceki çeyrek ve geçen yılla karşılaştırma."),
    Konu("risk", "Hata ve risk taraması", "Ters bakiye, kasa, ortaklar cari, örtülü sermaye, binek oto ve diğer riskler."),
    Konu("mutabakat", "Defter mutabakatı", "Yevmiye, kebir, muavin ve mizanın birbiriyle tutarlılığı."),
    Konu("fatura", "Fatura mutabakatı", "e-Fatura / e-Arşiv listesinin deftere işlenip işlenmediği."),
    Konu("kredibilite", "Kredibilite (banka görünümü)", "Bankanın kredi verirken baktığı oranlar, Altman Z, A–E not ve istenecek belgeler."),
]
KONU = {k.key: k for k in KONULAR}


def secilen(konular) -> list[str]:
    """Adres satırındaki konu listesini doğrular, sırayı KONULAR'a göre düzenler; boşsa genel rapor."""
    istenen = {k for k in (konular or []) if k in KONU}
    return [k.key for k in KONULAR if k.key in istenen] or ["genel"]


def baslik(konular: list[str]) -> str:
    if konular == ["genel"]:
        return "Dönem Kontrol Raporu"
    if len(konular) == 1:
        return f"{KONU[konular[0]].baslik} Raporu"
    return "Dönem Raporu"


# ---- Finansal analiz -------------------------------------------------------------------------------------------

def _x(v):
    return f"{v:.2f}".replace(".", ",")


def _pct(v):
    return f"%{v * 100:.1f}".replace(".", ",")


def _gun(v):
    return f"{v:.0f} gün"


@dataclass
class Gosterge:
    """Raporda yorumuyla birlikte gösterilen bir oran."""
    grup: str
    ad: str
    deger: float | None
    bicim: str  # x, pct, gun
    formul: str
    yorum: str = ""
    durum: str = "notr"  # iyi, dikkat, zayif, notr
    aralik: tuple | None = None  # (alt, üst) genel kabul gören aralık; gösterge çubuğunda taranır
    olcek: float | None = None  # gösterge çubuğunun üst ucu
    onceki: float | None = None

    @property
    def metin(self):
        if self.deger is None:
            return "—"
        return {"pct": _pct, "gun": _gun}.get(self.bicim, _x)(self.deger)

    @property
    def onceki_metin(self):
        if self.onceki is None:
            return ""
        return {"pct": _pct, "gun": _gun}.get(self.bicim, _x)(self.onceki)


@dataclass
class Finansal:
    gun: int | None = None
    gostergeler: list = field(default_factory=list)
    dupont: dict | None = None
    aylar: list = field(default_factory=list)  # ceyrek.Kolon
    giderler: list = field(default_factory=list)  # (kod, ad, tutar)
    ozet: list = field(default_factory=list)  # (ad, değer, önceki, açıklama)
    yorumlar: list = field(default_factory=list)  # düz metin cümleleri


def _div(a, b):
    return a / b if b else None


def _aylar_listesi(bas: date, bit: date, en_az: int = 3, en_fazla: int = 12):
    """Grafikte gösterilecek aylar: dönemin ayları; dönem kısaysa dönem sonundan geriye en az 3 ay."""
    aylar, y, m = [], bas.year, bas.month
    while (y, m) <= (bit.year, bit.month):
        aylar.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    while len(aylar) < en_az:
        y, m = aylar[0]
        aylar.insert(0, (y - 1, 12) if m == 1 else (y, m - 1))
    return aylar[-en_fazla:]


def aylik(session, client, period) -> list:
    """Dönem (en az son 3 ay) için ay ay gelir tablosu kolonları; veri olmayan ay var=False döner."""
    from . import ceyrek
    out = []
    for y, m in _aylar_listesi(period.baslangic, period.bitis):
        out.append(ceyrek._kolon(f"{ceyrek.AYLAR[m - 1][:3]} {str(y)[2:]}", date(y, m, 1), ceyrek.ay_sonu(y, m),
                                 ceyrek._ay_satirlari(session, client, y, m)))
    # Baştaki verisiz aylar grafiği boşuna uzatmasın
    while len(out) > 1 and not out[0].var:
        out.pop(0)
    return out if any(k.var for k in out) else []


def finansal(fin, prev_fin=None, aylar=()) -> Finansal:
    f = Finansal(aylar=list(aylar))
    if fin is None:
        return f
    k = fin.kalemler
    r = {o.key: o.value for o in fin.oranlar}
    pr = {o.key: o.value for o in prev_fin.oranlar} if prev_fin else {}
    pk = prev_fin.kalemler if prev_fin else {}
    if fin.flows_window:
        f.gun = (fin.flows_window[1] - fin.flows_window[0]).days + 1
    gun = f.gun

    ns, smm = k.get("net_satis"), None
    if ns is not None:
        smm = ns - k.get("brut_kar", 0)

    # --- Özet tutarlar ---
    if fin.has_income:
        f.ozet += [("Net satışlar", ns, pk.get("net_satis"), ""),
                   ("Brüt satış kârı", k.get("brut_kar"), pk.get("brut_kar"), ""),
                   ("Dönem kârı (vergi öncesi)", k.get("donem_kari"), pk.get("donem_kari"), "")]
    if fin.has_balance:
        f.ozet += [("Aktif toplamı", fin.aktif, prev_fin.aktif if prev_fin else None, ""),
                   ("Özkaynak (dönem sonucu dahil)", k.get("ozkaynak"), pk.get("ozkaynak"), "")]

    G = f.gostergeler
    # --- Likidite ---
    if fin.has_balance:
        cari = r.get("cari")
        if cari is not None:
            durum = "zayif" if cari < 1 else ("dikkat" if cari < 1.5 else "iyi")
            yorum = (f"Her 1 TL kısa vadeli borca karşılık {_x(cari)} TL dönen varlık var. "
                     + {"zayif": "Kısa vadeli borçlar dönen varlıklardan fazla; ödemelerde sıkışma yaşanabilir.",
                        "dikkat": "Borçlar karşılanıyor ama tampon dar.",
                        "iyi": "Kısa vadeli borçları karşılama gücü yeterli."}[durum])
            if cari > 3:
                yorum += " Oranın çok yüksek olması atıl nakit, tahsil edilemeyen alacak veya fazla stok işareti olabilir."
            G.append(Gosterge("Likidite", "Cari oran", cari, "x", "Dönen varlıklar / Kısa vadeli yabancı kaynaklar",
                              yorum, durum, (1.5, 2.0), 4, pr.get("cari")))
        asit = r.get("asit")
        if asit is not None:
            durum = "zayif" if asit < 0.7 else ("dikkat" if asit < 1 else "iyi")
            G.append(Gosterge("Likidite", "Asit-test oranı", asit, "x", "(Dönen varlıklar − Stoklar) / KVYK",
                              f"Stoklar satılmadan, kısa vadeli borçların {_pct(min(asit, 9.99))} kadarı ödenebilir. "
                              + ("Likidite büyük ölçüde stokların satışına bağlı." if durum == "zayif" else
                                 "Stoklara bağımlılık makul düzeyde." if durum == "dikkat" else "Stok satışına bağlı olmadan borçlar karşılanabiliyor."),
                              durum, (1.0, 1.5), 3, pr.get("asit")))
        nakit = r.get("nakit")
        if nakit is not None:
            durum = "zayif" if nakit < 0.1 else ("dikkat" if nakit < 0.2 else "iyi")
            G.append(Gosterge("Likidite", "Nakit oranı", nakit, "x", "Hazır değerler / KVYK",
                              f"Kasa ve bankadaki para kısa vadeli borçların {_pct(min(nakit, 9.99))} kadarını hemen karşılıyor."
                              + (" Yakın vadeli ödemeler tahsilatlara bağlı." if durum == "zayif" else ""),
                              durum, (0.2, 0.5), 1.5, pr.get("nakit")))
        # --- Mali yapı ---
        kal = r.get("kaldirac")
        if kal is not None:
            durum = "zayif" if kal > 0.7 else ("dikkat" if kal > 0.5 else "iyi")
            G.append(Gosterge("Mali yapı", "Kaldıraç oranı", kal, "pct", "Yabancı kaynaklar / Aktif toplamı",
                              f"Varlıkların {_pct(kal)}'i borçla, {_pct(1 - kal)}'i özkaynakla finanse ediliyor. "
                              + {"zayif": "Borç yükü yüksek; yeni borçlanma dikkatle değerlendirilmeli.",
                                 "dikkat": "Borç payı yarının üzerinde.", "iyi": "Finansman yapısı dengeli."}[durum],
                              durum, (0.0, 0.5), 1, pr.get("kaldirac")))
        bo = r.get("borc_ozk")
        if bo is not None:
            durum = "zayif" if (bo < 0 or bo > 3) else ("dikkat" if bo > 1.5 else "iyi")
            G.append(Gosterge("Mali yapı", "Borç / özkaynak", bo, "x", "Yabancı kaynaklar / Özkaynak",
                              ("Özkaynak eksiye düşmüş; borçlar şirketin kendi kaynağını aşıyor." if bo < 0 else
                               f"Her 1 TL özkaynağa karşılık {_x(bo)} TL borç var."),
                              durum, (0.0, 1.0), 4, pr.get("borc_ozk")))

    # --- Faaliyet (devir süreleri) ---
    if fin.has_balance and fin.has_income and gun and ns and ns > 0:
        ta = k.get("ticari_alacaklar", 0)
        sure = ta / ns * gun
        durum = "zayif" if sure > 120 else ("dikkat" if sure > 60 else "iyi")
        G.append(Gosterge("Faaliyet", "Alacak tahsil süresi", sure, "gun", f"Ticari alacaklar / Net satışlar × {gun} gün",
                          f"Satışlar ortalama {sure:.0f} günde tahsil ediliyor."
                          + (" Vadesi geçmiş alacaklar listelenip takip edilmeli; şüpheli alacak karşılığı (VUK 323) değerlendirilebilir."
                             if durum == "zayif" else ""), durum, (0, 60), 180, None))
        if smm and smm > 0:
            st = k.get("stoklar", 0)
            ss = st / smm * gun
            durum = "dikkat" if ss > 120 else "iyi"
            G.append(Gosterge("Faaliyet", "Stok devir süresi", ss, "gun", f"Stoklar / Satışların maliyeti × {gun} gün",
                              f"Stoklar ortalama {ss:.0f} günde satılıyor."
                              + (" Yavaş dönen veya değer kaybeden stoklar gözden geçirilmeli." if durum == "dikkat" else ""),
                              durum, (0, 90), 240, None))
            tb = k.get("ticari_borclar", 0)
            bs = tb / smm * gun
            G.append(Gosterge("Faaliyet", "Borç ödeme süresi", bs, "gun",
                              f"Ticari borçlar / Satışların maliyeti × {gun} gün (alışların yerine maliyet kullanıldı)",
                              f"Tedarikçilere ortalama {bs:.0f} günde ödeme yapılıyor.", "notr", None, 180, None))
            nd = sure + ss - bs
            G.append(Gosterge("Faaliyet", "Nakit dönüşüm süresi", nd, "gun", "Tahsil süresi + Stok süresi − Borç ödeme süresi",
                              (f"Mal alımına ödenen para ortalama {nd:.0f} gün sonra tahsilatla geri dönüyor; bu süre "
                               "işletme sermayesiyle finanse ediliyor." if nd > 0 else
                               "Tedarikçilere ödeme, tahsilattan sonra yapılıyor; işletme sermayesi ihtiyacı düşük."),
                              "dikkat" if nd > 90 else "notr", None, 240, None))
        elif ns > 0:
            f.yorumlar.append("Satışların maliyeti kaydı olmadığı için stok devir ve borç ödeme süreleri hesaplanmadı.")

    # --- Kârlılık ---
    if fin.has_income and ns and ns > 0:
        bm = r.get("brut_marj")
        if bm is not None:
            G.append(Gosterge("Kârlılık", "Brüt kâr marjı", bm, "pct", "Brüt satış kârı / Net satışlar",
                              f"Her 100 TL satıştan maliyet düşüldükten sonra {bm * 100:.1f} TL kalıyor.".replace(".", ",")
                              + (" Maliyet kaydı yok; marj gerçeğinden yüksek görünüyor." if smm == 0 else ""),
                              "dikkat" if smm == 0 else "notr", None, 1, pr.get("brut_marj")))
        fm = r.get("faaliyet_marj")
        if fm is not None:
            G.append(Gosterge("Kârlılık", "Faaliyet kâr marjı", fm, "pct", "Faaliyet kârı / Net satışlar",
                              f"Faaliyet giderleri de düşüldükten sonra satışların {_pct(fm)}'i kâr olarak kalıyor."
                              if fm >= 0 else "Esas faaliyet zarar ediyor; giderler brüt kârı aşıyor.",
                              "zayif" if fm < 0 else "notr", None, 1, pr.get("faaliyet_marj")))
        nm = r.get("net_marj")
        if nm is not None:
            G.append(Gosterge("Kârlılık", "Vergi öncesi kâr marjı", nm, "pct", "Dönem kârı / Net satışlar",
                              f"Tüm gelir ve giderlerden sonra satışların {_pct(nm)}'i kâr." if nm >= 0 else
                              "Dönem zararla kapanıyor.", "zayif" if nm < 0 else "notr", None, 1, pr.get("net_marj")))
    if fin.has_income and fin.has_balance and fin.aktif:
        dk = k.get("donem_kari", 0)
        roa = _div(dk, fin.aktif)
        G.append(Gosterge("Kârlılık", "Aktif kârlılığı", roa, "pct", "Dönem kârı / Aktif toplamı",
                          f"Şirketin varlıkları bu dönemde {_pct(roa)} getiri sağladı (yıllıklandırılmamış).",
                          "zayif" if roa < 0 else "notr", None, 1, None))
        oz = k.get("ozkaynak")
        if oz and oz > 0:
            roe = dk / oz
            G.append(Gosterge("Kârlılık", "Özkaynak kârlılığı", roe, "pct", "Dönem kârı / Özkaynak",
                              f"Ortakların şirkette bıraktığı her 100 TL bu dönemde {roe * 100:.1f} TL kâr üretti "
                              "(yıllıklandırılmamış).".replace(".", ",", 1), "zayif" if roe < 0 else "notr", None, 1, None))
            if ns and ns > 0:
                f.dupont = {"marj": dk / ns, "devir": ns / fin.aktif, "carpan": fin.aktif / oz, "roe": roe}

    # --- Gider dağılımı (ay kolonlarından) ---
    toplam = {}
    for kol in f.aylar:
        for kod, (ad, v) in kol.hesaplar.items():
            a0, v0 = toplam.get(kod, (ad, 0))
            toplam[kod] = (a0 or ad, v0 + v)
    f.giderler = sorted(((kod, ad or "", v) for kod, (ad, v) in toplam.items() if v > 0), key=lambda x: -x[2])[:8]

    # --- Genel yorum cümleleri ---
    var = [a for a in f.aylar if a.var and (a.kal.get("net_satis") or 0) != 0]
    if len(var) >= 2:
        ilk, son = var[0], var[-1]
        a0, a1 = ilk.kal["net_satis"], son.kal["net_satis"]
        if a0 > 0:
            d = (a1 - a0) / a0
            if abs(d) >= 0.1:
                f.yorumlar.append(f"Net satışlar {ilk.ad} ayından {son.ad} ayına {_pct(abs(d))} "
                                  f"{'arttı' if d > 0 else 'azaldı'} ({kurus_str(a0)} → {kurus_str(a1)} TL).")
        zararli = [a.ad for a in var if a.kal.get("donem_kari", 0) < 0]
        if zararli:
            f.yorumlar.append(f"Zararla kapanan ay: {', '.join(zararli)}.")
        maliyetsiz = [a.ad for a in var if a.kal.get("net_satis", 0) > 0 and a.smm == 0]
        if maliyetsiz:
            f.yorumlar.append(f"Satış olup maliyet kaydı olmayan ay: {', '.join(maliyetsiz)}; bu aylarda kâr yüksek görünür.")
    if f.giderler:
        tg = sum(v for _, _, v in f.giderler)
        kod, ad, v = f.giderler[0]
        f.yorumlar.append(f"En büyük gider kalemi {kod} {ad} ({kurus_str(v)} TL); gösterilen giderlerin {_pct(v / tg)}'i.")
    return f


# ---- Kredibilite (banka görünümü) ------------------------------------------------------------------------------

NOTLAR = {
    "A": "Güçlü: bankalar açısından kredi değerliliği yüksek görünüyor.",
    "B": "İyi: kredi kullanımında genel olarak sorun beklenmez; zayıf kalan göstergeler iyileştirilebilir.",
    "C": "Orta: kredi alınabilir ancak teminat ve faiz koşulları ağırlaşabilir.",
    "D": "Zayıf: bankalar ek teminat veya kefalet isteyebilir; önce zayıf göstergeler iyileştirilmeli.",
    "E": "Riskli: mevcut tablo kredi başvurusunda olumsuz değerlendirilebilir.",
}

BANKA_BELGELERI = [
    "Son iki yılın kurumlar (veya gelir) vergisi beyannamesi ile bilanço ve gelir tablosu",
    "Son dönem geçici vergi beyannamesi ve güncel mizan",
    "Vergi ve SGK borcu yoktur yazıları",
    "Faaliyet belgesi, imza sirküleri, ticaret sicil gazeteleri (kuruluş ve son değişiklikler)",
    "Ortakların ve yetkililerin kimlik bilgileri, gerekirse kefil bilgileri",
    "Diğer bankalardaki kredi ve limitlerin listesi (banka KKB raporunu ayrıca çeker)",
    "Alacak yaşlandırma listesi, çek/senet portföyü, önemli müşteri sözleşmeleri",
    "Teminat olarak gösterilecek gayrimenkul, araç veya makine belgeleri",
]


@dataclass
class KrediGosterge:
    ad: str
    deger: float | None
    bicim: str
    puan: int  # 0, 1, 2
    esik: str  # puanlama eşikleri, okunabilir
    yorum: str
    aralik: tuple | None = None
    olcek: float | None = None

    @property
    def metin(self):
        if self.deger is None:
            return "—"
        return {"pct": _pct, "gun": _gun}.get(self.bicim, _x)(self.deger)

    @property
    def durum(self):
        return {2: "iyi", 1: "dikkat", 0: "zayif"}[self.puan]


@dataclass
class Kredibilite:
    gostergeler: list = field(default_factory=list)
    altman: dict | None = None
    puan: int = 0
    en_fazla: int = 0
    harf: str | None = None
    guclu: list = field(default_factory=list)
    zayif: list = field(default_factory=list)
    eksik: list = field(default_factory=list)
    yillik_carpan: float = 1.0
    gun: int | None = None

    @property
    def yuzde(self):
        return self.puan / self.en_fazla if self.en_fazla else None


def _harf(oran: float) -> str:
    for h, alt in (("A", 0.85), ("B", 0.70), ("C", 0.50), ("D", 0.30)):
        if oran >= alt:
            return h
    return "E"


def kredibilite(fin) -> Kredibilite:
    """Bankanın kredi değerlendirmesinde baktığı başlıca göstergeler ve bunlardan basit bir A–E not.

    Puanlama genel kabul gören eşiklerle yapılır (her gösterge 0–2 puan); bankaların kendi modelleri ve KKB/çek
    sicili gibi bilgiler dahil değildir. Gelir tablosu kalemleri dönem gün sayısıyla yıllığa çevrilir.
    """
    kr = Kredibilite()
    if fin is None or not fin.has_balance:
        kr.eksik.append("Dönem sonu bakiyeleri (yılbaşından mizan veya defteri kebir)")
    if fin is None or not fin.has_income:
        kr.eksik.append("Gelir ve gider hareketleri (yevmiye, kebir veya mizan)")
    if kr.eksik:
        return kr
    k, aktif = fin.kalemler, fin.aktif or 0
    if fin.flows_window:
        kr.gun = (fin.flows_window[1] - fin.flows_window[0]).days + 1
        kr.yillik_carpan = 365 / kr.gun
    yc = kr.yillik_carpan
    ns = k.get("net_satis", 0)
    fk = k.get("faaliyet_kar", 0)
    amort = k.get("amortisman", 0)
    favok = (fk + amort) * yc
    fin_gid = k.get("finansman_gideri", 0) * yc
    oz = k.get("ozkaynak", 0)
    yab = k.get("yabanci_kaynak", 0)
    G = kr.gostergeler

    # Altman Z'' (üretim dışı / gelişmekte olan piyasa modeli)
    if aktif > 0:
        x1 = (k.get("donen", 0) - k.get("kvyk", 0)) / aktif
        x2 = (k.get("dagitilmamis_kar", 0) + (fin.donem_sonucu_kapanmamis or 0)) / aktif
        x3 = fk * yc / aktif
        x4 = oz / yab if yab else 3.0
        z = 6.56 * x1 + 3.26 * x2 + 6.72 * x3 + 1.05 * x4
        kr.altman = {"z": z, "x1": x1, "x2": x2, "x3": x3, "x4": x4, "x4_yok": not yab}
        p = 2 if z > 2.6 else (1 if z >= 1.1 else 0)
        G.append(KrediGosterge("Altman Z puanı", z, "x", p, "> 2,6 güvenli · 1,1–2,6 gri bölge · < 1,1 riskli",
                               {2: "Mali sıkıntı riski düşük bölgede.", 1: "Gri bölgede: yakından izlenmeli.",
                                0: "Mali sıkıntı riski yüksek bölgede."}[p], (2.6, 8), 8))
    cari = _div(k.get("donen", 0), k.get("kvyk", 0))
    if cari is not None:
        p = 2 if cari >= 1.5 else (1 if cari >= 1 else 0)
        G.append(KrediGosterge("Cari oran", cari, "x", p, "≥ 1,5 iyi · 1–1,5 orta · < 1 zayıf",
                               f"İşletme sermayesi {kurus_str(k.get('donen', 0) - k.get('kvyk', 0))} TL.", (1.5, 2.5), 4))
    if aktif > 0:
        oa = oz / aktif
        p = 2 if oa >= 0.35 else (1 if oa >= 0.2 else 0)
        G.append(KrediGosterge("Özkaynak / aktif", oa, "pct", p, "≥ %35 iyi · %20–35 orta · < %20 zayıf",
                               "Varlıkların ne kadarının ortakların kendi kaynağıyla finanse edildiği; bankalar "
                               "özkaynağı güçlü şirketi daha az riskli görür." if oa >= 0 else
                               "Özkaynak eksi: borçlar varlıkları aşıyor.", (0.35, 1), 1))
    if fin_gid > 0:
        fko = favok / fin_gid
        p = 2 if fko >= 3 else (1 if fko >= 1.5 else 0)
        G.append(KrediGosterge("Faiz karşılama (FAVÖK / finansman gideri)", fko, "x", p, "≥ 3 iyi · 1,5–3 orta · < 1,5 zayıf",
                               f"Faaliyetten elde edilen kâr finansman giderlerinin {_x(fko)} katı.", (3, 10), 10))
    else:
        G.append(KrediGosterge("Faiz karşılama (FAVÖK / finansman gideri)", None, "x", 2, "finansman gideri yok",
                               "Dönemde finansman gideri kaydı yok; faiz yükü bulunmuyor.", None, None))
    net_borc = k.get("mali_borclar", 0) - k.get("hazir_degerler", 0)
    if net_borc <= 0:
        G.append(KrediGosterge("Net borç / FAVÖK", None, "x", 2, "≤ 2,5 iyi · 2,5–4 orta · > 4 zayıf",
                               f"Kasa ve bankadaki para banka borçlarından fazla (net nakit {kurus_str(-net_borc)} TL).", None, None))
    elif favok <= 0:
        G.append(KrediGosterge("Net borç / FAVÖK", None, "x", 0, "≤ 2,5 iyi · 2,5–4 orta · > 4 zayıf",
                               f"Net banka borcu {kurus_str(net_borc)} TL var, faaliyetler ise kâr üretmiyor.", None, None))
    else:
        nbf = net_borc / favok
        p = 2 if nbf <= 2.5 else (1 if nbf <= 4 else 0)
        G.append(KrediGosterge("Net borç / FAVÖK", nbf, "x", p, "≤ 2,5 iyi · 2,5–4 orta · > 4 zayıf",
                               f"Net banka borcu ({kurus_str(net_borc)} TL) yaklaşık {_x(nbf)[:-1]} yıllık faaliyet kârıyla "
                               "(amortisman öncesi) ödenebilir.", (0, 2.5), 6))
    if ns > 0:
        nm = k.get("donem_kari", 0) / ns
        p = 2 if nm >= 0.05 else (1 if nm >= 0 else 0)
        G.append(KrediGosterge("Vergi öncesi kâr marjı", nm, "pct", p, "≥ %5 iyi · %0–5 orta · zarar zayıf",
                               "Satışlardan kalan kâr payı; bankalar sürekli kâr eden şirketi tercih eder.", (0.05, 0.3), 0.3))
        if kr.gun:
            ts = k.get("ticari_alacaklar", 0) / ns * kr.gun
            p = 2 if ts <= 60 else (1 if ts <= 120 else 0)
            G.append(KrediGosterge("Alacak tahsil süresi", ts, "gun", p, "≤ 60 gün iyi · 60–120 orta · > 120 zayıf",
                                   "Satışların nakde dönme hızı; uzun süre işletme sermayesi ihtiyacını artırır.", (0, 60), 180))
    else:
        G.append(KrediGosterge("Vergi öncesi kâr marjı", None, "pct", 0, "satış yok", "Dönemde satış kaydı yok.", None, None))

    kr.puan = sum(g.puan for g in G)
    kr.en_fazla = 2 * len(G)
    kr.harf = _harf(kr.yuzde) if kr.en_fazla else None
    kr.guclu = [g for g in G if g.puan == 2]
    kr.zayif = [g for g in G if g.puan == 0]
    return kr
