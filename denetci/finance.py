"""Finansal özet: Tekdüzen Hesap Planı'na göre bilanço ve gelir tablosu özeti, temel oranlar.

Hesaplama kuralları:
- Bilanço, dönem sonu bakiyelerinden kurulur: dönem sonu bakiyeli mizan veya defteri kebir (nakli yekün dahil).
  Yalnızca bir aralığın hareketlerini içeren (devirsiz) mizandan bilanço kurulmaz.
- Gelir tablosu dönem hareketlerinden kurulur (yevmiye, kebir veya mizan; hangisinin kullanıldığı yazılır).
- 7/A kullanılıp yansıtma yapılmamışsa faaliyet ve finansman giderleri 750-780 hesaplarından alınır.
- Oranlar yalnızca hesaplanır; "iyi/kötü" yorumu yapılmaz.
"""

from dataclasses import dataclass, field

from .checks.kdv import movements
from .parsing import kurus_str
from .checks.rules import Context

GRUPLAR = {
    "10": "Hazır değerler", "11": "Menkul kıymetler", "12": "Ticari alacaklar", "13": "Diğer alacaklar",
    "15": "Stoklar", "17": "Yıllara yaygın inşaat ve onarım maliyetleri", "18": "Gelecek aylara ait giderler ve gelir tahakkukları",
    "19": "Diğer dönen varlıklar",
    "22": "Ticari alacaklar", "23": "Diğer alacaklar", "24": "Mali duran varlıklar", "25": "Maddi duran varlıklar",
    "26": "Maddi olmayan duran varlıklar", "27": "Özel tükenmeye tabi varlıklar",
    "28": "Gelecek yıllara ait giderler ve gelir tahakkukları", "29": "Diğer duran varlıklar",
    "30": "Mali borçlar", "32": "Ticari borçlar", "33": "Diğer borçlar", "34": "Alınan avanslar",
    "35": "Yıllara yaygın inşaat ve onarım hakedişleri", "36": "Ödenecek vergi ve diğer yükümlülükler",
    "37": "Borç ve gider karşılıkları", "38": "Gelecek aylara ait gelirler ve gider tahakkukları", "39": "Diğer kısa vadeli yabancı kaynaklar",
    "40": "Mali borçlar", "42": "Ticari borçlar", "43": "Diğer borçlar", "44": "Alınan avanslar",
    "47": "Borç ve gider karşılıkları", "48": "Gelecek yıllara ait gelirler ve gider tahakkukları",
    "49": "Diğer uzun vadeli yabancı kaynaklar",
    "50": "Ödenmiş sermaye", "52": "Sermaye yedekleri", "54": "Kâr yedekleri", "57": "Geçmiş yıllar kârları",
    "58": "Geçmiş yıllar zararları", "59": "Dönem net kârı (zararı)",
}
SINIFLAR = {"1": "Dönen varlıklar", "2": "Duran varlıklar", "3": "Kısa vadeli yabancı kaynaklar",
            "4": "Uzun vadeli yabancı kaynaklar", "5": "Özkaynaklar"}


@dataclass
class Line:
    label: str
    value: int
    kind: str = "kalem"  # kalem, ara_toplam, toplam
    note: str | None = None


@dataclass
class Ratio:
    key: str
    label: str
    value: float | None
    formula: str
    fmt: str = "x"  # x (kat) veya pct


@dataclass
class Summary:
    balances_source: str | None = None
    flows_source: str | None = None
    flows_window: tuple | None = None
    bilanco: dict = field(default_factory=dict)  # sınıf -> tutar (pozitif)
    bilanco_gruplar: dict = field(default_factory=dict)  # sınıf -> [(grup, ad, tutar)]
    donem_sonucu_kapanmamis: int | None = None
    aktif: int | None = None
    pasif: int | None = None
    gelir_tablosu: list[Line] = field(default_factory=list)
    kalemler: dict = field(default_factory=dict)
    oranlar: list[Ratio] = field(default_factory=list)
    notlar: list[str] = field(default_factory=list)

    @property
    def has_balance(self):
        return self.aktif is not None

    @property
    def has_income(self):
        return bool(self.gelir_tablosu)


