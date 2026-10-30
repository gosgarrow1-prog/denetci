"""Mükellef panelindeki yönlendirici asistan: ne yapılmak istendiğini sorar, döneme göre gereken belgeleri listeler.

Kurallarla çalışır (yapay zekâ kullanmaz). Belgelerin yüklü olup olmadığı analizlerin gereksinim listesinden alınır.
"""

from dataclasses import dataclass
from datetime import date, timedelta

AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]


@dataclass
class Gorev:
    key: str
    baslik: str
    aciklama: str
    donem: str  # ay, ceyrek, gv, ay_ceyrek
    analizler: tuple  # analyses.build anahtarları
    link: str  # analiz sayfası (mukellef/{id}/ altında)
    soru: str


GOREVLER = [
    Gorev("kdv", "Aylık KDV kontrolü", "Beyan öncesi 191/391 mahsubu, devreden KDV ve oran kontrolü",
          "ay", ("kdv",), "kdv", "Hangi ayın KDV'sini kontrol edelim?"),
    Gorev("gecici", "Geçici vergi hesaplaması", "Yılbaşından kâr, tahmini matrah ve ödenecek vergi",
          "gv", ("gecici",), "gecici-vergi", "Hangi geçici vergi dönemi?"),
    Gorev("finansal", "Finansal analiz", "Gelir tablosu, bilanço, oranlar ve öneriler",
          "ay_ceyrek", ("finansal",), "finansal", "Hangi dönemi analiz edelim?"),
    Gorev("mutabakat", "Defter kontrolü", "Fiş denkliği; yevmiye, kebir, muavin ve mizan uyumu",
          "ay_ceyrek", ("mutabakat",), "mutabakat", "Hangi dönemin defterlerini kontrol edelim?"),
    Gorev("kapanis", "Dönem kapanışı ve rapor", "Tüm kontroller, öneriler ve yazdırılabilir rapor",
          "ay_ceyrek", ("kdv", "mutabakat", "finansal", "gecici"), "rapor", "Hangi dönemi kapatıyoruz?"),
]
GOREV = {g.key: g for g in GOREVLER}


def _ay_sonu(y: int, m: int) -> date:
    return date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)


def secenekler(gorev: Gorev, bugun: date) -> list[tuple[str, list[tuple[date, date, str]]]]:
    """Dönem seçenekleri: [(grup adı, [(başlangıç, bitiş, ad)])], en yeniden eskiye."""
    out = []
    if gorev.donem in ("ay", "ay_ceyrek"):
        aylar = []
        y, m = bugun.year, bugun.month
        for _ in range(12):
            aylar.append((date(y, m, 1), _ay_sonu(y, m), f"{AYLAR[m - 1]} {y}"))
            y, m = (y - 1, 12) if m == 1 else (y, m - 1)
        out.append(("Aylar", aylar))
    if gorev.donem in ("ceyrek", "ay_ceyrek"):
        q = []
        for y in (bugun.year, bugun.year - 1):
            for n in range(4, 0, -1):
                if date(y, 3 * n - 2, 1) <= bugun:
                    q.append((date(y, 3 * n - 2, 1), _ay_sonu(y, 3 * n), f"{n}. çeyrek {y} ({AYLAR[3 * n - 3]}–{AYLAR[3 * n - 1]})"))
        out.append(("Çeyrekler", q))
    if gorev.donem == "gv":
        g = []
        for y in (bugun.year, bugun.year - 1):
            for n in range(3, 0, -1):
                if date(y, 3 * n - 2, 1) <= bugun:
                    g.append((date(y, 1, 1), _ay_sonu(y, 3 * n), f"{y} / {n}. dönem (Ocak–{AYLAR[3 * n - 1]})"))
        out.append(("Geçici vergi dönemleri", g))
    return out


def uygun(gorev: Gorev, p) -> bool:
    """Seçili dönem bu görev için uygun mu (ör. geçici vergi için yılbaşından çeyrek sonu)."""
    if p is None:
        return False
    tek_ay = p.baslangic.day == 1 and (p.baslangic.year, p.baslangic.month) == (p.bitis.year, p.bitis.month)
    if gorev.donem == "ay":
        return tek_ay
    if gorev.donem == "gv":
        return p.baslangic == date(p.bitis.year, 1, 1) and p.bitis.month in (3, 6, 9) and p.bitis == _ay_sonu(p.bitis.year, p.bitis.month)
    return True


def nasil(gorev: Gorev, p) -> list[str]:
    """Belgelerin Luca'dan ve GİB'den nasıl alınacağına dair kısa yönergeler."""
    aralik = f"{p.baslangic:%d.%m.%Y} – {p.bitis:%d.%m.%Y}"
    ytd = f"01.01.{p.bitis.year} – {p.bitis:%d.%m.%Y}"
    onceki = p.baslangic - timedelta(days=1)
    out = []
    if gorev.key == "kdv":
        out = [f"Luca'dan yevmiye defterini ve mizanı tarih aralığı {aralik} olacak şekilde Excel (.xlsx) olarak alın.",
               f"Yılbaşından yevmiye ({ytd} veya daha geniş) zaten yüklüyse bu ay ondan ayrılır; ayrıca yüklemeniz gerekmez.",
               f"{AYLAR[onceki.month - 1]} {onceki.year} KDV beyannamesini GİB'den PDF olarak indirip yükleyin; devreden KDV oradan okunur.",
               "Bu ayın beyannamesi verildiyse onun PDF'ini de yükleyin; beyan defterle karşılaştırılır."]
    elif gorev.key == "gecici":
        out = [f"Luca'dan mizan veya yevmiyeyi yılbaşından, tarih aralığı {ytd} olacak şekilde Excel olarak alın.",
               "Bir önceki geçici vergi beyannamesini GİB'den PDF olarak yükleyin; mahsup edilecek vergi oradan okunur.",
               "KKEG, istisna ve geçmiş yıl zararı tutarlarını Geçici Vergi sayfasında girin. Satış maliyeti kaydı yapılmadıysa "
               "sayfa brüt kâr oranıyla tahmin eder."]
    elif gorev.key == "finansal":
        out = [f"Gelir tablosu için dönemin yevmiyesini ({aralik}) Excel olarak alın.",
               f"Bilanço için yılbaşından mizan veya yevmiye ({ytd}) yükleyin; yalnızca dönemin hareketlerini içeren mizan bakiye vermez.",
               "Karşılaştırma için önceki dönemin dosyalarını da yükleyebilirsiniz."]
    elif gorev.key == "mutabakat":
        out = [f"Yevmiye, defteri kebir, muavin ve mizanı aynı tarih aralığıyla ({aralik}) ve aynı gün Luca'dan alın.",
               "Kebir ve muavin isteğe bağlıdır; yüklenenler yevmiyeyle karşılaştırılır."]
    elif gorev.key == "kapanis":
        out = [f"Dönemin yevmiyesini ({aralik}) ve yılbaşından mizanı ({ytd}) Luca'dan Excel olarak alın.",
               "KDV ve geçici vergi beyannamelerini GİB'den PDF olarak yükleyin.",
               "Kontrolleri inceleyip notunuzu yazdıktan sonra dönemi panelden kesinleştirin ve raporu yazdırın."]
    return out
