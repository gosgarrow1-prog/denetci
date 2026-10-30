"""GİB beyanname PDF'lerinin okunması: KDV beyannamesi (KDV1) ve geçici vergi beyannamesi.

Beyanname sisteminden indirilen PDF'ler metin içerir (taranmış değildir). Metin, satır düzeni korunarak çıkarılır;
her satır "Etiket   tutar" biçimindedir. Etiketler formdaki adlarıyla aranır; bulunamayan alan boş kalır.
Bu okuyucu 2026 Temmuz KDV1 ve 2026/2. dönem kurumlar geçici vergi beyannamesi örnekleriyle yazıldı;
form değişirse etiketlerin güncellenmesi gerekebilir.
"""

import io
import re
from dataclasses import dataclass, field
from datetime import date
from calendar import monthrange

from ..parsing import fold

AYLAR = ["ocak", "subat", "mart", "nisan", "mayis", "haziran", "temmuz", "agustos", "eylul", "ekim", "kasim", "aralik"]

# alan anahtarı -> formdaki etiket (fold edilmiş karşılaştırma yapılır)
KDV_ALANLAR = {
    "matrah_toplami": "Matrah Toplamı",
    "hesaplanan": "Hesaplanan Katma Değer Vergisi",
    "ilave": "Daha Önce İndirim Konusu Yapılan KDV’nin İlavesi",
    "toplam_kdv": "Toplam Katma Değer Vergisi",
    "onceki_devreden": "Önceki Dönemden Devreden",
    "yurtici_alim": "Yurtiçi Alımlara İlişkin KDV",
    "sorumlu": "Sorumlu Sıfatıyla Beyan Edilerek Ödenen KDV",
    "indirimler_toplami": "İndirimler Toplamı",
    "odenecek": "Bu Dönemde Ödenmesi Gereken Katma Değer Vergisi",
    "odenmesi_gereken": "Ödenmesi Gereken Katma Değer Vergisi",
    "iade": "İade Edilmesi Gereken Katma Değer Vergisi",
    "tecil": "Tecil Edilecek Katma Değer Vergisi",
    "sonraki_devreden": "Sonraki Döneme Devreden Katma Değer Vergisi",
    "teslim_aylik": "Teslim ve Hizmetlerin Karşılığını Teşkil Eden Bedel (aylık)",
}

GV_ALANLAR = {
    "ticari_kar": "Ticari Bilanço Karı",
    "ticari_zarar": "Ticari Bilanço Zararı",
    "kkeg": "Kanunen Kabul Edilmeyen Gider",
    "toplam_gecmis_zarar": "Toplam Geçmiş Yıl Zararları",
    "gecmis_zarar": "Mahsup Edilecek Geçmiş Yıl Zararları",
    "zarar_olsa_dahi": "Zarar Olsa Dahi İndirilecek İstisna ve İndirimler",
    "matrah": "Geçici Vergi Matrahı",
    "hesaplanan": "Hesaplanan Geçici Vergi",
    "nihai": "Nihai Hesaplanan Geçici Vergi",
    "asgari": "Hesaplanan Yurt İçi Asgari Geçici Vergi",
    "onceki": "Önceki Dönemlerde Hesaplanan Geçici Vergi",
    "tevkifat": "Mahsup Edilecek Tevkifat Tutarı",
    "odenecek": "Ödenecek Geçici Vergi",
    "odenmesi_gereken": "Ödenmesi Gereken Geçici Vergi",
    "sonraki_devreden": "Sonraki Döneme Devreden Hesaplanan Geçici Vergi",
    "net_satis": "C.Net Satışlar",
    "donem_kari": "Dönem Karı veya Zararı",
}

TUR_ADLARI = {"kdv": "KDV beyannamesi", "gecici": "Geçici vergi beyannamesi"}

AMOUNT_RE = re.compile(r"^(.*?\S)\s{2,}(-?\d{1,3}(?:\.\d{3})*,\d{2})\s*$")


class BeyannameHatasi(ValueError):
    pass


@dataclass
class Beyanname:
    tur: str  # kdv, gecici
    alt: str | None  # gecici: kurum / sahis
    yil: int
    ay: int  # KDV: beyan ayı; geçici vergi: dönem sonu ayı (3, 6, 9, 12)
    vkn: str | None
    unvan: str | None
    vergi_dairesi: str | None
    onay: str | None
    duzeltme: bool
    alanlar: dict = field(default_factory=dict)  # kuruş

    @property
    def bitis(self) -> date:
        return date(self.yil, self.ay, monthrange(self.yil, self.ay)[1])


def _kurus(s: str) -> int:
    neg = s.startswith("-")
    digits = s.lstrip("-").replace(".", "").replace(",", "")
    return -int(digits) if neg else int(digits)


def metin(content: bytes) -> list[str]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # kurulumda eksikse
        raise BeyannameHatasi("PDF okuyucu (pypdf) kurulu değil. Denetçi'yi kapatıp yeniden başlatın; "
                              "eksik parça kendiliğinden kurulur.") from exc
    try:
        reader = PdfReader(io.BytesIO(content))
        lines = []
        for page in reader.pages:
            lines += page.extract_text(extraction_mode="layout").splitlines()
    except Exception as exc:
        raise BeyannameHatasi("PDF okunamadı. Dosya bozuk veya şifreli olabilir.") from exc
    return [ln.rstrip() for ln in lines if ln.strip()]