def ana_balances(ctx: Context):
    """Ana hesap (3 hane) bazında dönem sonu net bakiyeleri (borç +, alacak −) ve kaynağı."""
    if ctx.mizan and not ctx.mizan_hareket:
        rng = ctx.ranges.get("mizan")
        if not rng or rng[1] == ctx.period_end:
            out = {}
            for ln in ctx.leaves:
                if ln.bakiye_borc is not None or ln.bakiye_alacak is not None:
                    net = (ln.bakiye_borc or 0) - (ln.bakiye_alacak or 0)
                else:
                    net = (ln.acilis_borc or 0) - (ln.acilis_alacak or 0) + ln.borc - ln.alacak
                out[ln.hesap_kodu[:3]] = out.get(ln.hesap_kodu[:3], 0) + net
            return out, "mizan"
    if ctx.kebir:
        out = {}
        for o in ctx.openings.get("kebir", []):
            out[o.hesap_kodu[:3]] = out.get(o.hesap_kodu[:3], 0) + o.borc - o.alacak
        for j in ctx.kebir:
            if j.tarih <= ctx.period_end:
                out[j.hesap_kodu[:3]] = out.get(j.hesap_kodu[:3], 0) + j.borc - j.alacak
        return out, "defteri kebir (nakli yekün dahil)"
    return None, None


def _sum(d: dict, prefixes) -> int:
    return sum(v for k, v in d.items() if k.startswith(prefixes))


def _div(a, b):
    return a / b if b else None


def income_statement(net: dict):
    """Gelir tablosu: net = ana hesap -> tutar (alacak yönü +). (satırlar, kalemler, notlar) döner."""
    notes = []
    cr = lambda p: _sum(net, p)  # noqa: E731
    brut = cr(("600", "601", "602"))
    indirim = -cr(("610", "611", "612"))
    net_satis = brut - indirim
    smm = -cr(("620", "621", "622", "623"))
    # 7/A: 7'li hesaplarda kalan net tutar (ör. 750 borç − 751 alacak) henüz 6'lı hesaplara yansıtılmamış kısımdır.
    # Yansıtma dönemin bir kısmında yapılmışsa yalnızca kalan kısım eklenir; böylece çift sayılmaz.
    fg63, fg7 = -cr(("630", "631", "632")), -cr(("750", "751", "760", "761", "770", "771"))
    fin66, fin7 = -cr(("660", "661")), -cr(("780", "781"))
    hizmet7 = -cr(("740", "741"))
    uretim7 = -cr(("710", "711", "720", "721", "730", "731"))
    fg, fin = fg63 + fg7, fin66 + fin7
    if fg7:
        notes.append(("Faaliyet giderleri 7 sınıfından (750-770) alındı; 63 grubuna yansıtma yapılmamış." if not fg63 else
                      f"Faaliyet giderlerinin {kurus_str(fg7)} TL'si 63 grubuna yansıtılmamış (750-770'te kalmış); "
                      "kâra bu tutar da gider olarak eklendi."))
    if fin7:
        notes.append(("Finansman giderleri 780 hesabından alındı; 660'a yansıtma yapılmamış." if not fin66 else
                      f"Finansman giderlerinin {kurus_str(fin7)} TL'si 660'a yansıtılmamış (780'de kalmış); kâra eklendi."))
    if hizmet7:
        smm += hizmet7
        notes.append(f"Hizmet üretim maliyetinin {kurus_str(hizmet7)} TL'si 622'ye yansıtılmamış (740'ta kalmış); "
                     "satışların maliyetine eklendi.")
    if uretim7:
        notes.append(f"Üretim maliyet hesaplarında (710-730) {kurus_str(uretim7)} TL yansıtılmamış tutar var. Bu tutar "
                     "stoklara (151-152) ve satılan mamul maliyetine (620) aktarılmadığı için kâra dahil edilmedi; "
                     "kâr olduğundan yüksek görünebilir.")
    diger_gelir = cr(("64",))
    diger_gider = -cr(("65",))
    olagandisi = cr(("67",)) + cr(("68",))
    brut_kar = net_satis - smm
    faaliyet_kar = brut_kar - fg
    olagan = faaliyet_kar + diger_gelir - diger_gider - fin
    donem = olagan + olagandisi
    if any(net.get(k) for k in ("690", "692")):
        notes.append("690/692 kapanış kayıtları var; gelir tablosu kapanış kayıtları nedeniyle eksik görünebilir.")
    lines = [
        Line("Brüt satışlar", brut), Line("Satış indirimleri", -indirim),
        Line("Net satışlar", net_satis, "ara_toplam"), Line("Satışların maliyeti", -smm),
        Line("Brüt satış kârı", brut_kar, "ara_toplam"), Line("Faaliyet giderleri", -fg),
        Line("Faaliyet kârı", faaliyet_kar, "ara_toplam"),
        Line("Diğer faaliyet gelirleri", diger_gelir), Line("Diğer faaliyet giderleri", -diger_gider),
        Line("Finansman giderleri", -fin), Line("Olağan kâr", olagan, "ara_toplam"),
        Line("Olağandışı gelir ve giderler (net)", olagandisi), Line("Dönem kârı (vergi öncesi)", donem, "toplam"),
    ]
    if smm == 0 and net_satis > 0:
        notes.append("Satışların maliyeti kaydı yok; brüt kâr satışların tamamı gibi görünür "
                     "(maliyet kaydı dönem sonunda yapılıyor olabilir).")
    return lines, {"net_satis": net_satis, "brut_kar": brut_kar, "faaliyet_kar": faaliyet_kar, "donem_kari": donem}, notes


