"""GİB e-Defter (XBRL-GL) yevmiye ve kebir dosyalarının okuyucusu.

e-Defter dosyası .xml olarak ya da GİB'e gönderilen paket gibi .zip içinde gelebilir. Biçim:
    edefter:defter / xbrli:xbrl / gl-cor:accountingEntries
      gl-cor:documentInfo      entriesType (journal = yevmiye, ledger = kebir), periodCoveredStart / End
      gl-cor:entityInformation gl-bus:organizationIdentifier (VKN/TCKN), gl-bus:organizationDescription (unvan)
      gl-cor:entryHeader       yevmiye maddesi: entryNumber, entryNumberCounter, enteredDate, entryComment
        gl-cor:entryDetail     satır: account/accountMainID, accountSub/accountSubID, amount, debitCreditCode,
                               postingDate, documentNumber, detailComment

Dosya, Luca okuyucusunun kullandığı satır yapısına çevrilir: her satır bir hesap hareketidir ve dosyadaki sırasıyla
numaralanır (kaynak satıra iz). Ad alanı önekleri programa göre değişebildiği için etiketler yerel adla okunur.
"""

import io
import zipfile
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree as ET

from ..parsing import fold

SHEET_ADI = {"journal": "e-Defter yevmiye", "ledger": "e-Defter kebir"}
PROFIL = {"e-Defter yevmiye": "edefter_yevmiye", "e-Defter kebir": "edefter_kebir"}
ETIKET = {"edefter_yevmiye": "e-Defter yevmiye defteri", "edefter_kebir": "e-Defter defteri kebir"}
BELGE = {"edefter_yevmiye": "yevmiye", "edefter_kebir": "kebir"}
KOLONLAR = ["Madde no", "Fiş no", "Tarih", "Hesap kodu", "Hesap adı", "Açıklama", "Borç", "Alacak", "Belge no", "Fiş türü"]
ZIP_SINIR = 300 * 1024 * 1024  # açılmış toplam boyut; sıkıştırma bombasına karşı


class EDefterHatasi(ValueError):
    pass


