"""Türkçe sayı, tarih ve metin okuma yardımcıları."""

import re
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


class ParseError(ValueError):
    pass


_TR_FOLD = str.maketrans({"ı": "i", "ş": "s", "ğ": "g", "ü": "u", "ö": "o", "ç": "c", "â": "a", "î": "i", "û": "u"})


def fold(text) -> str:
    """Karşılaştırma için metni sadeleştirir: küçük harf, Türkçe karakterler ASCII, noktalama yok."""
    if text is None:
        return ""
    s = str(text).replace("İ", "i").replace("I", "ı").lower().translate(_TR_FOLD)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def is_empty(value) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


# ---- Tutarlar ---------------------------------------------------------------

_EMPTY_AMOUNTS = {"", "-", "—", "–"}


def _clean_amount_text(s: str) -> str:
    return s.replace(" ", "").replace(" ", "").replace("TL", "").replace("₺", "").strip()


def detect_number_format(samples) -> str:
    """Dosyadaki metin tutarlara bakarak biçimi belirler.
    'tr' = 1.234.567,89 ; 'en' = 1,234,567.89. Varsayılan 'tr'."""
    tr = en = 0
    for v in samples:
        if not isinstance(v, str):
            continue
        s = _clean_amount_text(v).strip("()-")
        if re.search(r",\d{1,2}$", s):
            tr += 1
        elif re.search(r"\.\d{1,2}$", s):
            en += 1
        elif re.search(r"\.\d{3}$", s):
            tr += 1
        elif re.search(r",\d{3}$", s):
            en += 1
    return "en" if en > tr else "tr"


def parse_amount(value, fmt: str = "tr") -> int | None:
    """Tutarı kuruş cinsinden tam sayıya çevirir. Boş hücre için None döner.
    Negatif gösterimler: -1.234,56 / (1.234,56) / 1.234,56-"""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ParseError("Tutar yerine mantıksal değer")
    if isinstance(value, (int, float, Decimal)):
        d = Decimal(str(value))
    else:
        s = _clean_amount_text(str(value))
        if s in _EMPTY_AMOUNTS:
            return None
        negative = False
        if s.startswith("(") and s.endswith(")"):
            negative, s = True, s[1:-1]
        if s.endswith("-"):
            negative, s = True, s[:-1]
        if s.startswith("-"):
            negative, s = not negative, s[1:]
        if s.startswith("+"):
            s = s[1:]
        if fmt == "tr":
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
        if not re.fullmatch(r"\d+(\.\d+)?", s):
            raise ParseError(f"Geçersiz tutar: {value!r}")
        try:
            d = Decimal(s)
        except InvalidOperation as exc:
            raise ParseError(f"Geçersiz tutar: {value!r}") from exc
        if negative:
            d = -d
    return int((d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * 100).to_integral_value())


def kurus_str(kurus: int | None) -> str:
    """Kuruşu Türkçe biçimde yazar: 4825000 -> '48.250,00'."""
    if kurus is None:
        return "—"
    sign = "-" if kurus < 0 else ""
    k = abs(int(kurus))
    lira, kr = divmod(k, 100)
    return f"{sign}{lira:,}".replace(",", ".") + f",{kr:02d}"


# ---- Tarihler ---------------------------------------------------------------

_DATE_FORMATS = ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%y", "%d/%m/%y")


def parse_date(value) -> date | None:
    if is_empty(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and 20000 < value < 80000:
        return date(1899, 12, 30) + timedelta(days=int(value))  # Excel seri tarihi
    s = str(value).strip().split(" ")[0]
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ParseError(f"Geçersiz tarih: {value!r}")


# ---- Hesap kodları ----------------------------------------------------------

_CODE_RE = re.compile(r"^\d{1,4}([.\- ]\d+)*$")


def parse_account_code(value) -> tuple[str | None, str | None]:
    """Hesap kodunu metin olarak döner. İkinci değer varsa bir uyarıdır.
    Excel kodu sayıya çevirdiyse baştaki/sondaki sıfırlar kaybolmuş olabilir."""
    if is_empty(value):
        return None, None
    if isinstance(value, bool):
        return None, None
    if isinstance(value, int):
        return str(value), None
    if isinstance(value, float):
        text = repr(value).rstrip("0").rstrip(".") if not value.is_integer() else str(int(value))
        return text, "Hesap kodu Excel'de sayı olarak saklanmış; sondaki sıfırlar kaybolmuş olabilir"
    s = str(value).strip().replace(" ", "")
    if _CODE_RE.fullmatch(s):
        return s, None
    return None, None


def code_segments(code: str) -> list[str]:
    return [p for p in re.split(r"[.\- ]", code) if p]
