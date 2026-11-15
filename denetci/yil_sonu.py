"""Yıl sonu (dönem sonu) işlem listesi.

Defterden anlaşılabilen işlemler son analizin kural sonuçlarına bağlanır; anlaşılamayanlar elle "yapıldı" işaretlenir.
Mevzuat ayrıntıları için madde numarası verilmez: her büro kendi mükellefine göre doğrulamalıdır.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Is:
    key: str  # en çok 12 karakter ("ys-" ön ekiyle saklanır)
    baslik: str
    aciklama: str
    kurallar: tuple = ()  # boş: yalnızca elle işaretlenir


ISLER = [
    Is("amort", "Yıllık amortisman ayrıldı", "Duran varlıklar için yıllık amortisman gideri kaydedildi; birikmiş amortisman varlık tutarını aşmıyor.", ("K-17",)),
    Is("yansitma", "7/A giderleri 7/B'ye yansıtıldı", "710, 720, 730, 740, 750 hesaplarındaki giderler 7/B (mali yönetim/maliyet) hesaplarına aktarıldı.", ("K-18",)),
    Is("donemsellik", "Dönemsellik: gelecek aya/yıla ait gider ve gelirler", "180/280 (gelecek dönem giderleri) ve 380/480 (gelir tahakkukları) kayıtları gözden geçirildi.", ("K-19",)),
    Is("supheli", "Şüpheli alacaklar ve karşılık", "Tahsili şüpheli alacaklar 128'e alındı, karşılık (129) için koşulları gözden geçirin.", ("K-20",)),
    Is("avans", "Hareketsiz avanslar", "Uzun süredir hareketsiz kalan sipariş ve iş avansları temizlendi ya da açıklandı.", ("K-31",)),
    Is("ters", "Ters bakiyeler", "Ters bakiye veren hesaplar düzeltildi ya da nedeni açıklandı.", ("K-10",)),
    Is("kasa", "Kasa sayımı", "Kasa sayıldı, negatif bakiye yok, yüksek tutarlı nakit hareketleri açıklandı.", ("K-09", "K-11")),
    Is("ortaklar", "Ortaklar cari hesabı ve adat", "131/231 ve 331/431 bakiyeleri, faiz (adat) ve örtülü sermaye durumu gözden geçirildi.", ("K-14", "K-15")),
    Is("binek", "Binek otomobil giderleri", "Binek oto giderlerinin kanunen kabul edilmeyen kısmı ayrıldı.", ("K-16",)),
    Is("kapanis", "Kapanış kayıtları", "Dönem kârı hesapları (690–692, 370, 590) tutarlı kapatıldı.", ("K-32",)),
    Is("kur", "Dövizli hesapların yıl sonu değerlemesi", "Dövizli kasa, banka, alacak ve borçlar yıl sonu kuruyla değerlendi, kur farkı kaydedildi (doğrulanmalı: uygulanacak kur ve hesap türleri)."),
    Is("reeskont", "Senetlerin reeskontu", "Alacak ve borç senetleri için reeskont gerekiyorsa hesaplandı ve kaydedildi (doğrulanmalı: oran ve kapsam)."),
    Is("stok", "Stok sayımı ve envanter", "Yıl sonu stok sayımı yapıldı; sayım farkı ve değer düşüklüğü kayıtları işlendi."),
    Is("mutabakat", "Cari hesap ve banka mutabakatları", "Banka ekstreleri, büyük müşteri ve tedarikçi bakiyeleri ile mutabakat sağlandı."),
    Is("babs", "Form Ba-Bs bildirimleri", "Yıl içinde verilmesi gereken Ba-Bs bildirimlerinde eksik ay yok."),
    Is("edefter", "e-Defter beratları", "Yıl içindeki tüm aylar için e-Defter berat süreci tamamlandı ya da planlandı."),
    Is("yillik", "Yıllık beyanname hazırlığı", "Kanunen kabul edilmeyen giderler, istisnalar, indirimler, geçmiş yıl zararı ve mahsup edilecek geçici vergiler listelendi."),
]
ISIM = {i.key: i for i in ISLER}


def sakla_kodu(key: str) -> str:
    return "ys-" + key
