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
                "ödeme kuruluşu veya PTT aracılığıyla yapılmalıdır. İşlem bedeli farklı tarihlerde parça parça "
                "ödense de işlemin toplam tutarı dikkate alınır. 30.000 TL sınırı 11.11.2026'da büro tarafından teyit edildi."),
    "dogrulandi": True,
}

# Resmî ve dinî bayram günleri (hafta sonu dışında tatil sayılan günler). Dinî bayram tarihleri yıla göre değişir;
# listede olmayan yıllarda yalnızca pazar günleri ve sabit resmî tatiller kullanılır.
SABIT_TATILLER = [(1, 1), (4, 23), (5, 1), (5, 19), (7, 15), (8, 30), (10, 29)]
DINI_BAYRAMLAR = {
    2025: [("2025-03-30", 3), ("2025-06-06", 4)],
    2026: [("2026-03-20", 3), ("2026-05-27", 4)],
    2027: [("2027-03-09", 3), ("2027-05-16", 4)],
}

# Örtülü sermaye (KVK 12): ortaklardan ve ilişkili kişilerden alınan borç, dönem başı öz sermayenin bu katını aşarsa
# aşan kısım örtülü sermaye sayılır. Vergino'dan 11.11.2026'da kontrol edildi.
ORTULU_SERMAYE = {
    "kat": 3,
    "dayanak": ("KVK 12: ortaklardan veya ortaklarla ilişkili kişilerden temin edilen borçların, hesap dönemi içinde herhangi bir "
                "tarihte kurumun hesap dönemi başındaki öz sermayesinin üç katını aşan kısmı örtülü sermaye sayılır. Bu kısma "
                "isabet eden faiz, kur farkı ve benzeri giderler indirilemez (KVK 11/1-b). Vergino'dan 11.11.2026'da kontrol edildi."),
    "dogrulandi": True,
}

# Binek otomobil giderlerinde kanunen kabul edilmeyen kısım (GVK 40/5): giderlerin en fazla %70'i indirilebilir.
# 2026 tutar sınırları (332 Seri No.lu GVK Genel Tebliği): aylık kira 46.000 TL; ÖTV+KDV toplamı 1.200.000 TL;
# amortismanda ÖTV ve KDV hariç bedel 1.380.000 TL, vergiler dahil ya da ikinci el 2.600.000 TL. Bu sınırlar kontrol edilmez.
BINEK_OTO = {
    "kkeg_orani": 0.30,
    "dayanak": ("GVK 40/5: binek otomobillere ilişkin giderlerin en fazla %70'i indirilebilir (binek otomobil kiralama ya da "
                "işletme faaliyetinde kullanılanlar hariç); kalan %30 kanunen kabul edilmeyen giderdir. Kiralama (GVK 40/1; 2026: "
                "aylık 46.000 TL), ÖTV+KDV (2026: 1.200.000 TL) ve amortisman (GVK 40/7; 2026: 1.380.000 / 2.600.000 TL) için "
                "tutar sınırları ayrıca uygulanır. Vergino'dan 11.11.2026'da kontrol edildi."),
    "dogrulandi": True,
}

STOPAJ = {
    "kira": 0.20,            # GVK 94/5-a: gerçek kişi ve vergi mükellefi olmayanlardan kiralanan mallar için ödenen kira
    "serbest_meslek": 0.20,  # GVK 94/2-b: serbest meslek kazancı ödemeleri
    "dayanak": ("GVK 94 ve 2009/14592 sayılı BKK: işyeri kirası (94/5-a) ve serbest meslek ödemeleri (94/2-b) için "
                "gelir vergisi tevkifatı %20; kesinti muhtasar ve prim hizmet beyannamesiyle izleyen ayın 26'sına kadar "
                "beyan edilip ödenir. Oranlar doğrulanmadı; Burak teyit etmeli."),
    "dogrulandi": False,
}
