"""Benford analizi: kayıt tutarlarının ilk rakam dağılımı.

Doğal oluşan tutarlarda ilk rakamın 1 olma olasılığı yaklaşık %30, 9 olma olasılığı %5'tir (Benford yasası).
Elle uydurulmuş veya belirli bir sınırın altında tutulmaya çalışılmış tutarlar bu dağılımdan sapar. Sapma tek başına
hata ya da hile göstermez; bakılacak kayıtları öne çıkarır. Uygunluk Nigrini'nin ortalama mutlak sapma (MAD) eşikleriyle
ve ki-kare testiyle değerlendirilir.
"""

import math
from collections import Counter
from dataclasses import dataclass, field

BEKLENEN = {d: math.log10(1 + 1 / d) for d in range(1, 10)}
EN_AZ_KAYIT = 300  # bundan az kayıtta sonuç güvenilir değildir
ALT_SINIR = 10_00  # 10 TL altı tutarlar alınmaz (kuruş)
KI_KARE_KRITIK = 15.51  # 8 serbestlik derecesi, %5


@dataclass
class Sonuc:
    n: int
    gozlenen: dict  # rakam -> oran
    adet: dict
    mad: float
    ki_kare: float
    uygunluk: str  # yakin, kabul, sinirda, uygun_degil
    fazla: list = field(default_factory=list)  # beklenenden belirgin fazla çıkan rakamlar
    tekrar: list = field(default_factory=list)  # (tutar, adet) en çok tekrarlanan tutarlar
    yuvarlak: int = 0  # 1.000 TL'nin katı olan tutar sayısı

    @property
    def yeterli(self):
        return self.n >= EN_AZ_KAYIT


UYGUNLUK_ADI = {"yakin": "Benford'a çok yakın", "kabul": "Kabul edilebilir uyum", "sinirda": "Sınırda uyum",
                "uygun_degil": "Benford'a uymuyor"}


def ilk_rakam(kurus: int) -> int:
    return int(str(abs(kurus))[0])


def analiz(tutarlar) -> Sonuc | None:
    """tutarlar: kuruş cinsinden pozitif tutarlar."""
    t = [abs(x) for x in tutarlar if abs(x) >= ALT_SINIR]
    if not t:
        return None
    n = len(t)
    adet = Counter(ilk_rakam(x) for x in t)
    goz = {d: adet.get(d, 0) / n for d in range(1, 10)}
    mad = sum(abs(goz[d] - BEKLENEN[d]) for d in range(1, 10)) / 9
    ki = sum((adet.get(d, 0) - n * BEKLENEN[d]) ** 2 / (n * BEKLENEN[d]) for d in range(1, 10))
    uyg = "yakin" if mad < 0.006 else ("kabul" if mad < 0.012 else ("sinirda" if mad < 0.015 else "uygun_degil"))
    # Beklenenden belirgin fazla: oran farkı 2 puandan büyük ve z > 1,96
    fazla = []
    for d in range(1, 10):
        p = BEKLENEN[d]
        z = (abs(goz[d] - p) - 1 / (2 * n)) / math.sqrt(p * (1 - p) / n)
        if goz[d] - p > 0.02 and z > 1.96:
            fazla.append(d)
    tekrar = [(v, c) for v, c in Counter(t).most_common(10) if c >= 3]
    yuvarlak = sum(1 for x in t if x % 100_000 == 0)
    return Sonuc(n, goz, dict(adet), mad, ki, uyg, fazla, tekrar[:8], yuvarlak)