def _tutarlar(lines: list[str]) -> dict:
    """Etiket (fold, tek boşluk) -> ilk görülen tutar (kuruş)."""
    out = {}
    for ln in lines:
        m = AMOUNT_RE.match(ln)
        if m:
            label = " ".join(fold(m.group(1)).split())
            out.setdefault(label, _kurus(m.group(2)))
    return out


def _alanlar(tutarlar: dict, tanim: dict) -> dict:
    out = {}
    for key, label in tanim.items():
        v = tutarlar.get(" ".join(fold(label).split()))
        if v is not None:
            out[key] = v
    return out


_TR = str.maketrans("İıŞşĞğÜüÖöÇç", "IiSsGgUuOoCc")


def _bul(lines, pattern):
    """Desen Türkçe karakterler ASCII'ye çevrilmiş satırda aranır (karakter sayısı aynı kalır);
    grup 1 özgün satırdan döner."""
    rx = re.compile(pattern)
    for ln in lines:
        m = rx.search(ln.translate(_TR))
        if m:
            return ln[m.start(1):m.end(1)]
    return None


def _unvan(lines) -> str | None:
    def field_(label):
        v = _bul(lines, rf"{label}\s{{2,}}(.+?)(?:\s{{2,}}|$)")
        return v.strip() if v else ""
    a, b = field_(r"Soyadi \(Unvani\)"), field_(r"Adi \(Unvanin Devami\)")
    if not a:
        return None
    # Unvan formda 30 karakterlik iki alana bölünür; ilk alan doluysa kelime ortadan bölünmüştür.
    return (a + b if len(a) >= 30 else f"{a} {b}").strip()


def parse(content: bytes) -> Beyanname:
    return parse_lines(metin(content))


def parse_lines(lines: list[str]) -> Beyanname:
    if not lines:
        raise BeyannameHatasi("PDF'te metin bulunamadı (taranmış belge olabilir). Beyannameyi GİB sisteminden PDF olarak indirin.")
    head = fold(" ".join(lines[:12]))
    if "katma deger vergisi beyannamesi" in head:
        tur, alt = "kdv", None
    elif "gecici vergi beyannamesi" in head:
        tur = "gecici"
        alt = "kurum" if "kurumlar vergisi" in head else ("sahis" if "gelir vergisi" in head else None)
    else:
        raise BeyannameHatasi("Bu PDF tanınan bir beyanname değil. Şimdilik KDV ve geçici vergi beyannameleri okunuyor.")

    m = _bul(lines, r"Yili?\s{2,}(\d{4})")
    if not m:
        raise BeyannameHatasi("Beyannamede yıl bulunamadı.")
    yil = int(m)
    if tur == "kdv":
        if "3 aylik" in head or "uc aylik" in head:
            raise BeyannameHatasi("Üç aylık KDV beyannamesi henüz desteklenmiyor.")
        m = _bul(lines, r"\bAy\s{2,}(\S+)")
        ay_adi = fold(m) if m else ""
        if ay_adi not in AYLAR:
            raise BeyannameHatasi("KDV beyannamesinde ay bulunamadı.")
        ay = AYLAR.index(ay_adi) + 1
    else:
        m = _bul(lines, r"Donem\s{2,}(\d)\.\s*Donem")
        if not m or not 1 <= int(m) <= 4:
            raise BeyannameHatasi("Geçici vergi beyannamesinde dönem bulunamadı.")
        ay = int(m) * 3

    vkn = _bul(lines, r"Vergi Kimlik Numarasi\s{2,}(\d{10,11})\b")
    vd = _bul(lines, r"^\s*(.+?)\s+VD\b")
    onay = _bul(lines, r"Onay Zamani\s*:\s*([\d.]+\s*-\s*[\d:]+)")
    alanlar = _alanlar(_tutarlar(lines), KDV_ALANLAR if tur == "kdv" else GV_ALANLAR)
    need = ("sonraki_devreden", "toplam_kdv") if tur == "kdv" else ("matrah", "hesaplanan")
    if not all(k in alanlar for k in need):
        raise BeyannameHatasi("Beyannamedeki tutarlar okunamadı. Form biçimi değişmiş olabilir.")
    return Beyanname(tur, alt, yil, ay, vkn, _unvan(lines), vd.strip() if vd else None,
                     " ".join(onay.split()) if onay else None,
                     "duzeltme" in head, alanlar)


def peek(content: bytes) -> dict:
    """Yükleme öncesi firma kontrolü için: {"firma": unvan, "vkn": ...}; okunamazsa {}."""
    try:
        b = parse(content)
    except BeyannameHatasi:
        return {}
    return {"firma": b.unvan, "vkn": b.vkn}


def donem_adi(tur: str, yil: int, ay: int) -> str:
    if tur == "gecici":
        return f"{yil} / {ay // 3}. dönem"
    return f"{['Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran', 'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık'][ay - 1]} {yil}"
