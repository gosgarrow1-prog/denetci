"""Çeyrek analizi: seçilen 3 aylık dönemin kendi içinde incelenmesi (geçici vergiden ayrı, kümülatif değil).

- Gelir tablosu: çeyreğin 3 ayı ayrı ayrı, çeyrek toplamı, önceki çeyrek ve geçen yılın aynı çeyreği.
- Oranlar: brüt kâr, faaliyet gideri, finansman gideri, net kâr / net satış.
- Düzenli giderlerde boşluk: diğer iki ayda kaydı olan bir gider hesabının bir ayda hiç kaydı yoksa işaretlenir.
- Bilanço hareketleri: çeyrek başı ve sonu bakiyeleri (yılbaşından başlayan yevmiye gerekir).
- KDV: aylara göre hesaplanan (391) ve indirilecek (191) KDV.

Veriler yüklenmiş onaylı yevmiyelerden alınır; her ay için o ayı kapsayan en son yevmiye kullanılır.
Uyarılar "olası neden" niteliğindedir; kesin hata olarak sunulmaz.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

from .checks.engine import _yevmiye_lines, kapsayan_yevmiye
from .finance import income_statement
from .parsing import fold, kurus_str

AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
GIDER_ONEKLERI = ("63", "65", "66", "68", "75", "76", "77", "78")
BILANCO_KALEMLERI = [
    ("Kasa ve banka", ("100", "101", "102", "103", "108")),
    ("Ticari alacaklar", ("120", "121", "126", "127", "128", "129")),
    ("Stoklar", ("150", "151", "152", "153", "157", "159")),
    ("Ticari borçlar", ("320", "321", "326", "329")),
    ("Banka kredileri", ("300", "303", "304", "400")),
    ("Ödenecek vergi ve SGK", ("360", "361", "368", "369")),
]
PASIF = ("3", "4", "5")


def ay_sonu(y, m):
    return (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))


def ceyrek_of(d: date) -> tuple[date, date]:
    m = (d.month - 1) // 3 * 3 + 1
    return date(d.year, m, 1), ay_sonu(d.year, m + 2)


def ceyrek_adi(bas: date) -> str:
    return f"{AYLAR[bas.month - 1]}–{AYLAR[bas.month + 1]} {bas.year}"


def _acilis(j) -> bool:
    return fold(j.fis_tipi or "").startswith("acilis") or fold(j.aciklama or "").startswith("acilis")


def _ay_satirlari(session, client, y, m):
    """O ayı kapsayan en son onaylı yevmiyeden o ayın kayıtları; yoksa None."""
    bas, bit = date(y, m, 1), ay_sonu(y, m)
    b = kapsayan_yevmiye(session, client, bas, bit)
    if b is None:
        return None
    return [j for j in _yevmiye_lines(session, client, b) if bas <= j.tarih <= bit]


@dataclass
class Kolon:
    ad: str
    bas: date
    bit: date
    var: bool  # veri var mı
    satirlar: list = field(default_factory=list)
    kal: dict = field(default_factory=dict)
    hesaplar: dict = field(default_factory=dict)  # tam hesap kodu -> (ad, net gider)
    hes_kdv: int = 0
    ind_kdv: int = 0

    def oran(self, key):
        ns = self.kal.get("net_satis") or 0
        if ns <= 0:
            return None
        if key == "brut":
            return self.kal["brut_kar"] / ns
        if key == "faaliyet_gider":
            return (self.kal["brut_kar"] - self.kal["faaliyet_kar"]) / ns
        if key == "net":
            return self.kal["donem_kari"] / ns
        return None

    @property
    def smm(self):
        return (self.kal.get("net_satis") or 0) - (self.kal.get("brut_kar") or 0)


def _kolon(ad, bas, bit, lines) -> Kolon:
    k = Kolon(ad, bas, bit, lines is not None)
    if lines is None:
        return k
    net = {}
    for j in lines:
        if _acilis(j):
            continue
        c = j.hesap_kodu
        if c[:1] in ("6", "7"):
            net[c[:3]] = net.get(c[:3], 0) + j.alacak - j.borc
        if c.startswith(GIDER_ONEKLERI):
            ad_, v = k.hesaplar.get(c, (j.hesap_adi, 0))
            k.hesaplar[c] = (ad_ or j.hesap_adi, v + j.borc - j.alacak)
        if c.startswith("391"):
            k.hes_kdv += j.alacak - j.borc
        elif c.startswith("191"):
            k.ind_kdv += j.borc - j.alacak
    k.satirlar, k.kal, _ = income_statement(net)
    return k


def _toplam(ad, kolonlar) -> Kolon:
    var = [k for k in kolonlar if k.var]
    t = Kolon(ad, kolonlar[0].bas, kolonlar[-1].bit, len(var) == len(kolonlar))
    if not var:
        return t
    t.satirlar = []
    for i, ln in enumerate(var[0].satirlar):
        t.satirlar.append(type(ln)(ln.label, sum(k.satirlar[i].value for k in var), ln.kind))
    t.kal = {key: sum(k.kal[key] for k in var) for key in var[0].kal}
    for k in var:
        for c, (a, v) in k.hesaplar.items():
            a0, v0 = t.hesaplar.get(c, (a, 0))
            t.hesaplar[c] = (a0, v0 + v)
    t.hes_kdv, t.ind_kdv = sum(k.hes_kdv for k in var), sum(k.ind_kdv for k in var)
    return t


def _ceyrek_kolonu(session, client, bas, ad) -> Kolon:
    aylar = [_kolon(AYLAR[bas.month - 1 + i], date(bas.year, bas.month + i, 1), ay_sonu(bas.year, bas.month + i),
                    _ay_satirlari(session, client, bas.year, bas.month + i)) for i in range(3)]
    return _toplam(ad, aylar)


@dataclass
class Uyari:
    seviye: str  # onemli, dikkat, bilgi
    metin: str
    neden: str


@dataclass
class Bilanco:
    ad: str
    bas: int
    bit: int

    @property
    def degisim(self):
        return self.bit - self.bas


@dataclass
class Analiz:
    ad: str
    bas: date
    bit: date
    aylar: list
    toplam: Kolon
    onceki: Kolon
    gecen_yil: Kolon
    uyarilar: list = field(default_factory=list)
    bilanco: list = field(default_factory=list)
    bilanco_kaynak: str | None = None
    eksik_aylar: list = field(default_factory=list)


def _pct(x):
    return f"%{x * 100:.1f}".replace(".", ",")


def _degisim(a, b):
    return (a - b) / abs(b) if b else None


def analiz(session, client, ref: date) -> Analiz | None:
    bas, bit = ceyrek_of(ref)
    aylar = [_kolon(AYLAR[bas.month - 1 + i], date(bas.year, bas.month + i, 1), ay_sonu(bas.year, bas.month + i),
                    _ay_satirlari(session, client, bas.year, bas.month + i)) for i in range(3)]
    if not any(k.var for k in aylar):
        return None
    toplam = _toplam("Çeyrek toplamı", aylar)
    ob = ceyrek_of(bas - timedelta(days=1))[0]
    onceki = _ceyrek_kolonu(session, client, ob, ceyrek_adi(ob))
    gb = date(bas.year - 1, bas.month, 1)
    gecen = _ceyrek_kolonu(session, client, gb, ceyrek_adi(gb))
    a = Analiz(ceyrek_adi(bas), bas, bit, aylar, toplam, onceki, gecen)
    a.eksik_aylar = [k.ad for k in aylar if not k.var]
    a.uyarilar = _uyarilar(a)
    a.bilanco, a.bilanco_kaynak = _bilanco(session, client, bas, bit)
    return a


def _uyarilar(a: Analiz) -> list:
    out = []
    var = [k for k in a.aylar if k.var]
    if a.eksik_aylar:
        out.append(Uyari("onemli", f"{', '.join(a.eksik_aylar)} için yevmiye yüklenmemiş; çeyrek toplamı eksik.",
                         "O ayların Luca yevmiyesini yükleyin."))
    satisli = [k for k in var if (k.kal.get("net_satis") or 0) > 0]
    # Maliyet kaydı olmayan ay
    maliyetli = [k for k in satisli if k.smm > 0]
    if maliyetli:
        for k in satisli:
            if k.smm <= 0:
                out.append(Uyari("onemli", f"{k.ad} ayında {kurus_str(k.kal['net_satis'])} TL satış var, satışların maliyeti kaydı yok.",
                                 "Maliyet kaydı ay sonunda girilmemiş ya da çeyrek sonunda toplu yapılacak olabilir. "
                                 "Brüt kâr oranı bu ayda %100 görünür."))
    elif satisli and not any(k.smm > 0 for k in satisli):
        out.append(Uyari("dikkat", "Çeyrekte satış var ama hiç satışların maliyeti kaydı yok.",
                         "Maliyet yıl sonunda hesaplanıyor olabilir; hizmet işletmelerinde 622 kullanılır. Kâr olduğundan yüksek görünür."))
    # Satış olmayan ay
    if satisli and len(satisli) < len(var):
        for k in var:
            if k not in satisli:
                out.append(Uyari("dikkat", f"{k.ad} ayında satış kaydı yok.",
                                 "Satış faturaları sonraki aya kaydedilmiş olabilir; e-Fatura listesiyle karşılaştırın."))
    # Düzenli giderde boşluk: diğer iki ayda kaydı olan hesap bu ayda yok
    if len(var) == 3:
        bos = []
        for c, (ad, top) in a.toplam.hesaplar.items():
            aylik = [k.hesaplar.get(c, (None, 0))[1] for k in var]
            dolu = [v for v in aylik if v > 0]
            if len(dolu) == 2 and top > 0:
                ay = var[[i for i, v in enumerate(aylik) if v <= 0][0]].ad
                bos.append((sum(dolu) / 2, c, ad, ay))
        bos.sort(reverse=True)
        for ort, c, ad, ay in bos[:6]:
            out.append(Uyari("dikkat", f"{c} {ad or ''}: {ay} ayında kayıt yok (diğer aylarda ortalama {kurus_str(round(ort))} TL).",
                             "Kira, personel, amortisman gibi düzenli bir gider bu ay girilmemiş ya da başka aya kaydedilmiş olabilir."))
    # Önceki çeyrekle karşılaştırma
    t, o = a.toplam, a.onceki
    if t.var and o.var:
        tb, ob = t.oran("brut"), o.oran("brut")
        if tb is not None and ob is not None and abs(tb - ob) >= 0.10:
            out.append(Uyari("dikkat", f"Brüt kâr oranı önceki çeyrekte {_pct(ob)}, bu çeyrekte {_pct(tb)}.",
                             "Maliyet kaydı eksik veya fazla, stok sayım farkı, fiyat değişimi ya da satış karmasının değişmesi olabilir."))
        d = _degisim(t.kal.get("net_satis") or 0, o.kal.get("net_satis") or 0)
        if d is not None and abs(d) >= 0.30:
            out.append(Uyari("bilgi", f"Net satışlar önceki çeyreğe göre {_pct(d)} {'arttı' if d > 0 else 'azaldı'}.",
                             "Mevsimsellik, büyük bir sipariş ya da faturaların dönemler arasında kayması olabilir."))
        tg, og = t.oran("faaliyet_gider"), o.oran("faaliyet_gider")
        if tg is not None and og is not None and abs(tg - og) >= 0.05:
            out.append(Uyari("bilgi", f"Faaliyet giderlerinin satışa oranı önceki çeyrekte {_pct(og)}, bu çeyrekte {_pct(tg)}.",
                             "Gider tahakkuku, yansıtma (7/A) kaydı veya tek seferlik bir gider olabilir."))
    if not out:
        out.append(Uyari("bilgi", "Belirgin bir tutarsızlık görülmedi.", ""))
    return out


def _bilanco(session, client, bas, bit):
    b = kapsayan_yevmiye(session, client, date(bit.year, 1, 1), bit, yilbasindan=True)
    if b is None:
        return [], None
    lines = [j for j in _yevmiye_lines(session, client, b) if j.tarih <= bit]
    out = []
    for ad, kodlar in BILANCO_KALEMLERI:
        isaret = -1 if kodlar[0][:1] in PASIF else 1
        once = sum(j.borc - j.alacak for j in lines if j.hesap_kodu.startswith(kodlar) and (j.tarih < bas or _acilis(j)))
        sonra = sum(j.borc - j.alacak for j in lines if j.hesap_kodu.startswith(kodlar))
        out.append(Bilanco(ad, isaret * once, isaret * sonra))
    return out, "yılbaşından yevmiye (açılış fişi dahil)"
