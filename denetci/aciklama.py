"""Bulguların mali müşavire sade anlatımı: Ne buldu / Neden önemli / Ne yapmalı.

Kontrollerin ürettiği metinler (tespit, kesinlik, önerilen adım) değişmez; bu modül onları ekranda ve raporda
okunur bir düzene sokar: tekrarları ayıklar, adımları maddelere böler, teknik kodları geri plana alır.
"""

import re

# Bulgu türü → mali müşavirin hemen anlayacağı hüküm etiketi.
HUKUM = {
    "A": ("Kesin hata", "kesin", "Rakamlar birbirini tutmuyor; bu bir hesap hatası, yoruma açık değil."),
    "K": ("Olası sorun", "olasi", "Kayıtlar bir kurala uymuyor gibi görünüyor; haklı bir nedeni olabilir, kontrol edin."),
    "I": ("Dikkat çeken durum", "dikkat", "Olağandışı bir görünüm; tek başına hata değildir."),
    "D": ("Belgeyle kontrol edin", "belge", "Defterden tek başına karar verilemiyor; belgeye veya mükellefe bakmak gerekir."),
}

# Kontrol → bu bulgu neden önemli (tek cümle, sade).
NEDEN = {
    "K-00": "Belge dönemin tamamını kapsamıyorsa karşılaştırmalar eksik kalır ve bazı hatalar gözden kaçar.",
    "K-01": "Mizan denk değilse defterin bir yerinde kayıt eksik veya yanlıştır; bu mizanla beyanname hazırlanamaz.",
    "K-02": "Borcu alacağına eşit olmayan fiş, mizanı ve beyannameye giren tutarları bozar; e-Defter de bu fişle oluşmaz.",
    "K-03": "Hesabın bakiyesi hareketlerinden çıkmıyorsa ya dosya eksik alınmıştır ya da defterde hatalı bir kayıt vardır.",
    "K-04": "Ana hesap toplamı alt hesaplarla tutmazsa mizanda eksik alt hesap veya hatalı kayıt vardır.",
    "K-05": "Yevmiye ile mizan aynı rakamı göstermiyorsa iki belgeden biri eksik veya farklı tarihte alınmıştır.",
    "K-05K": "Yevmiye ile defteri kebir farklıysa defterler birbirini tutmuyor demektir; e-Defter beratında sorun çıkabilir.",
    "K-05M": "Yevmiye ile muavin farklıysa cari veya banka bakiyeleri yanlış görünüyor olabilir.",
    "K-09": "Kasa eksi bakiye veremez; vergi incelemesinde kaydedilmemiş satış (hasılat) olarak değerlendirilebilir.",
    "K-10": "Ters bakiye çoğunlukla yanlış hesaba kayıt demektir; bilanço yanlış görünür, incelemede soru çıkarır.",
    "K-11": "Sınırı aşan tahsilat ve ödemeler bankadan yapılmazsa her işlem için özel usulsüzlük cezası kesilir.",
    "K-12": "Tatil günü giderleri işle ilgisiz harcama olabilir; işle ilgisi yoksa gider yazılamaz.",
    "K-13": "Aynı fatura iki kez kaydedilirse KDV ve gider (ya da satış) fazla görünür; incelemede tarhiyat ve ceza doğar.",
    "K-14": "Ortağa faizsiz para kullandırmak örtülü kazanç dağıtımı sayılır; adat hesaplanıp faturalanmazsa vergi ve ceza çıkar.",
    "K-15": "Ortaktan alınan borç öz sermayenin üç katını aşarsa aşan kısım örtülü sermaye sayılır; faiz ve kur farkı gider yazılamaz.",
    "K-16": "Binek otomobil giderlerinin %30'u gider yazılamaz; ayrılmazsa kâr düşük beyan edilmiş olur.",
    "K-17": "Birikmiş amortisman varlığın değerini aşamaz; aşıyorsa fazla gider yazılmıştır.",
    "K-18": "7/A'da gider hesapları dönem sonunda yansıtılmazsa gelir tablosu eksik veya yanlış çıkar.",
    "K-19": "Peşin ödenen giderin dönem payı gidere aktarılmazsa dönem kârı yanlış hesaplanır.",
    "K-20": "Şüpheli alacak karşılığı alacağı aşamaz; aşan kısım gider olarak kabul edilmez.",
    "K-31": "Uzun süre kapanmayan avans, fatura alınmamış veya kesilmemiş bir işlemi gösterebilir; KDV ve gelir eksik kalır.",
    "K-32": "Kapanış kayıtları birbirini tutmazsa bilançodaki dönem kârı ve vergi karşılığı yanlış görünür.",
    "K-33": "Tutarların dağılımı doğal görünmüyorsa elle uydurulmuş veya tekrar eden kayıtlar olabilir; yalnızca bir işarettir.",
    "K-34": "Gerçek kişiye ödenen kira ve serbest meslek ücretinde stopaj kesilmezse vergi ve ceza mükellefe çıkar.",
    "K-35": "Kesilen stopaj izleyen ay ödenmezse gecikme faizi ve ceza işler.",
    "K-36": "Muhtasar beyanname ile defter farklıysa ya beyan eksik verilmiştir ya da kayıt hatalıdır.",
    "K-37": "Tevkifatlı KDV (KDV 2) zamanında beyan edilip ödenmezse gecikme faizi ve ceza işler.",
    "K-21": "191 ve 391 ay sonunda mahsup edilmezse ödenecek veya devreden KDV defterde doğru görünmez.",
    "K-22": "Devreden KDV önceki aydan doğru gelmezse beyannamedeki devir ile defter birbirini tutmaz.",
    "K-22D": "Devreden KDV varken ödenecek KDV çıkması çoğunlukla mahsup veya devir hatasıdır; gereksiz ödeme yapılmış olabilir.",
    "K-23": "Satışa göre hesaplanan KDV önceki aylardan çok farklıysa eksik veya yanlış oranla KDV hesaplanmış olabilir.",
    "K-24": "Beyanname ile defter farklıysa ya beyan yanlış verilmiştir ya da kayıt sonradan değişmiştir.",
    "K-25": "Satış tutarı ile KDV'si oran bazında tutmazsa yanlış oranla fatura veya kayıt vardır.",
    "K-26": "İndirilecek KDV oran bazında beyannameyle tutmazsa indirim eksik veya fazla yapılmış olabilir.",
    "K-30": "Deftere girmeyen fatura eksik satış veya eksik indirim demektir; mükerrer kayıt ise fazla KDV indirimi demektir.",
    "K-27": "Amortisman, kur değerlemesi ve satış maliyeti kaydı yapılmadan geçici vergi kârı yanlış hesaplanır.",
    "K-28": "Beyan edilen kâr defterle tutmazsa geçici vergi eksik veya fazla ödenmiş olabilir.",
    "K-29": "193'te kalan peşin vergi mahsup edilmez veya iade istenmezse mükellef parasını geri alamayabilir.",
    "K-40": "Tekdüzen'de olmayan hesap kodu e-Defter'de hata verir ve usulsüzlük cezasına yol açabilir.",
    "K-41": "Ana hesabın adı Tekdüzen'den farklıysa hesap amacı dışında kullanılıyor olabilir; e-Defter'de sorun çıkarabilir.",
    "K-42": "Alt hesabı olan ana hesaba doğrudan kayıt, cari ve banka bazında izlemeyi bozar; muavin bakiyeleri tutmaz.",
    "K-43": "Gelir veya gider hesabında ters bakiye çoğunlukla yanlış hesaba kayıt demektir; gelir tablosu yanlış çıkar.",
    "K-44": "7/A ve 7/B birlikte kullanılırsa maliyetler iki kez veya eksik görünür; seçenek yıl içinde değiştirilemez.",
}

