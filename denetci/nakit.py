"""Nakit akım tablosu (dolaylı yöntem): dönem başı ve dönem sonu bakiyelerinin farkından.

Çift taraflı kayıtta tüm hesapların toplamı sıfır olduğundan, hazır değerler (10) dışındaki her hesabın bakiye
değişiminin tersi nakde etkisidir. Hesaplar Tekdüzen gruplarına göre faaliyet, yatırım ve finansman olarak sınıflanır:

- Faaliyet: dönem kârı (6–7 sınıfı), amortisman (257, 268), işletme sermayesi kalemleri (alacaklar, stoklar,
  ticari ve diğer borçlar, vergi yükümlülükleri).
- Yatırım: menkul kıymetler (11) ve duran varlıklar (24–29; birikmiş amortisman hariç).
- Finansman: banka kredileri ve mali borçlar (30, 40), ortaklara borçlar (331, 431), özkaynak hareketleri (5).

Dönem başı bakiyeleri: yılbaşından yevmiye (açılış fişi dahil), dönem başlangıcındaki defteri kebir devri veya
dönem başından alınmış mizanın açılış sütunları. Tutarlar kuruştur.
"""

from dataclasses import dataclass, field
from datetime import timedelta

from .checks.rules import Context
from .finance import ana_balances


def _acilis(j) -> bool:
    from .parsing import fold
    return fold(j.fis_tipi or "").startswith("acilis") or fold(j.aciklama or "").startswith("acilis")


def baslangic_bakiyeleri(ctx: Context):
    """Dönem başındaki ana hesap bakiyeleri (borç +) ve kaynağı; bulunamazsa (None, None)."""
    bas = ctx.period_start
    if ctx.ytd:
        out = {}
        for j in ctx.ytd:
            if j.tarih < bas or (j.tarih == bas and _acilis(j)):
                out[j.hesap_kodu[:3]] = out.get(j.hesap_kodu[:3], 0) + j.borc - j.alacak
        return out, "yılbaşından yevmiye"
    rng = ctx.ranges.get("kebir")
    if ctx.kebir and (not rng or rng[0] <= bas):
        out = {}
        for o in ctx.openings.get("kebir", []):
            out[o.hesap_kodu[:3]] = out.get(o.hesap_kodu[:3], 0) + o.borc - o.alacak
        for j in ctx.kebir:
            if j.tarih < bas or (j.tarih == bas and _acilis(j)):
                out[j.hesap_kodu[:3]] = out.get(j.hesap_kodu[:3], 0) + j.borc - j.alacak
        return out, "defteri kebir devri"
    rng = ctx.ranges.get("mizan")
    if ctx.mizan and not ctx.mizan_hareket and rng and rng[0] == bas:
        out, var = {}, False
        for ln in ctx.leaves:
            if ln.acilis_borc is not None or ln.acilis_alacak is not None:
                var = True
            out[ln.hesap_kodu[:3]] = out.get(ln.hesap_kodu[:3], 0) + (ln.acilis_borc or 0) - (ln.acilis_alacak or 0)
        if var:
            return out, "mizanın açılış sütunları"
    return None, None


GRUPLAR = [
    # (bölüm, satır adı, eşleşme fonksiyonu)
    ("faaliyet", "Dönem kârı (vergi öncesi)", lambda k: k[0] in "67"),
    ("faaliyet", "Amortisman (nakit çıkışı olmayan gider)", lambda k: k in ("257", "268")),
    ("faaliyet", "Ticari alacaklardaki değişim", lambda k: k[:2] in ("12", "22")),
    ("faaliyet", "Stoklardaki değişim", lambda k: k[:2] == "15"),
    ("faaliyet", "Diğer alacak ve dönen varlıklardaki değişim", lambda k: k[:2] in ("13", "17", "18", "19", "23")),
    ("faaliyet", "Ticari borçlardaki değişim", lambda k: k[:2] in ("32", "42")),
    ("faaliyet", "Vergi, SGK ve karşılıklardaki değişim", lambda k: k[:2] in ("36", "37", "47")),
    ("faaliyet", "Alınan avanslar ve diğer borçlardaki değişim",
     lambda k: k[:2] in ("33", "34", "35", "38", "39", "43", "44", "48", "49") and k not in ("331", "431")),
    ("yatirim", "Menkul kıymetler", lambda k: k[:2] == "11"),
    ("yatirim", "Maddi ve maddi olmayan duran varlık alımları (satışlar düşülmüş)",
     lambda k: k[:2] in ("25", "26", "27", "28", "29") and k not in ("257", "268")),
    ("yatirim", "Mali duran varlıklar (iştirak, bağlı ortaklık)", lambda k: k[:2] == "24"),
    ("finansman", "Banka kredileri ve diğer mali borçlar", lambda k: k[:2] in ("30", "40")),
    ("finansman", "Ortaklardan sağlanan / ortaklara ödenen", lambda k: k in ("331", "431")),
    ("finansman", "Sermaye ve diğer özkaynak hareketleri (kâr dağıtımı dahil)", lambda k: k[0] == "5"),
]
BOLUMLER = {"faaliyet": "Faaliyetlerden sağlanan nakit", "yatirim": "Yatırım faaliyetlerinde kullanılan nakit",
            "finansman": "Finansman faaliyetlerinden sağlanan nakit"}


@dataclass
class NakitAkim:
    bas_kaynak: str
    son_kaynak: str
    satirlar: dict = field(default_factory=dict)  # bölüm -> [(ad, tutar)]
    toplam: dict = field(default_factory=dict)
    nakit_bas: int = 0
    nakit_son: int = 0
    siniflanmayan: int = 0

    @property
    def degisim(self):
        return sum(self.toplam.values())

    @property
    def fark(self):
        """Hesaplanan değişim ile hazır değerlerdeki gerçek değişim arasındaki fark (nazım hesaplar vb.)."""
        return (self.nakit_son - self.nakit_bas) - self.degisim


def hesapla(ctx: Context) -> NakitAkim | None:
    son, son_k = ana_balances(ctx)
    if son is None:
        return None
    bas, bas_k = baslangic_bakiyeleri(ctx)
    if bas is None:
        return None
    na = NakitAkim(bas_k, son_k)
    na.nakit_bas = sum(v for k, v in bas.items() if k[:2] == "10")
    na.nakit_son = sum(v for k, v in son.items() if k[:2] == "10")
    etki = {}
    for k in set(bas) | set(son):
        if k[:2] == "10" or k[0] in "89" or len(k) < 3:
            continue
        etki[k] = -(son.get(k, 0) - bas.get(k, 0))
    kullanilan = set()
    for bolum, ad, f in GRUPLAR:
        t = sum(v for k, v in etki.items() if k not in kullanilan and f(k))
        kullanilan |= {k for k in etki if f(k)}
        na.satirlar.setdefault(bolum, []).append((ad, t))
    na.siniflanmayan = sum(v for k, v in etki.items() if k not in kullanilan)
    if na.siniflanmayan:
        na.satirlar["faaliyet"].append(("Diğer kalemler", na.siniflanmayan))
    na.toplam = {b: sum(t for _, t in rows) for b, rows in na.satirlar.items()}
    return na


def onceki_gun(ctx: Context):
    return ctx.period_start - timedelta(days=1)
