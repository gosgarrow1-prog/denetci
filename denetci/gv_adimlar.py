"""Geçici vergi hazırlık aşamaları: verilerden kendini işaretleyen adım listesi.

Durumlar: tamam, uyari (yapılmış ama bakılmalı), eksik (yapılmalı), elle (kullanıcı işaretler).
Elle işaretlenen adımlar dönemin gv sözlüğünde "adimlar" altında saklanır.
"""

from dataclasses import dataclass

from .parsing import kurus_str

ELLE = {
    "degerleme": "Değerleme ve tahakkuk kayıtları yapıldı",
    "beyan": "Beyanname verildi ve tahakkuk alındı",
}


@dataclass
class Adim:
    key: str
    baslik: str
    durum: str
    metin: str
    eylem: str | None = None  # bağlantı metni
    link: str | None = None
    elle: bool = False  # kullanıcı işaretleyebilir
    isaretli: bool = False


def adimlar(c, p, gv, results, findings) -> list[Adim]:
    base = f"/mukellef/{c.id}"
    q = f"?donem={p.id}"
    saved = p.gv or {}
    elle = saved.get("adimlar") or {}
    res = {r["id"]: r for r in results}
    out = []

    # 1. Belgeler
    if gv.ticari_kar is not None:
        out.append(Adim("belgeler", "Yılbaşından mizan veya yevmiye", "tamam",
                        f"01.01 – {p.bitis:%d.%m.%Y} kümülatif veriler okundu ({gv.kaynak})."))
    else:
        out.append(Adim("belgeler", "Yılbaşından mizan veya yevmiye", "eksik",
                        f"Luca'dan mizanı veya yevmiyeyi 01.01.{p.bitis.year} – {p.bitis:%d.%m.%Y} aralığıyla Excel olarak alıp yükleyin.",
                        "Dosya yükle", f"{base}/belgeler{q}"))

    # 2. Önceki dönem beyannamesi
    if gv.donem_no == 1:
        out.append(Adim("onceki", "Önceki dönem beyannamesi", "tamam", "1. dönemde mahsup edilecek önceki dönem vergisi yok."))
    elif gv.mahsup_kaynak == "girildi":
        out.append(Adim("onceki", "Önceki dönem beyannamesi", "tamam",
                        f"Önceki dönemlerde hesaplanan vergi elle girildi: {kurus_str(gv.mahsup)} TL."))
    elif gv.mahsup_kaynak:
        out.append(Adim("onceki", "Önceki dönem beyannamesi", "tamam",
                        f"{gv.mahsup_kaynak}nden okundu: {kurus_str(gv.mahsup)} TL mahsup edilecek."))
    else:
        out.append(Adim("onceki", "Önceki dönem beyannamesi", "eksik",
                        "Bir önceki geçici vergi beyannamesini GİB'den PDF olarak yükleyin; mahsup edilecek vergi oradan okunur.",
                        "Beyanname yükle", f"{base}/belgeler{q}"))

    # 3. Dönem sonu kayıtları (K-27) ve satış maliyeti
    r27 = res.get("K-27")
    f27 = [f.title for f in findings if f.rule_id == "K-27"]
    if gv.maliyet and gv.maliyet.ek:
        f27.append(f"Satışların maliyeti tahmin edildi ({kurus_str(gv.maliyet.ek)} TL); kayıt yapılmalı")
    if f27:
        out.append(Adim("kayitlar", "Dönem sonu kayıtları", "uyari",
                        "Eksik görünen kayıtlar: " + "; ".join(f27) + ". Luca'da kayıtları girip dosyaları yeniden yükleyin.",
                        "Kontrollere bak", "#kontroller"))
    elif r27 and r27["status"] == "gecti":
        out.append(Adim("kayitlar", "Dönem sonu kayıtları", "tamam", r27["message"]))
    else:
        out.append(Adim("kayitlar", "Dönem sonu kayıtları", "eksik",
                        "Amortisman, satışların maliyeti (stok sayımı) ve 7/A yansıtma kayıtları kontrol edilemedi; "
                        "yılbaşından veriler gerekli.", "Kontrollere bak", "#kontroller"))

    # 4. Değerleme (elle)
    ok = bool(elle.get("degerleme"))
    out.append(Adim("degerleme", "Değerleme ve tahakkuklar", "tamam" if ok else "elle",
                    "Dövizli hesapların kur değerlemesi, alacak/borç senetleri reeskontu, şüpheli alacak karşılığı, "
                    "gider ve gelir tahakkukları (kira, faiz, sigorta) ve peşin ödenen giderlerin dönemine ayrılması. "
                    "Uygulama bunları otomatik denetlemez; yaptıktan sonra işaretleyin.",
                    elle=True, isaretli=ok))

    # 5. Düzeltmeler
    if saved.get("kaydedildi"):
        g = gv.girdiler
        parca = [f"{ad} {kurus_str(g.get(k))}" for k, ad in (("kkeg", "KKEG"), ("istisna", "istisna"),
                                                                ("gecmis_zarar", "geçmiş yıl zararı")) if g.get(k)]
        out.append(Adim("duzeltme", "KKEG, istisna ve geçmiş yıl zararı", "tamam",
                        ("Girildi: " + ", ".join(parca) + ".") if parca else "Kaydedildi (tutar girilmedi, sıfır kabul edildi)."))
    else:
        out.append(Adim("duzeltme", "KKEG, istisna ve geçmiş yıl zararı", "eksik",
                        "Kanunen kabul edilmeyen giderleri, istisnaları ve mahsup edilecek geçmiş yıl zararını aşağıdaki "
                        "Düzeltmeler bölümüne girip kaydedin. Yoksa boş bırakıp kaydedin.", "Düzeltmelere git", "#duzeltmeler"))

    # 6. Hesaplama ve beyanname karşılaştırması
    r28 = res.get("K-28")
    hesap = (f"Tahmini matrah {kurus_str(gv.matrah)} TL, ödenecek {kurus_str(gv.odenecek)} TL."
             if gv.matrah is not None and gv.odenecek is not None else "Hesaplama için yılbaşından veri gerekli.")
    if r28 and r28["status"] == "gecti":
        out.append(Adim("hesap", "Hesaplama ve beyanname kontrolü", "tamam", hesap + " Verilen beyanname defterle uyumlu."))
    elif r28 and r28["status"] == "bulgu":
        out.append(Adim("hesap", "Hesaplama ve beyanname kontrolü", "uyari", hesap + " " + r28["message"],
                        "Karşılaştırmaya bak", "#karsilastirma"))
    else:
        out.append(Adim("hesap", "Hesaplama ve beyanname kontrolü", "tamam" if gv.matrah is not None else "eksik",
                        hesap + " Beyanname hazırlayınca PDF'ini yüklerseniz defterle karşılaştırılır.",
                        "Hesaplamaya bak", "#hesap"))

    # 7. Beyan ve ödeme
    if gv.beyanname:
        out.append(Adim("beyan", "Beyan ve ödeme", "tamam",
                        "Bu dönemin beyannamesi yüklendi."
                        + (f" Ödeme son günü {gv.odeme:%d.%m.%Y}." if gv.odeme else "")))
    else:
        ok = bool(elle.get("beyan"))
        tarih = (f"Beyan son günü {gv.beyan:%d.%m.%Y}, ödeme son günü {gv.odeme:%d.%m.%Y}." if gv.beyan else "")
        out.append(Adim("beyan", "Beyan ve ödeme", "tamam" if ok else "elle", tarih + " Beyannameyi verdikten sonra "
                        "işaretleyin ya da PDF'ini yükleyin.", elle=True, isaretli=ok))
    return out
