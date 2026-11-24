"""Tekdüzen Hesap Planı ana hesapları (1 Sıra No.lu Muhasebe Sistemi Uygulama Genel Tebliği).

Ana hesap kodu -> Tekdüzen'deki adı. 8 (serbest) ve 9 (nazım) hesap sınıfları işletmeye bırakıldığı için listede
yoktur ve kontrollerde serbest sayılır. Liste genel bilgiyle hazırlandı; resmî metinle henüz karşılaştırılmadı.
"""

THP = {
    # 10 Hazır değerler
    "100": "Kasa", "101": "Alınan Çekler", "102": "Bankalar", "103": "Verilen Çekler ve Ödeme Emirleri",
    "108": "Diğer Hazır Değerler",
    # 11 Menkul kıymetler
    "110": "Hisse Senetleri", "111": "Özel Kesim Tahvil Senet ve Bonoları", "112": "Kamu Kesimi Tahvil Senet ve Bonoları",
    "118": "Diğer Menkul Kıymetler", "119": "Menkul Kıymetler Değer Düşüklüğü Karşılığı",
    # 12 Ticari alacaklar
    "120": "Alıcılar", "121": "Alacak Senetleri", "122": "Alacak Senetleri Reeskontu",
    "124": "Kazanılmamış Finansal Kiralama Faiz Gelirleri", "126": "Verilen Depozito ve Teminatlar",
    "127": "Diğer Ticari Alacaklar", "128": "Şüpheli Ticari Alacaklar", "129": "Şüpheli Ticari Alacaklar Karşılığı",
    # 13 Diğer alacaklar
    "131": "Ortaklardan Alacaklar", "132": "İştiraklerden Alacaklar", "133": "Bağlı Ortaklıklardan Alacaklar",
    "135": "Personelden Alacaklar", "136": "Diğer Çeşitli Alacaklar", "137": "Diğer Alacak Senetleri Reeskontu",
    "138": "Şüpheli Diğer Alacaklar", "139": "Şüpheli Diğer Alacaklar Karşılığı",
    # 15 Stoklar
    "150": "İlk Madde ve Malzeme", "151": "Yarı Mamuller Üretim", "152": "Mamuller", "153": "Ticari Mallar",
    "157": "Diğer Stoklar", "158": "Stok Değer Düşüklüğü Karşılığı", "159": "Verilen Sipariş Avansları",
    # 17 Yıllara yaygın inşaat ve onarım maliyetleri
    **{f"17{i}": "Yıllara Yaygın İnşaat ve Onarım Maliyetleri" for i in range(8)},
    "178": "Yıllara Yaygın İnşaat Enflasyon Düzeltme Hesabı", "179": "Taşeronlara Verilen Avanslar",
    # 18 Gelecek aylara ait giderler ve gelir tahakkukları
    "180": "Gelecek Aylara Ait Giderler", "181": "Gelir Tahakkukları",
    # 19 Diğer dönen varlıklar
    "190": "Devreden KDV", "191": "İndirilecek KDV", "192": "Diğer KDV", "193": "Peşin Ödenen Vergiler ve Fonlar",
    "195": "İş Avansları", "196": "Personel Avansları", "197": "Sayım ve Tesellüm Noksanları",
    "198": "Diğer Çeşitli Dönen Varlıklar", "199": "Diğer Dönen Varlıklar Karşılığı",
    # 22 Ticari alacaklar (uzun vadeli)
    "220": "Alıcılar", "221": "Alacak Senetleri", "222": "Alacak Senetleri Reeskontu",
    "224": "Kazanılmamış Finansal Kiralama Faiz Gelirleri", "226": "Verilen Depozito ve Teminatlar",
    "229": "Şüpheli Alacaklar Karşılığı",
    # 23 Diğer alacaklar (uzun vadeli)
    "231": "Ortaklardan Alacaklar", "232": "İştiraklerden Alacaklar", "233": "Bağlı Ortaklıklardan Alacaklar",
    "235": "Personelden Alacaklar", "236": "Diğer Çeşitli Alacaklar", "237": "Diğer Alacak Senetleri Reeskontu",
    "239": "Şüpheli Diğer Alacaklar Karşılığı",
    # 24 Mali duran varlıklar
    "240": "Bağlı Menkul Kıymetler", "241": "Bağlı Menkul Kıymetler Değer Düşüklüğü Karşılığı", "242": "İştirakler",
    "243": "İştiraklere Sermaye Taahhütleri", "244": "İştirakler Sermaye Payları Değer Düşüklüğü Karşılığı",
    "245": "Bağlı Ortaklıklar", "246": "Bağlı Ortaklıklara Sermaye Taahhütleri",
    "247": "Bağlı Ortaklıklar Sermaye Payları Değer Düşüklüğü Karşılığı", "248": "Diğer Mali Duran Varlıklar",
    "249": "Diğer Mali Duran Varlıklar Karşılığı",
    # 25 Maddi duran varlıklar
    "250": "Arazi ve Arsalar", "251": "Yeraltı ve Yerüstü Düzenleri", "252": "Binalar", "253": "Tesis Makine ve Cihazlar",
    "254": "Taşıtlar", "255": "Demirbaşlar", "256": "Diğer Maddi Duran Varlıklar", "257": "Birikmiş Amortismanlar",
    "258": "Yapılmakta Olan Yatırımlar", "259": "Verilen Avanslar",
    # 26 Maddi olmayan duran varlıklar
    "260": "Haklar", "261": "Şerefiye", "262": "Kuruluş ve Örgütlenme Giderleri", "263": "Araştırma ve Geliştirme Giderleri",
    "264": "Özel Maliyetler", "267": "Diğer Maddi Olmayan Duran Varlıklar", "268": "Birikmiş Amortismanlar",
    "269": "Verilen Avanslar",
    # 27 Özel tükenmeye tabi varlıklar
    "271": "Arama Giderleri", "272": "Hazırlık ve Geliştirme Giderleri", "277": "Diğer Özel Tükenmeye Tabi Varlıklar",
    "278": "Birikmiş Tükenme Payları", "279": "Verilen Avanslar",
    # 28 Gelecek yıllara ait giderler ve gelir tahakkukları
    "280": "Gelecek Yıllara Ait Giderler", "281": "Gelir Tahakkukları",
    # 29 Diğer duran varlıklar
    "291": "Gelecek Yıllarda İndirilecek KDV", "292": "Diğer KDV", "293": "Gelecek Yıllar İhtiyacı Stoklar",
    "294": "Elden Çıkarılacak Stoklar ve Maddi Duran Varlıklar", "295": "Peşin Ödenen Vergiler ve Fonlar",
    "297": "Diğer Çeşitli Duran Varlıklar", "298": "Stok Değer Düşüklüğü Karşılığı", "299": "Birikmiş Amortismanlar",
    # 30 Mali borçlar
    "300": "Banka Kredileri", "301": "Finansal Kiralama İşlemlerinden Borçlar",
    "302": "Ertelenmiş Finansal Kiralama Borçlanma Maliyetleri", "303": "Uzun Vadeli Kredilerin Anapara Taksitleri ve Faizleri",
    "304": "Tahvil Anapara Borç Taksit ve Faizleri", "305": "Çıkarılmış Bonolar ve Senetler",
    "306": "Çıkarılmış Diğer Menkul Kıymetler", "308": "Menkul Kıymetler İhraç Farkı", "309": "Diğer Mali Borçlar",
    # 32 Ticari borçlar
    "320": "Satıcılar", "321": "Borç Senetleri", "322": "Borç Senetleri Reeskontu", "326": "Alınan Depozito ve Teminatlar",
    "329": "Diğer Ticari Borçlar",
    # 33 Diğer borçlar
    "331": "Ortaklara Borçlar", "332": "İştiraklere Borçlar", "333": "Bağlı Ortaklıklara Borçlar", "335": "Personele Borçlar",
    "336": "Diğer Çeşitli Borçlar", "337": "Diğer Borç Senetleri Reeskontu",
    # 34 Alınan avanslar
    "340": "Alınan Sipariş Avansları", "349": "Alınan Diğer Avanslar",
    # 35 Yıllara yaygın inşaat ve onarım hakedişleri
    **{f"35{i}": "Yıllara Yaygın İnşaat ve Onarım Hakediş Bedelleri" for i in range(8)},
    "358": "Yıllara Yaygın İnşaat Enflasyon Düzeltme Hesabı",
    # 36 Ödenecek vergi ve diğer yükümlülükler
    "360": "Ödenecek Vergi ve Fonlar", "361": "Ödenecek Sosyal Güvenlik Kesintileri",
    "368": "Vadesi Geçmiş Ertelenmiş veya Taksitlendirilmiş Vergi ve Diğer Yükümlülükler",
    "369": "Ödenecek Diğer Yükümlülükler",
    # 37 Borç ve gider karşılıkları
    "370": "Dönem Kârı Vergi ve Diğer Yasal Yükümlülük Karşılıkları",
    "371": "Dönem Kârının Peşin Ödenen Vergi ve Diğer Yükümlülükleri", "372": "Kıdem Tazminatı Karşılığı",
    "373": "Maliyet Giderleri Karşılığı", "379": "Diğer Borç ve Gider Karşılıkları",
    # 38 Gelecek aylara ait gelirler ve gider tahakkukları
    "380": "Gelecek Aylara Ait Gelirler", "381": "Gider Tahakkukları",
    # 39 Diğer kısa vadeli yabancı kaynaklar
    "391": "Hesaplanan KDV", "392": "Diğer KDV", "393": "Merkez ve Şubeler Cari Hesabı",
    "397": "Sayım ve Tesellüm Fazlaları", "399": "Diğer Çeşitli Yabancı Kaynaklar",
    # 40 Mali borçlar (uzun vadeli)
    "400": "Banka Kredileri", "401": "Finansal Kiralama İşlemlerinden Borçlar",
    "402": "Ertelenmiş Finansal Kiralama Borçlanma Maliyetleri", "405": "Çıkarılmış Tahviller",
    "407": "Çıkarılmış Diğer Menkul Kıymetler", "408": "Menkul Kıymetler İhraç Farkı", "409": "Diğer Mali Borçlar",
    # 42 Ticari borçlar (uzun vadeli)
    "420": "Satıcılar", "421": "Borç Senetleri", "422": "Borç Senetleri Reeskontu", "426": "Alınan Depozito ve Teminatlar",
    "429": "Diğer Ticari Borçlar",
    # 43 Diğer borçlar (uzun vadeli)
    "431": "Ortaklara Borçlar", "432": "İştiraklere Borçlar", "433": "Bağlı Ortaklıklara Borçlar",
    "436": "Diğer Çeşitli Borçlar", "437": "Diğer Borç Senetleri Reeskontu",
    "438": "Kamuya Olan Ertelenmiş veya Taksitlendirilmiş Borçlar",
    # 44 Alınan avanslar (uzun vadeli)
    "440": "Alınan Sipariş Avansları", "449": "Alınan Diğer Avanslar",
    # 47 Borç ve gider karşılıkları (uzun vadeli)
    "472": "Kıdem Tazminatı Karşılığı", "479": "Diğer Borç ve Gider Karşılıkları",
    # 48 Gelecek yıllara ait gelirler ve gider tahakkukları
    "480": "Gelecek Yıllara Ait Gelirler", "481": "Gider Tahakkukları",
    # 49 Diğer uzun vadeli yabancı kaynaklar
    "492": "Gelecek Yıllara Ertelenen veya Terkin Edilecek KDV", "493": "Tesise Katılma Payları",
    "499": "Diğer Çeşitli Uzun Vadeli Yabancı Kaynaklar",
    # 50–59 Özkaynaklar
    "500": "Sermaye", "501": "Ödenmemiş Sermaye", "502": "Sermaye Düzeltmesi Olumlu Farkları",
    "503": "Sermaye Düzeltmesi Olumsuz Farkları", "520": "Hisse Senedi İhraç Primleri", "521": "Hisse Senedi İptal Kârları",
    "522": "Maddi Duran Varlık Yeniden Değerleme Artışları", "523": "İştirakler Yeniden Değerleme Artışları",
    "524": "Maliyet Artış Fonu", "529": "Diğer Sermaye Yedekleri", "540": "Yasal Yedekler", "541": "Statü Yedekleri",
    "542": "Olağanüstü Yedekler", "548": "Diğer Kâr Yedekleri", "549": "Özel Fonlar", "570": "Geçmiş Yıllar Kârları",
    "580": "Geçmiş Yıllar Zararları", "590": "Dönem Net Kârı", "591": "Dönem Net Zararı",
    # 60–69 Gelir tablosu hesapları
    "600": "Yurtiçi Satışlar", "601": "Yurtdışı Satışlar", "602": "Diğer Gelirler", "610": "Satıştan İadeler",
    "611": "Satış İskontoları", "612": "Diğer İndirimler", "620": "Satılan Mamuller Maliyeti",
    "621": "Satılan Ticari Mallar Maliyeti", "622": "Satılan Hizmet Maliyeti", "623": "Diğer Satışların Maliyeti",
    "630": "Araştırma ve Geliştirme Giderleri", "631": "Pazarlama Satış ve Dağıtım Giderleri",
    "632": "Genel Yönetim Giderleri", "640": "İştiraklerden Temettü Gelirleri",
    "641": "Bağlı Ortaklıklardan Temettü Gelirleri", "642": "Faiz Gelirleri", "643": "Komisyon Gelirleri",
    "644": "Konusu Kalmayan Karşılıklar", "645": "Menkul Kıymet Satış Kârları", "646": "Kambiyo Kârları",
    "647": "Reeskont Faiz Gelirleri", "648": "Enflasyon Düzeltmesi Kârları", "649": "Diğer Olağan Gelir ve Kârlar",
    "653": "Komisyon Giderleri", "654": "Karşılık Giderleri", "655": "Menkul Kıymet Satış Zararları",
    "656": "Kambiyo Zararları", "657": "Reeskont Faiz Giderleri", "658": "Enflasyon Düzeltmesi Zararları",
    "659": "Diğer Olağan Gider ve Zararlar", "660": "Kısa Vadeli Borçlanma Giderleri",
    "661": "Uzun Vadeli Borçlanma Giderleri", "671": "Önceki Dönem Gelir ve Kârları",
    "679": "Diğer Olağandışı Gelir ve Kârlar", "680": "Çalışmayan Kısım Gider ve Zararları",
    "681": "Önceki Dönem Gider ve Zararları", "689": "Diğer Olağandışı Gider ve Zararlar",
    "690": "Dönem Kârı veya Zararı", "691": "Dönem Kârı Vergi ve Diğer Yasal Yükümlülük Karşılıkları",
    "692": "Dönem Net Kârı veya Zararı", "697": "Yıllara Yaygın İnşaat Enflasyon Düzeltme Hesabı",
    "698": "Enflasyon Düzeltme Hesabı",
    # 7/A Maliyet hesapları (fonksiyon esası)
    "700": "Maliyet Muhasebesi Bağlantı Hesabı", "701": "Maliyet Muhasebesi Yansıtma Hesabı",
    "710": "Direkt İlk Madde ve Malzeme Giderleri", "711": "Direkt İlk Madde ve Malzeme Yansıtma Hesabı",
    "712": "Direkt İlk Madde ve Malzeme Fiyat Farkı", "713": "Direkt İlk Madde ve Malzeme Miktar Farkı",
    "720": "Direkt İşçilik Giderleri", "721": "Direkt İşçilik Giderleri Yansıtma Hesabı", "722": "Direkt İşçilik Ücret Farkları",
    "723": "Direkt İşçilik Süre Farkları", "730": "Genel Üretim Giderleri", "731": "Genel Üretim Giderleri Yansıtma Hesabı",
    "732": "Genel Üretim Giderleri Bütçe Farkları", "733": "Genel Üretim Giderleri Verimlilik Farkları",
    "734": "Genel Üretim Giderleri Kapasite Farkları", "740": "Hizmet Üretim Maliyeti",
    "741": "Hizmet Üretim Maliyeti Yansıtma Hesabı", "742": "Hizmet Üretim Maliyeti Fark Hesapları",
    "750": "Araştırma ve Geliştirme Giderleri", "751": "Araştırma ve Geliştirme Giderleri Yansıtma Hesabı",
    "752": "Araştırma ve Geliştirme Gider Farkları", "760": "Pazarlama Satış ve Dağıtım Giderleri",
    "761": "Pazarlama Satış ve Dağıtım Giderleri Yansıtma Hesabı", "762": "Pazarlama Satış ve Dağıtım Giderleri Fark Hesabı",
    "770": "Genel Yönetim Giderleri", "771": "Genel Yönetim Giderleri Yansıtma Hesabı",
    "772": "Genel Yönetim Gider Farkları Hesabı", "780": "Finansman Giderleri", "781": "Finansman Giderleri Yansıtma Hesabı",
    "782": "Finansman Giderleri Fark Hesabı",
    # 7/B Maliyet hesapları (gider çeşitleri esası)
    "790": "İlk Madde ve Malzeme Giderleri", "791": "İşçi Ücret ve Giderleri", "792": "Memur Ücret ve Giderleri",
    "793": "Dışarıdan Sağlanan Fayda ve Hizmetler", "794": "Çeşitli Giderler", "795": "Vergi Resim ve Harçlar",
    "796": "Amortisman ve Tükenme Payları", "797": "Finansman Giderleri", "798": "Gider Çeşitleri Yansıtma Hesabı",
    "799": "Üretim Maliyet Hesabı",
}

YEDI_A = frozenset(k for k in THP if "710" <= k <= "782")
YEDI_B = frozenset(k for k in THP if "790" <= k <= "799")

# Gelir tablosu hesaplarının olağan bakiye yönü (6 ile başlayanlar).
GELIR_ALACAK = frozenset({"600", "601", "602", "640", "641", "642", "643", "644", "645", "646", "647", "648", "649",
                          "671", "679"})
GIDER_BORC = frozenset({"610", "611", "612", "620", "621", "622", "623", "630", "631", "632", "653", "654", "655", "656",
                        "657", "658", "659", "660", "661", "680", "681", "689"})


def serbest(kod: str) -> bool:
    """8 ve 9 ile başlayan hesaplar işletmeye bırakılmıştır."""
    return kod[:1] in ("8", "9")
