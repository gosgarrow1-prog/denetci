"""Vergi parametreleri. Oranlar ve tarihler koda gömülmez; buradan okunur ve kullanıcı tarafından doğrulanmalıdır.

"dogrulandi": False olan değerler ekranda "doğrulanmadı" etiketiyle gösterilir.
"""

GECICI_VERGI = {
    # Geçici vergi dönem sonları (ay, gün). 4. dönem (Ekim–Aralık) beyanı kaldırılmıştır; doğrulayın.
    "donem_sonlari": [(3, 31), (6, 30), (9, 30)],
    # Kurum kazançları için geçici vergi oranı = kurumlar vergisi oranı (yıla göre).
    "kurum_orani": {2025: 0.25, 2026: 0.25},
    # Gelir vergisi mükellefleri (şahıs işletmeleri) için geçici vergi oranı.
    "sahis_orani": {2025: 0.15, 2026: 0.15},
    # Beyan dönemi izleyen ikinci ayın 14'ü, ödeme 17'si (GVK mük. 120).
    "beyan_ay_sonra": 2,
    "beyan_gunu": 14,
    "odeme_gunu": 17,
    "dayanak": ("GVK mükerrer 120 (beyan izleyen 2. ayın 14'ü, ödeme 17'si; şahıs oranı GVK 103 ilk dilim %15), KVK 32 "
                "(geçici vergi cari dönem kurumlar vergisi oranında, genel oran %25). Vergino'dan 01.11.2026'da kontrol edildi. "
                "Banka/finans kurumları (%30), ihracat (5 puan indirim) ve 2027'den itibaren üretim kazancı (%12,5) için "
                "farklı oranlar uygulanmaz; bu mükelleflerde oranı ayrıca kontrol edin."),
    "dogrulandi": True,
    # Yurt içi asgari kurumlar vergisi (KVK 32/C): indirim ve istisnalar düşülmeden önceki kurum kazancının
    # (ticari kâr + KKEG) %10'u; geçici vergi dönemlerinde de uygulanır (fıkra 4); ilk 3 hesap dönemi uygulanmaz (fıkra 5).
    # Geçmiş yıl zararı bu matrahtan düşülmez. Vergino'dan 02.11.2026'da kontrol edildi.
    "asgari_kv_orani": {2025: 0.10, 2026: 0.10},
}

KURUM_ISARETLERI = ("a.s", "as", "anonim", "ltd", "limited", "sti", "sirketi", "koop", "kooperatif")


# Tahsilat ve ödemelerin banka/PTT aracılığıyla yapılması (tevsik) zorunluluğunun tutar sınırı (kuruş).
TEVSIK = {
    "limit": 30_000_00,
    "dayanak": ("VUK Genel Tebliği (Sıra No: 459) ve değişiklikleri: belirlenen tutarı aşan tahsilat ve ödemeler banka, "
                "ödeme kuruluşu veya PTT aracılığıyla yapılmalıdır. Tutar sınırı 30.000 TL olarak girildi; "
                "güncel tebliğden doğrulayın."),
    "dogrulandi": False,
}

# Resmî ve dinî bayram günleri (hafta sonu dışında tatil sayılan günler). Dinî bayram tarihleri yıla göre değişir;
# listede olmayan yıllarda yalnızca pazar günleri ve sabit resmî tatiller kullanılır.
SABIT_TATILLER = [(1, 1), (4, 23), (5, 1), (5, 19), (7, 15), (8, 30), (10, 29)]
DINI_BAYRAMLAR = {
    2025: [("2025-03-30", 3), ("2025-06-06", 4)],
    2026: [("2026-03-20", 3), ("2026-05-27", 4)],
    2027: [("2027-03-09", 3), ("2027-05-16", 4)],
}
