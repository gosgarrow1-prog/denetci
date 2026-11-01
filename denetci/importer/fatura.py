"""e-Fatura / e-Arşiv fatura listesi (Excel) okuyucusu.

Bir satır bir faturadır. Beklenen başlıklar: Fatura Tarihi, Fatura Numarası, Fatura Türü, VKN/TCKN, Açıklama
(karşı taraf), Tevkifat Oranı, Özel Matrah, oranlara göre KDV (%1'lik KDV ...) ve matrah (%1'lik Matrah ...), Toplam.
Okuyucu Burak'ın gönderdiği liste biçimine göre yazıldı; başlıklar sadeleştirilerek karşılaştırılır.
Tutarlar kuruştur. Listenin gelen mi giden mi olduğu dosyada yazmaz; yükleme sırasında defterle eşleşmeden bulunur.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from ..parsing import fold

ORAN_RE = re.compile(r"^(\d{1,2})\s*l[iu]k\s+(kdv|matrah)\b")  # "%20'lik KDV" sadeleşince "20 lik kdv"


@dataclass
class Fatura:
    tarih: date
    no: str
    tip: str | None
    vkn: str | None
    unvan: str | None
    tevkifat: str | None
    ozel_matrah: int
    kdv: dict = field(default_factory=dict)  # oran -> kuruş
    matrah: dict = field(default_factory=dict)
    toplam: int = 0
    satir: int = 0

    @property
    def kdv_toplam(self) -> int:
        return sum(self.kdv.values())

    @property
    def matrah_toplam(self) -> int:
        return sum(self.matrah.values()) + self.ozel_matrah


def _kurus(v) -> int:
    if v is None or v == "":
        return 0
    if isinstance(v, (int, float)):
        return round(v * 100)
    s = str(v).strip().replace(".", "").replace(",", ".")
    try:
        return round(float(s) * 100)
    except ValueError:
        return 0


def _tarih(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    m = re.match(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", str(v or "").strip())
    return date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


def baslik(values) -> dict | None:
    """Başlık satırıysa kolon eşlemesini döner."""
    cols = {}
    for i, v in enumerate(values):
        f = " ".join(fold(str(v or "")).split())
        if not f:
            continue
        m = ORAN_RE.search(f)
        if m:
            cols[(m.group(2), int(m.group(1)))] = i
        elif f.startswith("fatura tarihi"):
            cols["tarih"] = i
        elif f.startswith("fatura numarasi") or f.startswith("fatura no"):
            cols["no"] = i
        elif f.startswith("fatura turu") or f.startswith("fatura tipi"):
            cols["tip"] = i
        elif f.startswith("vkn") or f.startswith("tckn"):
            cols["vkn"] = i
        elif f.startswith("aciklama") or f.startswith("unvan"):
            cols["unvan"] = i
        elif f.startswith("tevkifat"):
            cols["tevkifat"] = i
        elif f.startswith("ozel matrah"):
            cols["ozel"] = i
        elif f.startswith("toplam"):
            cols["toplam"] = i
    if "tarih" in cols and "no" in cols and any(isinstance(k, tuple) for k in cols):
        return cols
    return None


def sniff(sheets) -> tuple[object, int, dict] | None:
    """(sayfa, başlık satır sırası, kolonlar) veya None."""
    for sh in sheets:
        for idx, (_, vals) in enumerate(sh.rows[:15]):
            cols = baslik(vals)
            if cols:
                return sh, idx, cols
    return None


def parse(sheet, idx: int, cols: dict) -> list[Fatura]:
    out = []
    for row_no, vals in sheet.rows[idx + 1:]:
        def g(k):
            i = cols.get(k)
            return vals[i] if i is not None and i < len(vals) else None
        t, no = _tarih(g("tarih")), str(g("no") or "").strip()
        if not t or not no:
            continue
        vkn = g("vkn")
        if isinstance(vkn, float):
            vkn = str(int(vkn))
        f = Fatura(t, no, (str(g("tip") or "").strip() or None), (str(vkn).strip() if vkn else None),
                   (str(g("unvan") or "").strip() or None), (str(g("tevkifat") or "").strip() or None),
                   _kurus(g("ozel")), toplam=_kurus(g("toplam")), satir=row_no)
        for k, i in cols.items():
            if isinstance(k, tuple):
                v = _kurus(vals[i] if i < len(vals) else None)
                if v:
                    (f.kdv if k[0] == "kdv" else f.matrah)[k[1]] = v
        out.append(f)
    return out
