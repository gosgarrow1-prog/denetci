"""Dosyayı satırlara çevirir; başlık satırını, belge türünü ve kolon eşlemesini önerir."""

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path

from openpyxl import load_workbook

from ..parsing import fold, is_empty, parse_account_code


@dataclass
class Sheet:
    name: str
    rows: list[tuple[int, list]]  # (dosyadaki satır no, hücre değerleri)
    merged_ranges: list[str] = field(default_factory=list)


class UnsupportedFile(ValueError):
    pass


def read_tables(path: Path) -> list[Sheet]:
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        return _read_xlsx(path)
    if suffix == ".csv":
        return [_read_csv(path)]
    raise UnsupportedFile("Bu dosya türü desteklenmiyor. Luca'dan .xlsx veya .csv olarak dışa aktarın.")


def _read_xlsx(path: Path) -> list[Sheet]:
    with open(path, "rb") as fh:
        if fh.read(2) != b"PK":
            raise UnsupportedFile("Dosya geçerli bir .xlsx değil.")
    # data_only: formüllerin son hesaplanan değeri okunur. Makrolar hiçbir zaman çalıştırılmaz.
    wb = load_workbook(path, data_only=True, read_only=False)
    sheets = []
    for ws in wb.worksheets:
        rows = []
        for idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
            values = list(row)
            while values and is_empty(values[-1]):
                values.pop()
            rows.append((idx, values))
        sheets.append(Sheet(ws.title, rows, [str(r) for r in ws.merged_cells.ranges]))
    wb.close()
    return sheets


def _read_csv(path: Path) -> Sheet:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1254", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";"
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = [(i, [c if c.strip() != "" else None for c in r]) for i, r in enumerate(reader, start=1)]
    return Sheet("CSV", rows)


# ---- Kolon eşleme -----------------------------------------------------------

FIELD_DEFS: dict[str, tuple[str, list[str]]] = {
    "hesap_kodu": ("Hesap kodu", ["hesap kodu", "hesap no", "hesap numarasi", "kod", "hesap"]),
    "hesap_adi": ("Hesap adı", ["hesap adi", "hesap ismi", "hesap tanimi"]),
    "acilis_borc": ("Açılış borç", ["acilis borc", "acilis borcu", "devir borc", "onceki donem borc"]),
    "acilis_alacak": ("Açılış alacak", ["acilis alacak", "acilis alacagi", "devir alacak", "onceki donem alacak"]),
    "borc": ("Borç (dönem)", ["borc", "borc tutari", "donem borc", "toplam borc", "borc toplami", "hareket borc"]),
    "alacak": ("Alacak (dönem)", ["alacak", "alacak tutari", "donem alacak", "toplam alacak", "alacak toplami", "hareket alacak"]),
    "bakiye_borc": ("Borç bakiye", ["borc bakiye", "borc bakiyesi", "bakiye borc", "b bakiye"]),
    "bakiye_alacak": ("Alacak bakiye", ["alacak bakiye", "alacak bakiyesi", "bakiye alacak", "a bakiye"]),
    "fis_no": ("Fiş no", ["fis no", "fis numarasi", "yevmiye no", "madde no", "fis"]),
    "tarih": ("Tarih", ["tarih", "fis tarihi", "islem tarihi"]),
    "aciklama": ("Açıklama", ["aciklama", "fis aciklamasi", "satir aciklamasi"]),
}

DOC_FIELDS = {
    "mizan": ["hesap_kodu", "hesap_adi", "acilis_borc", "acilis_alacak", "borc", "alacak", "bakiye_borc", "bakiye_alacak"],
    "yevmiye": ["fis_no", "tarih", "hesap_kodu", "hesap_adi", "aciklama", "borc", "alacak"],
}
REQUIRED = {
    "mizan": ["hesap_kodu", "borc", "alacak"],
    "yevmiye": ["fis_no", "tarih", "hesap_kodu", "borc", "alacak"],
}
DOC_LABELS = {"mizan": "Mizan", "yevmiye": "Yevmiye defteri", "kebir": "Defteri kebir", "muavin": "Muavin defter"}

_SYNONYM_TO_FIELD = {syn: f for f, (_, syns) in FIELD_DEFS.items() for syn in syns}


def match_header(cell) -> str | None:
    return _SYNONYM_TO_FIELD.get(fold(cell))


def header_mapping(values: list) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, cell in enumerate(values):
        f = match_header(cell)
        if f and f not in mapping:
            mapping[f] = idx
    return mapping


@dataclass
class Detection:
    sheet: str
    header_row: int  # dosyadaki satır no
    mapping: dict[str, int]
    doc_type: str | None
    header_values: list


def detect(sheets: list[Sheet]) -> Detection | None:
    best = None
    for sheet in sheets:
        for row_no, values in sheet.rows[:40]:
            mapping = header_mapping(values)
            score = len(mapping)
            if score >= 3 and (best is None or score > best[0]):
                best = (score, sheet, row_no, mapping, values)
    if best is None:
        return None
    _, sheet, row_no, mapping, values = best
    return Detection(sheet.name, row_no, mapping, guess_doc_type(mapping), list(values))


def guess_doc_type(mapping: dict) -> str | None:
    if "tarih" in mapping and "fis_no" in mapping:
        return "yevmiye"
    if "bakiye_borc" in mapping or "bakiye_alacak" in mapping or "acilis_borc" in mapping:
        return "mizan"
    if "hesap_kodu" in mapping and "borc" in mapping:
        return "mizan"
    return None


# ---- Satır sınıflandırma ----------------------------------------------------

_SUBTOTAL_WORDS = ("toplam", "yekun", "nakli", "devreden", "sayfa toplami")


def _cell(values, idx):
    if idx is None or idx >= len(values):
        return None
    return values[idx]


def classify_row(values: list, mapping: dict[str, int]) -> tuple[str, str | None]:
    """Satırı kayit / ara_toplam / baslik / bos / belirsiz olarak sınıflar. (sınıf, hesap_kodu)"""
    if all(is_empty(v) for v in values):
        return "bos", None
    if len(header_mapping(values)) >= 3:
        return "baslik", None
    for v in values:
        if isinstance(v, str) and any(w in fold(v) for w in _SUBTOTAL_WORDS):
            return "ara_toplam", None
    code, _ = parse_account_code(_cell(values, mapping.get("hesap_kodu")))
    amount_idx = [mapping[k] for k in ("borc", "alacak") if k in mapping]
    has_amount = any(not is_empty(_cell(values, i)) for i in amount_idx)
    if code:
        # Tek veya iki haneli kodlar hesap sınıfı/grup satırıdır (ör. "1 DÖNEN VARLIKLAR"), kayıt değildir.
        if len(code.replace(".", "").replace("-", "")) < 3:
            return "ara_toplam", None
        return "kayit", code
    if has_amount:
        return "belirsiz", None
    return "bilgi", None
