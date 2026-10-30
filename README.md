# Denetçi — Muhasebe kontrol modülü

Mali müşavirlik bürosu için hazırlanan özel bir uygulama. Mükelleflerin Luca'dan alınan muhasebe dosyalarını okur, doğrular ve bulguları kaynak kayıtlarıyla birlikte gösterir. **Kendi bilgisayarınızda çalışır; veriler bilgisayarınızdan çıkmaz.**

Durum: **Aşama 0 tamam, Luca biçimleri, KDV ön kontrolleri ve finansal panel eklendi.** Ürün planı: <https://claude.ai/artifact/STx2i6GbryR1XBUJbzykQw>

## Neler yapıyor

- **Giriş:** Kayıt ekranı yok; kullanıcı adı ve parola ile girilir. Uygulama yalnızca `127.0.0.1` adresinde çalıştığı için ağdaki başka bilgisayarlar erişemez. Telefon kodu (MFA) varsayılan olarak kapalıdır; uygulama internete açılacaksa `set DENETCI_TELEFON_KODU=1` ile açılmalıdır.
- **Görünüm:** Solda menü, üstte mükellef ve dönem seçimi. Mükellefler sayfası tüm mükelleflerin son dönem durumunu gösterir. Hiç mükellef yokken **Örnek mükellefle dene** düğmesi sentetik bir mükellef oluşturur.
- **Mükellef listesi:** Luca'dan alınan mükellef listesi (.xlsx/.csv) yüklenince önizleme gösterilir; onaylanınca mükellefler toplu eklenir. Aynı VKN veya unvan ikinci kez eklenmez. (Luca'nın liste düzeni henüz gerçek bir örnekle denenmedi.)
- **Gösterge paneli:** Dönem özeti, finansal özet kutucukları, önem sırasıyla "Dikkat Gerektirenler", kontroller ve belgeler tek sayfada.
- **Analizler ve gereken belgeler:** KDV Ön Kontrol, Defter Mutabakatı, Finansal Analiz ve Geçici Vergi kendi ihtiyaç duyduğu belgeleri listeler ve eksikleri ister. KDV için önceki ayın beyannamesindeki devreden KDV tutarı elle girilebilir; K-22 bunu dönem başı 190 bakiyesiyle karşılaştırır.
- **Geçici vergi:** Yılbaşından dönem sonuna ticari kâr (kümülatif mizan veya defteri kebir), elle girilen KKEG/istisna/indirim/geçmiş yıl zararı ile tahmini matrah ve vergi; önceki dönemlerin mahsubu. Oranlar ve tarihler `denetci/parametreler.py` dosyasındadır ve doğrulanmalıdır. K-27 dönem sonu kayıtlarının (amortisman, kur değerlemesi, satış maliyeti) izine, K-29 yıl sonunda 193 bakiyesine bakar. Ön hesaplamadır, beyan değildir.
- **Dönem işlemleri:** Çalışma notu, kesinleştirme (dönem kilitlenir, açılabilir), analizi temizleme, yazdırılabilir dönem raporu. Dosyadaki firma unvanı seçili mükellefle uyuşmazsa aktarmadan önce sorulur; yüklemeler geri alınabilir.
- **Finansal analiz:** Gelir tablosu özeti, bilanço yapısı grafiği ve temel oranlar (formülleriyle). Bilgi amaçlıdır; kapanmamış dönemlerde dönem sonucu hesaplanarak gösterilir.
- **Toplu yükleme:** Birden fazla dosya sürükle-bırak ile yüklenebilir. Luca dosyasındaki "Tarih Aralığı"ndan dönem kendiliğinden bulunur, analiz kendiliğinden çalışır.
- **Mükellef ve dönem:** Mükellef ve dönem tanımlanabilir.
- **Luca dosyaları:** Luca'nın Excel çıktısı olarak alınan **mizan, yevmiye defteri, defteri kebir ve muavin** kolon eşleştirmesi gerekmeden tanınır. Yevmiyede yalnızca yaprak hesap satırları kayıt olarak alınır; ana ve ara seviye satırları toplamdır. Her fişin TOPLAM satırı ve kebirdeki her grubun Toplam satırı okunan kayıtlarla karşılaştırılır; tutmazsa aktarım hata gösterir. Kebir ve muavindeki "Nakli Yekün" önceki dönemden gelen tutar olarak ayrıca saklanır. Mizanın "Tarih Aralığı" dönem başından başlamıyorsa (ör. yalnızca Eylül), mizan devir içermez; bakiye kolonları gerçek bakiye sayılmaz ve uygulama bunu aktarımda uyarır.
- **Diğer dosyalar:** Mizan ve yevmiye `.xlsx` veya `.csv` olarak da yüklenebilir. Belge türü ve kolonlar otomatik önerilir. Önizlemede ara toplam, nakli yekün ve tekrar eden başlık satırları ayıklanır. Onaylamadan hiçbir şey aktarılmaz.
- **Veri okuma:** Türkçe sayı ve tarih biçimleri okunur. Hesap kodları metin olarak tutulduğu için baştaki sıfırlar korunur. Tutarlar kuruş cinsinden tam sayı olarak saklanır, kayan nokta hatası oluşmaz.
- **Sürümler:** Aynı dosya ikinci kez yüklenirse reddedilir. Yeni sürüm yüklendiğinde eski sürüm ve eski analizler saklanır.
- **Kontroller:** Şu 13 kontrol çalışır:
  - K-00 belge kapsamı (Luca'daki "Tarih Aralığı" dönemi kapsıyor mu)
  - K-01 mizan denkliği
  - K-02 fiş denkliği
  - K-03 açılış + hareket = kapanış
  - K-04 ana/alt hesap tutarlılığı
  - K-05 yevmiye–mizan mutabakatı
  - K-05K yevmiye–defteri kebir mutabakatı (ana hesap bazında, ortak tarih aralığında)
  - K-05M yevmiye–muavin mutabakatı (muavindeki hesaplar için)
  - K-09 negatif kasa (mizan yoksa kebir veya muavinden, nakli yekün dahil)
  - KDV ön kontrolleri (beyan veya vergi görüşü yerine geçmez):
    - K-21 KDV mahsup kaydı: ay sonunda 191 ve 391 birlikte bakiye veriyor mu
    - K-22 devreden KDV sürekliliği: önceki ay sonundaki 190 bakiyesi bu ayın başına doğru devretmiş mi (önceki dönem yüklüyse)
    - K-22D devreden KDV varken ödenecek KDV oluşmuş mu (360 altındaki KDV hesabı adından tanınır, tevkifat hariç)
    - K-23 hesaplanan KDV / net satış oranı; önceki dönemler varsa ortalamadan hem %25 hem 10.000 TL sapma
- **Veri durumu:** Analiz sonucu "analize uygun", "kısmen uygun" veya "kritik veri sorunu var" olarak gösterilir. Eksik belge varsa ilgili kontrol "çalıştırılamadı" olarak işaretlenir.
- **Bulgular:** Her bulgudan orijinal dosyanın sayfa ve satırına inilebilir. Bulgulara durum (yeni, inceleniyor, açıklandı) ve not eklenebilir; bunlar kaynak veriyi değiştirmez.

Henüz yapılmayanlar: PDF ve Word dosyalarının okunması, beyanname gerektiren KDV kontrolleri (K-24–K-26), Excel rapor ve Claude ile soru-cevap. Bunlar sonraki aşamalarda.

> Luca mizan, yevmiye, kebir ve muavin okuyucuları 29.10.2026'da Luca'dan alınmış gerçek örneklerle denendi. Testlerde ve DEMO'da yalnızca bu düzeni taklit eden **sentetik** dosyalar kullanılır; gerçek mükellef dosyaları depoya konmaz.

## Windows'ta kurulum

1. [python.org](https://www.python.org/downloads/) adresinden Python 3.11 veya üstünü kurun. Kurulumda **"Add python.exe to PATH"** kutusunu işaretleyin.
2. Bu depoyu indirin: GitHub'da **Code → Download ZIP** ile indirip bir klasöre açın.
3. O klasörün adres çubuğuna `cmd` yazıp Enter'a basın. Açılan pencerede şunları çalıştırın:

```
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
py -m denetci kullanici-ekle burak
```

4. Son komut sizden bir parola ister (en az 12 karakter).
5. İsteğe bağlı olarak sentetik demo verisini oluşturun: `py -m denetci demo`
6. Uygulamayı başlatın: `py -m denetci calistir`. Ardından tarayıcıda <http://127.0.0.1:8000> adresini açın.

Sonraki kullanımlarda aynı klasörde şu iki komut yeterli: `.venv\Scripts\activate` ve ardından `py -m denetci calistir`.

## Veriler ve yedek

Tüm veriler (veritabanı ve yüklenen orijinal dosyalar) `C:\Users\<kullanıcı>\DenetciVeri` klasöründe durur. **Yedek almak için uygulama kapalıyken bu klasörü harici bir diske kopyalayın.** Bu klasör hiçbir zaman GitHub'a gönderilmez.

Farklı bir klasör kullanmak isterseniz uygulamayı başlatmadan önce şu komutu çalıştırın: `set DENETCI_VERI=D:\Denetci`

## Geliştirme

```
pip install -r requirements-dev.txt
py -m pytest
```

Testler bilinen sonuçlu sentetik verilerle şunları doğrular:

- Hatalı setteki 8 bilinen hatanın hepsi bulunuyor, temiz sette hiç bulgu çıkmıyor.
- Ara toplamlar çift sayılmıyor.
- Kanıt satırı, orijinal dosyadaki satırla birebir eşleşiyor.
- Aynı dosya tekrar yüklenemiyor.
- Bir mükellefin adresiyle başka bir mükellefin verisine erişilemiyor.
- Giriş, MFA ve form güvenliği (CSRF) çalışıyor.

## Teknik notlar

- **Altyapı:** Python, FastAPI, SQLite (SQLAlchemy) ve sunucu tarafında üretilen HTML. Kurulumu kolaylaştırmak için Node.js veya Docker gerekmiyor. İleride sunucuya taşınırken veritabanı PostgreSQL'e geçirilebilir.
- **Mükellef yalıtımı:** Her tabloda `client_id` var ve tüm adresler mükellef bazında doğrulanıyor.
- **Kurallar:** `denetci/checks/rules.py` dosyasında. Her kuralda kimlik, sürüm, dayanak, dayanağın doğrulanma durumu ve istisnalar yer alıyor. Kurallar bir çerçeve (`frameworks`) etiketi taşıyor; böylece ileride BOBİ FRS gibi başka çerçeveler eklenebilecek. Dayanaklar siz doğrulayana kadar "doğrulanmadı" olarak işaretli.