def _ad(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _metin(el, ad: str) -> str | None:
    for c in el.iter():
        if _ad(c.tag) == ad and c.text and c.text.strip():
            return c.text.strip()
    return None


def _cocuk(el, ad: str):
    return next((c for c in el if _ad(c.tag) == ad), None)


def _tarih(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s.strip()[:10])
    except ValueError:
        return None


def _kurus(s: str | None) -> int:
    if not s:
        return 0
    try:
        return int((Decimal(s.strip().replace(",", ".")) * 100).quantize(Decimal(1)))
    except InvalidOperation:
        raise EDefterHatasi(f"Tutar okunamadı: {s!r}")


def _taraf(s: str | None) -> str | None:
    f = fold(s)
    if f in ("d", "debit", "borc", "b"):
        return "borc"
    if f in ("c", "credit", "alacak", "a"):
        return "alacak"
    return None


def _fis_turu(aciklama: str | None) -> str | None:
    f = fold(aciklama)
    if f.startswith("acilis") or ("acilis" in f.split() and "kaydi" in f.split()):
        return "AÇILIŞ"
    if f.startswith("kapanis"):
        return "KAPANIŞ"
    return None


def xml_dosyalari(path: Path) -> list[tuple[str, bytes]]:
    """Dosyadan e-Defter XML içeriklerini çıkarır (.xml tek dosya, .zip içindeki .xml'ler)."""
    data = path.read_bytes()
    if path.suffix.lower() == ".xml":
        return [(path.name, data)]
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise EDefterHatasi("ZIP dosyası açılamadı.")
    out, toplam = [], 0
    for info in zf.infolist():
        if info.is_dir() or not info.filename.lower().endswith(".xml"):
            continue
        toplam += info.file_size
        if toplam > ZIP_SINIR:
            raise EDefterHatasi("ZIP içeriği çok büyük.")
        out.append((Path(info.filename).name, zf.read(info)))
    return out


def _bilgi(root) -> dict:
    info = {}
    for el in root.iter():
        ad = _ad(el.tag)
        if ad == "documentInfo":
            info["tur"] = (_metin(el, "entriesType") or "").lower()
            bas, bit = _tarih(_metin(el, "periodCoveredStart")), _tarih(_metin(el, "periodCoveredEnd"))
            if bas and bit:
                info["tarih_araligi"] = [bas.isoformat(), bit.isoformat()]
        elif ad == "entityInformation":
            vkn = _metin(el, "organizationIdentifier")
            unvan = _metin(el, "organizationDescription")
            if vkn:
                info["vkn"] = vkn
            if unvan:
                info["firma"] = unvan[:256]
            break
    return info


def okuyucu_sayfalari(path: Path):
    """read_tables için: e-Defter dosyasını satırlara çevirir. Defter içermeyen XML'ler (berat vb.) atlanır."""
    from .tables import Sheet

    sheets = []
    for ad, data in xml_dosyalari(path):
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            continue
        if not any(_ad(el.tag) == "entryHeader" for el in root.iter()):
            continue
        info = _bilgi(root)
        tur = info.get("tur") if info.get("tur") in SHEET_ADI else "journal"
        rows = [(1, ["E-DEFTER", tur, info.get("vkn"), info.get("firma"),
                     *(info.get("tarih_araligi") or [None, None]), ad]),
                (2, list(KOLONLAR))]
        n = 2
        for h in (el for el in root.iter() if _ad(el.tag) == "entryHeader"):
            madde = fis = None
            if (e := _cocuk(h, "entryNumber")) is not None and e.text:
                fis = e.text.strip()
            if (e := _cocuk(h, "entryNumberCounter")) is not None and e.text:
                madde = e.text.strip()
            h_tarih = _cocuk(h, "enteredDate")
            h_tarih = h_tarih.text.strip() if h_tarih is not None and h_tarih.text else None
            h_acik = _cocuk(h, "entryComment")
            h_acik = h_acik.text.strip() if h_acik is not None and h_acik.text else None
            for d in (c for c in h if _ad(c.tag) == "entryDetail"):
                hesap = _cocuk(d, "account")
                ana = _metin(hesap, "accountMainID") if hesap is not None else None
                ana_ad = _metin(hesap, "accountMainDescription") if hesap is not None else None
                alt = alt_ad = None
                if hesap is not None and (sub := _cocuk(hesap, "accountSub")) is not None:
                    alt, alt_ad = _metin(sub, "accountSubID"), _metin(sub, "accountSubDescription")
                kod = alt or ana
                tutar = _metin(d, "amount")
                taraf = _taraf(_metin(d, "debitCreditCode"))
                tarih = _metin(d, "postingDate") or h_tarih
                acik = _metin(d, "detailComment") or h_acik
                kurus = _kurus(tutar)
                n += 1
                rows.append((n, [madde, fis, _tarih(tarih) or tarih, kod, alt_ad or ana_ad, acik,
                                 kurus if taraf == "borc" else None,
                                 kurus if taraf == "alacak" else None,
                                 _metin(d, "documentNumber"), _fis_turu(h_acik) or _fis_turu(acik)]))
        sheets.append(Sheet(SHEET_ADI[tur], rows))
    if not sheets:
        raise EDefterHatasi("Dosyada e-Defter yevmiye veya kebir kaydı bulunamadı (berat dosyası olabilir).")
    return sheets


# ---- Luca okuyucusuyla aynı arayüz -------------------------------------------

def sniff(sheets):
    for sh in sheets:
        if sh.name in PROFIL and sh.rows and sh.rows[0][1][:1] == ["E-DEFTER"]:
            return PROFIL[sh.name], sh
    return None


def info(sheet) -> dict:
    vals = sheet.rows[0][1] if sheet.rows else []
    out = {}
    if len(vals) > 5 and vals[4] and vals[5]:
        out["tarih_araligi"] = [vals[4], vals[5]]
    if len(vals) > 3 and vals[3]:
        out["firma"] = vals[3]
    if len(vals) > 2 and vals[2]:
        out["vkn"] = vals[2]
    return out


def parse(profile: str, sheet, out):
    """Satırları kayda çevirir; madde bazında borç = alacak denetlenir."""
    from ..parsing import code_segments, kurus_str
    from .luca import ParsedRow

    maddeler: dict[str, list[int]] = {}
    for row_no, vals in sheet.rows:
        if row_no <= 2:
            out.rows.append(ParsedRow(row_no, vals, "bilgi" if row_no == 1 else "baslik"))
            continue
        madde, fis, tarih, kod, ad, acik, borc, alacak, evrak, fis_turu = vals[:10]
        hata = None
        if not isinstance(tarih, date):
            hata = "Tarih okunamadı"
        elif not kod:
            hata = "Hesap kodu yok"
        elif borc is None and alacak is None:
            hata = "Borç/alacak tarafı belirlenemedi"
        if hata:
            out.rows.append(ParsedRow(row_no, vals, "hatali", note=hata))
            out.errors.append(f"Satır {row_no - 2} (madde {madde or fis or '?'}): {hata.lower()}.")
            continue
        if len(code_segments(kod)) == 1 and profile == "edefter_yevmiye":
            out.warnings.append(f"Satır {row_no - 2}: {kod} yalnızca ana hesap koduyla kayıtlı (alt hesap yok).")
        anahtar = madde or fis or f"satir{row_no}"
        t = maddeler.setdefault(anahtar, [0, 0])
        t[0] += borc or 0
        t[1] += alacak or 0
        out.rows.append(ParsedRow(row_no, vals, "kayit", record={
            "fis_no": fis or madde or "?", "madde_no": madde, "fis_tipi": fis_turu, "evrak_no": evrak, "tarih": tarih,
            "hesap_kodu": kod, "hesap_adi": ad, "aciklama": acik, "borc": borc or 0, "alacak": alacak or 0}))
    if profile == "edefter_yevmiye":
        for m, (b, a) in maddeler.items():
            if b != a:
                out.errors.append(f"Madde {m}: borç {kurus_str(b)} / alacak {kurus_str(a)} eşit değil.")