# Kesinlik metnindeki teknik girişler: hüküm etiketi bunu zaten söylüyor.
_KESIN_GIRIS = re.compile(
    r"^(?:Fark |Bakiye |Gider tutarı |Kayıtlar |Tutar(?:lar)? )?(?:aritmetik olarak )?kesin(?:dir)?(?![a-zçğıöşü])\s*[.;:]?\s*",
    re.IGNORECASE)
_BELIRSIZ_GIRIS = re.compile(
    r"^(?:Uygunsuzluk olup olmadığı belirsiz|Nedeni belirsiz|Neden belirsiz)\s*:\s*", re.IGNORECASE)

# Dayanak metnindeki iç notlar (doğrulama durumu ayrıca, bir kez gösterilir).
_DAYANAK_NOT = re.compile(
    r"\s*(?:Dayanak kaynağı henüz kullanıcı tarafından doğrulanmadı\.|Oranlar doğrulanmadı; Burak teyit etmeli\.|"
    r"Süreler doğrulanmalı\.)", re.IGNORECASE)


def _cumleler(metin: str) -> list[str]:
    """Metni cümlelere böler (kısaltma ve tutarlardaki noktalara dokunmadan)."""
    parcalar = re.split(r"(?<=[.!?])\s+(?=[A-ZÇĞİÖŞÜ0-9])", (metin or "").strip())
    return [p.strip() for p in parcalar if p.strip()]


