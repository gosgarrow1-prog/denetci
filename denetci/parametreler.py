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
    # Beyan ve ödeme: dönemi izleyen ikinci ayın 17'si.
    "beyan_ay_sonra": 2,
    "beyan_gunu": 17,
    "dayanak": "GVK mükerrer 120, KVK 32. Oranlar ve tarihler mevzuat değişikliklerine göre güncellenmelidir.",
    "dogrulandi": False,
}

KURUM_ISARETLERI = ("a.s", "as", "anonim", "ltd", "limited", "sti", "sirketi", "koop", "kooperatif")