def summarize(ctx: Context) -> Summary:
    s = Summary()
    bal, src = ana_balances(ctx)
    if bal is not None:
        s.balances_source = src
        donen, duran = _sum(bal, "1"), _sum(bal, "2")
        kvyk, uvyk, ozk = -_sum(bal, "3"), -_sum(bal, "4"), -_sum(bal, "5")
        sonuc = -_sum(bal, ("6", "7"))
        s.bilanco = {"1": donen, "2": duran, "3": kvyk, "4": uvyk, "5": ozk}
        s.donem_sonucu_kapanmamis = sonuc
        s.aktif = donen + duran
        s.pasif = kvyk + uvyk + ozk + sonuc
        for sinif in SINIFLAR:
            sign = 1 if sinif in "12" else -1
            groups = sorted({k[:2] for k in bal if k.startswith(sinif)})
            s.bilanco_gruplar[sinif] = [(g, GRUPLAR.get(g, f"{g} grubu"), sign * _sum(bal, g)) for g in groups
                                        if abs(_sum(bal, g)) >= 100]
        s.kalemler.update({"hazir_degerler": _sum(bal, "10"), "stoklar": _sum(bal, "15"),
                           "ticari_alacaklar": _sum(bal, ("12", "22")), "ticari_borclar": -_sum(bal, ("32", "42")),
                           "mali_borclar": -_sum(bal, ("30", "40")), "donen": donen, "kvyk": kvyk,
                           "yabanci_kaynak": kvyk + uvyk, "ozkaynak": ozk + sonuc})
        if abs(s.aktif - s.pasif) > 100:
            s.notlar.append(f"Aktif ile pasif arasında {_tl(s.aktif - s.pasif)} TL fark var; nazım hesaplar dışında "
                            "eksik hesap olabilir.")
        if ctx.mizan_hareket and src.startswith("defteri kebir"):
            s.notlar.append("Mizan devir içermediği için bilanço defteri kebirden (nakli yekün dahil) kuruldu.")
    elif ctx.mizan_hareket:
        s.notlar.append("Bilanço kurulamadı: mizan yalnızca tarih aralığının hareketlerini içeriyor. "
                        "Yılbaşından alınmış mizan veya defteri kebir yükleyin.")

    m = movements(ctx)
    if m is not None:
        s.flows_source, s.flows_window = m.source, m.window
        net = {}
        for k in set(m.borc) | set(m.alacak):
            net[k[:3]] = net.get(k[:3], 0) + m.alacak.get(k, 0) - m.borc.get(k, 0)  # alacak (gelir) yönü +
        lines, kal, notes = income_statement(net)
        s.gelir_tablosu = lines
        s.kalemler.update(kal)
        s.notlar += notes

    k = s.kalemler
    if s.has_balance:
        s.oranlar += [
            Ratio("cari", "Cari oran", _div(k["donen"], k["kvyk"]), "Dönen varlıklar / Kısa vadeli yabancı kaynaklar"),
            Ratio("asit", "Asit-test oranı", _div(k["donen"] - k["stoklar"], k["kvyk"]),
                  "(Dönen varlıklar − Stoklar) / Kısa vadeli yabancı kaynaklar"),
            Ratio("nakit", "Nakit oranı", _div(k["hazir_degerler"], k["kvyk"]), "Hazır değerler / Kısa vadeli yabancı kaynaklar"),
            Ratio("kaldirac", "Kaldıraç oranı", _div(k["yabanci_kaynak"], s.aktif), "Yabancı kaynaklar / Aktif toplamı", "pct"),
            Ratio("borc_ozk", "Borç / özkaynak", _div(k["yabanci_kaynak"], k["ozkaynak"]),
                  "Yabancı kaynaklar / (Özkaynaklar + kapanmamış dönem sonucu)"),
        ]
    if s.has_income and k.get("net_satis"):
        s.oranlar += [
            Ratio("brut_marj", "Brüt kâr marjı", _div(k["brut_kar"], k["net_satis"]), "Brüt satış kârı / Net satışlar", "pct"),
            Ratio("faaliyet_marj", "Faaliyet kâr marjı", _div(k["faaliyet_kar"], k["net_satis"]), "Faaliyet kârı / Net satışlar", "pct"),
            Ratio("net_marj", "Vergi öncesi kâr marjı", _div(k["donem_kari"], k["net_satis"]),
                  "Dönem kârı (vergi öncesi) / Net satışlar", "pct"),
        ]
    return s


def _tl(kurus: int) -> str:
    from .parsing import kurus_str
    return kurus_str(kurus)