def _sade(metin: str) -> str:
    return re.sub(r"\s+", " ", (metin or "").lower()).strip(" .")


def dikkat(certainty: str, exceptions: str | None) -> list[str]:
    """'Şu durumlarda sorun olmayabilir' maddeleri: kesinlik ve bilinen istisnalar tek listede, tekrarsız."""
    c = _KESIN_GIRIS.sub("", (certainty or "").strip())
    c = _BELIRSIZ_GIRIS.sub("", c)
    maddeler = []
    gorulen = set()
    for kaynak in (c, exceptions or ""):
        for cumle in _cumleler(kaynak):
            cumle = _BELIRSIZ_GIRIS.sub("", cumle)
            anahtar = _sade(cumle)
            if not anahtar or anahtar in gorulen or any(anahtar in g or g in anahtar for g in gorulen):
                continue
            gorulen.add(anahtar)
            maddeler.append(cumle[0].upper() + cumle[1:])
    return maddeler


def adimlar(next_step: str) -> tuple[list[str], list[str]]:
    """Önerilen adımı maddelere böler; 'Gerekli belgeler: a, b' kısmını ayrı liste olarak döner."""
    belgeler: list[str] = []
    metin = next_step or ""
    m = re.search(r"Gerekli belgeler\s*:\s*([^.]*)\.?", metin)
    if m:
        belgeler = [b.strip() for b in m.group(1).split(",") if b.strip()]
        metin = (metin[:m.start()] + metin[m.end():]).strip()
    maddeler = []
    for cumle in _cumleler(metin):
        # "x; y" ile ayrılmış iki ayrı iş de ayrı madde olur.
        for parca in re.split(r";\s+", cumle):
            parca = parca.strip()
            if parca:
                maddeler.append(parca[0].upper() + parca[1:].rstrip(".") + ".")
    return maddeler, belgeler


def dayanak(basis: str | None, verified: bool) -> tuple[str, bool]:
    """Dayanak metni (iç notlar ayıklanmış) ve 'henüz teyit edilmedi' uyarısı gösterilsin mi."""
    metin = _DAYANAK_NOT.sub("", basis or "").strip()
    return metin, not verified


def bulgu(f, rule=None) -> dict:
    """Bir bulgunun ekran ve rapor için sade anlatımı."""
    etiket, sinif, hukum_aciklama = HUKUM.get(f.kind, HUKUM["K"])
    maddeler, belgeler = adimlar(f.next_step)
    dayanak_metni, teyitsiz = dayanak(rule.basis if rule else None, rule.basis_verified if rule else True)
    return {
        "hukum": etiket,
        "hukum_sinif": sinif,
        "hukum_aciklama": hukum_aciklama,
        "ne_buldu": f.explanation,
        "neden": NEDEN.get(f.rule_id) or (rule.description if rule else ""),
        "adimlar": maddeler,
        "belgeler": belgeler,
        "dikkat": dikkat(f.certainty, rule.exceptions if rule else None),
        "dayanak": dayanak_metni,
        "teyitsiz": teyitsiz,
    }
