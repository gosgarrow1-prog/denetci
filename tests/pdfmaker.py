"""Testler için sentetik beyanname PDF'i üretir (gerçek beyanname kullanılmaz).

Standart Helvetica yazı tipinde ğ, ş, ı, İ bulunmadığından bu harfler ASCII karşılıklarıyla yazılır;
okuyucu etiketleri zaten sadeleştirerek karşılaştırır.
"""

ASCII = str.maketrans("ğĞşŞıİ’", "gGsSiI'")


def _esc(text: str) -> bytes:
    t = text.translate(ASCII).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return t.encode("cp1252")


def make_pdf(lines: list[list[tuple[int, str]]]) -> bytes:
    """lines: her satır [(x, metin), ...]. Satırlar yukarıdan aşağı yazılır."""
    stream = b""
    y = 800
    for segs in lines:
        for x, text in segs:
            stream += b"BT /F1 9 Tf %d %d Td (" % (x, y) + _esc(text) + b") Tj ET\n"
        y -= 14
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"endstream",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


def _amt(kurus: int) -> str:
    lira, kr = divmod(abs(kurus), 100)
    return ("-" if kurus < 0 else "") + f"{lira:,}".replace(",", ".") + f",{kr:02d}"


def _head(title, sub, vkn, unvan1, unvan2, vd, extra):
    rows = [[(150, title)]]
    if sub:
        rows.append([(150, sub)])
    rows += extra
    rows += [
        [(40, "Onay Zamanı :"), (120, "10.09.2026 - 10:00:00")],
        [(40, "Vergi Kimlik Numarası"), (200, vkn)],
        [(40, "Soyadı (Unvanı)"), (200, unvan1)],
        [(40, "Adı (Unvanın Devamı)"), (200, unvan2)],
    ]
    rows.insert(1, [(30, f"{vd} VD"), (200, "DONEM TIPI")])
    return rows


def kdv_pdf(yil=2026, ay="Ağustos", vkn="1234567890", unvan=("ORNEK KAGIT AMBALAJ", "SAN TIC LTD STI"),
            hesaplanan=36_000_00, onceki_devreden=10_000_00, yurtici=30_000_00, sonraki_devreden=4_000_00,
            odenecek=0) -> bytes:
    rows = _head("KATMA DEĞER VERGİSİ BEYANNAMESİ", "(Gerçek Usulde Vergilendirilen Mükellefler İçin)", vkn, unvan[0],
                 unvan[1], "MERKEZ", [[(200, "Aylık"), (350, "Yıl"), (400, str(yil))], [(350, "Ay"), (400, ay)]])
    toplam = hesaplanan
    indirim = onceki_devreden + yurtici
    for label, v in [("Matrah Toplamı", hesaplanan * 5), ("Hesaplanan Katma Değer Vergisi", hesaplanan),
                     ("Daha Önce İndirim Konusu Yapılan KDV’nin İlavesi", 0), ("Toplam Katma Değer Vergisi", toplam),
                     ("Yurtiçi Alımlara İlişkin KDV", yurtici), ("Önceki Dönemden Devreden", onceki_devreden),
                     ("İndirimler Toplamı", indirim), ("Tecil Edilecek Katma Değer Vergisi", 0),
                     ("Ödenmesi Gereken Katma Değer Vergisi", odenecek),
                     ("Bu Dönemde Ödenmesi Gereken Katma Değer Vergisi", odenecek),
                     ("İade Edilmesi Gereken Katma Değer Vergisi", 0),
                     ("Sonraki Döneme Devreden Katma Değer Vergisi", sonraki_devreden)]:
        rows.append([(40, label), (420, _amt(v))])
    return make_pdf(rows)


def gecici_pdf(yil=2026, donem=2, vkn="1234567890", unvan=("ORNEK KAGIT AMBALAJ", "SAN TIC LTD STI"),
               ticari_kar=400_000_00, matrah=400_000_00, hesaplanan=100_000_00, onceki=30_000_00, odenecek=70_000_00) -> bytes:
    rows = _head("GEÇİCİ VERGİ BEYANNAMESİ", "( Kurumlar Vergisi Mükellefleri İçin )", vkn, unvan[0], unvan[1], "MERKEZ",
                 [[(200, "Geçici Vergi Dönemi (Normal Dönem)"), (350, "Yılı"), (400, str(yil))],
                  [(350, "Dönem"), (400, f"{donem}. Dönem")]])
    for label, v in [("Ticari Bilanço Karı", max(ticari_kar, 0)), ("Ticari Bilanço Zararı", max(-ticari_kar, 0)),
                     ("Kanunen Kabul Edilmeyen Gider", 0), ("Toplam Geçmiş Yıl Zararları", 0),
                     ("Mahsup Edilecek Geçmiş Yıl Zararları", 0), ("Geçici Vergi Matrahı", matrah),
                     ("Hesaplanan Geçici Vergi", hesaplanan), ("Nihai Hesaplanan Geçici Vergi", hesaplanan),
                     ("Önceki Dönemlerde Hesaplanan Geçici Vergi", onceki), ("Ödenecek Geçici Vergi", odenecek),
                     ("Sonraki Döneme Devreden Hesaplanan Geçici Vergi", 0)]:
        rows.append([(40, label), (420, _amt(v))])
    return make_pdf(rows)
