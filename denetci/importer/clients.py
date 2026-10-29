"""Mükellef listesi aktarımı (Luca'dan alınan firma/mükellef listesi, .xlsx veya .csv).

Luca'nın liste düzeni henüz örnek dosyayla doğrulanmadı; başlıklar eş anlamlılarla tanınır.
Aynı mükellef ikinci kez eklenmez: önce VKN/TCKN, yoksa sadeleştirilmiş unvan karşılaştırılır.
"""

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..parsing import fold, is_empty
from .tables import read_tables

ALANLAR = {
    "unvan": ("unvan", "unvani", "firma adi", "firma unvani", "firma", "mukellef", "mukellef adi", "adi soyadi unvani",
              "ad soyad unvan", "musteri adi", "sirket adi", "ticaret unvani", "adi unvani"),
    "vkn": ("vkn", "vergi no", "vergi numarasi", "vergi kimlik no", "vergi kimlik numarasi", "tckn", "tc kimlik no",
            "tc no", "vkn tckn", "tckn vkn", "vergi tc kimlik no"),
    "vergi_dairesi": ("vergi dairesi", "vd", "vergi dairesi adi"),
    "luca_kodu": ("firma kodu", "kod", "firma no", "sirket kodu", "mukellef kodu", "musteri kodu"),
    "sirket_turu": ("sirket turu", "sirket tipi", "firma turu", "firma tipi", "mukellefiyet turu", "tur"),
    "faaliyet": ("faaliyet", "faaliyet alani", "faaliyet konusu", "nace", "sektor"),
}


@dataclass
class ClientRow:
    row_no: int
    unvan: str
    vkn: str | None = None
    vergi_dairesi: str | None = None
    luca_kodu: str | None = None
    sirket_turu: str | None = None
    faaliyet: str | None = None
    durum: str = "yeni"  # yeni, var, tekrar


class ListError(ValueError):
    pass


def _clean_vkn(v) -> str | None:
    if is_empty(v):
        return None
    s = str(v).strip()
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".")[0]
    digits = re.sub(r"\D", "", s)
    return digits if len(digits) in (10, 11) else (s or None)


def _txt(v):
    return None if is_empty(v) else str(v).strip()


def unvan_key(unvan: str) -> str:
    return fold(unvan).replace(" ", "")


def parse_client_list(filename: str, content: bytes) -> list[ClientRow]:
    suffix = Path(filename).suffix.lower()
    if suffix not in (".xlsx", ".csv"):
        raise ListError("Mükellef listesi .xlsx veya .csv olmalı.")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"liste{suffix}"
        path.write_bytes(content)
        sheets = read_tables(path)
    for sheet in sheets:
        for idx, (row_no, vals) in enumerate(sheet.rows[:30]):
            cols = {}
            for i, v in enumerate(vals):
                f = fold(v)
                for field, names in ALANLAR.items():
                    if field not in cols and f in names:
                        cols[field] = i
            if "unvan" in cols:
                return _rows(sheet.rows[idx + 1:], cols)
    raise ListError("Listede unvan kolonu bulunamadı. Başlık satırında 'Unvan' veya 'Firma Adı' gibi bir kolon olmalı.")


def _rows(rows, cols) -> list[ClientRow]:
    out = []
    for row_no, vals in rows:
        get = lambda f: vals[cols[f]] if f in cols and cols[f] < len(vals) else None  # noqa: E731
        unvan = _txt(get("unvan"))
        if not unvan or fold(unvan).startswith(("toplam", "genel toplam")):
            continue
        out.append(ClientRow(row_no, unvan[:256], _clean_vkn(get("vkn")), _txt(get("vergi_dairesi")),
                             _txt(get("luca_kodu")), _txt(get("sirket_turu")), _txt(get("faaliyet"))))
    return out


def mark_existing(rows: list[ClientRow], existing) -> list[ClientRow]:
    """Var olan mükellefleri ve dosya içindeki tekrarları işaretler."""
    vkns = {c.vkn for c in existing if c.vkn}
    names = {unvan_key(c.unvan) for c in existing}
    seen = set()
    for r in rows:
        key = r.vkn or unvan_key(r.unvan)
        if (r.vkn and r.vkn in vkns) or unvan_key(r.unvan) in names:
            r.durum = "var"
        elif key in seen:
            r.durum = "tekrar"
        seen.add(key)
    return rows
