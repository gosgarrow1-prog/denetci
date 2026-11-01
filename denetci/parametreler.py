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
}

KURUM_ISARETLERI = ("a.s", "as", "anonim", "ltd", "limited", "sti", "sirketi", "koop", "kooperatif")
