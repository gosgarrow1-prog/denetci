"""Luca dışa aktarımlarının okuyucuları (Excel).

Düzenler, Luca'dan alınmış örnek dosyalara göre yazıldı (29.10.2026):

Yevmiye defteri: her fiş bir blok.
    "00123-----00045-----MAHSUP-----01/09/2026"   madde no / fiş no / fiş tipi / tarih
    HESAP KODU | HESAP ADI | AÇIKLAMA | DETAY | BORÇ | ALACAK
    150            (ana hesap; tutar BORÇ veya ALACAK kolonunda -> tarafı belirler)
    150.01         (ara seviye; tutar DETAY kolonunda)
       150.01.010  (yaprak hesap, başında boşluk; tutar DETAY kolonunda)
    FİŞ AÇIKLAMA : ...
    TOPLAM : borç / alacak
  Yalnızca yaprak satırlar kayıt olarak alınır; ana ve ara seviye satırları toplamdır.

Defteri kebir: ana hesap grupları ("100 KASA"), "NAKLİ YEKÜN" satırı, tarihli satırlar, "Toplam:".
Muavin: hesap başlığı ("100.01.001 KASA ... TL"), "Nakli Yekün:", tarihli satırlar, "Genel Toplam".
"""

import re
from dataclasses import dataclass, field
from datetime import date

from ..parsing import ParseError, code_segments, fold, is_empty, kurus_str, parse_account_code, parse_amount, parse_date

FIS_RE = re.compile(r"^\s*(\d+)\s*-{3,}\s*(\d+)\s*-{3,}\s*([^-]+?)\s*-{3,}\s*(\d{1,2}[./]\d{1,2}[./]\d{4})")
RANGE_RE = re.compile(r"(\d{1,2}[./]\d{1,2}[./]\d{4})\s*-\s*(\d{1,2}[./]\d{1,2}[./]\d{4})")
GROUP_RE = re.compile(r"^\s*(\d{3})\s+\S")
MUAVIN_ACC_RE = re.compile(r"^\s*(\d{3}(?:[.\-]\d+)*)\s+\S")

PROFILE_LABELS = {
    "luca_yevmiye": "Luca yevmiye defteri",
    "luca_kebir": "Luca defteri kebir",
    "luca_muavin": "Luca muavin defter",
    "luca_mizan": "Luca mizan",
}
PROFILE_DOC = {"luca_yevmiye": "yevmiye", "luca_kebir": "kebir", "luca_muavin": "muavin", "luca_mizan": "mizan"}


@dataclass
class ParsedRow:
    row_no: int
    values: list
    cls: str
    note: str | None = None
    record: dict | None = None
    opening: dict | None = None
    tb: dict | None = None  # mizan satırı


@dataclass
class Parsed:
    profile: str
    doc_type: str
    rows: list[ParsedRow]
    info: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _v(values, i):
    return values[i] if i < len(values) else None


def _num_id(v) -> str | None:
    """Madde/fiş numarası: '00123' ve 123.0 aynı numaradır."""
    if is_empty(v):
        return None
    s = str(v).strip()
    if re.fullmatch(r"\d+(\.0+)?", s):
        return str(int(float(s)))
    return s


def _amt(v) -> int:
    return parse_amount(v, "tr") or 0


def sniff(sheets) -> tuple[str, object] | None:
    """Luca biçimini tanır. (profil, sayfa) döner."""
    for sheet in sheets:
        heads = [fold(" ".join(str(c) for c in vals if c is not None)) for _, vals in sheet.rows[:80]]
        firsts = [vals[0] if vals else None for _, vals in sheet.rows[:80]]
        if any(isinstance(a, str) and FIS_RE.match(a) for a in firsts) and any("detay" in h for h in heads):
            return "luca_yevmiye", sheet
        if any("madde no" in h and "fis no" in h and "bakiye" in h for h in heads):
            return "luca_kebir", sheet
        if any(h.startswith("tarih tip fis no aciklama borc alacak bakiye") for h in heads):
            return "luca_muavin", sheet
        if any(h.startswith("hesap kodu hesap adi") and "borc bakiyesi" in h and "alacak bakiyesi" in h for h in heads) \
                and any(h.startswith("tarih araligi") for h in heads):
            return "luca_mizan", sheet
    return None


def _info(rows) -> dict:
    info = {}
    for _, vals in rows[:12]:
        label = fold(_v(vals, 0))
        val = _v(vals, 1)
        m = RANGE_RE.search(str(val or ""))
        if label.startswith("tarih araligi") and m:
            info["tarih_araligi"] = [parse_date(m.group(1).replace("/", ".")).isoformat(), parse_date(m.group(2).replace("/", ".")).isoformat()]
        elif label.startswith("donem") and m:
            info["donem"] = [parse_date(m.group(1).replace("/", ".")).isoformat(), parse_date(m.group(2).replace("/", ".")).isoformat()]
    return info


def parse(profile: str, sheet) -> Parsed:
    fn = {"luca_yevmiye": _parse_yevmiye, "luca_kebir": _parse_kebir, "luca_muavin": _parse_muavin,
          "luca_mizan": _parse_mizan}[profile]
    out = Parsed(profile, PROFILE_DOC[profile], [])
    out.info = _info(sheet.rows)
    fn(sheet, out)
    return out


# ---- Yevmiye ----------------------------------------------------------------

def _parse_yevmiye(sheet, out: Parsed):
    fis = None  # o anki fiş bilgisi
    coded: list[ParsedRow] = []

    def flush():
        nonlocal coded
        if fis is None or not coded:
            coded = []
            return
        _finish_fis(fis, coded, out)
        coded = []

    for row_no, vals in sheet.rows:
        a = _v(vals, 0)
        fa = fold(a)
        if all(is_empty(v) for v in vals):
            out.rows.append(ParsedRow(row_no, vals, "bos"))
            continue
        m = FIS_RE.match(str(a)) if isinstance(a, str) else None
        if m:
            flush()
            fis = {"madde": _num_id(m.group(1)), "fis": _num_id(m.group(2)), "tip": m.group(3).strip().title(),
                   "tarih": parse_date(m.group(4).replace("/", ".")), "row_no": row_no, "toplam": None}
            out.rows.append(ParsedRow(row_no, vals, "baslik", note=f"Fiş başlığı: madde {fis['madde']}, fiş {fis['fis']}"))
            continue
        if fa == "hesap kodu":
            out.rows.append(ParsedRow(row_no, vals, "baslik"))
            continue
        if fa.startswith("fis aciklama"):
            if fis is not None:
                fis["aciklama"] = None if is_empty(_v(vals, 1)) else str(_v(vals, 1)).strip()
            out.rows.append(ParsedRow(row_no, vals, "bilgi"))
            continue
        if fa.startswith("toplam") or fa.startswith("fis toplami"):
            pr = ParsedRow(row_no, vals, "ara_toplam")
            out.rows.append(pr)
            if fis is not None:
                fis["toplam"] = (_amt(_v(vals, 4)), _amt(_v(vals, 5)), row_no)
            flush()
            continue
        if "nakli yekun" in fa or "nakli yekun" in fold(_v(vals, 2)):
            out.rows.append(ParsedRow(row_no, vals, "ara_toplam"))
            continue
        code, _ = parse_account_code(a)
        if code and fis is not None:
            pr = ParsedRow(row_no, vals, "ust_hesap")
            pr.record = {"_raw_code": str(a)}
            out.rows.append(pr)
            coded.append(pr)
            continue
        out.rows.append(ParsedRow(row_no, vals, "bilgi" if fis is None else "belirsiz"))
        if fis is not None:
            out.warnings.append(f"Satır {row_no}: fiş içinde tanınmayan satır; kayıt olarak alınmadı.")
    flush()


def _finish_fis(fis: dict, coded: list[ParsedRow], out: Parsed):
    codes = [parse_account_code(pr.values[0])[0] for pr in coded]
    side, ana_code, ana_amount, ana_sum = None, None, 0, 0
    leaf_b = leaf_a = 0

    def close_ana():
        if ana_code is not None and abs(ana_sum - ana_amount) > 0:
            out.errors.append(f"Madde {fis['madde']}: {ana_code} ana hesap tutarı {kurus_str(ana_amount)}, "
                              f"alt hesapların toplamı {kurus_str(ana_sum)}.")

    for i, pr in enumerate(coded):
        code = codes[i]
        vals = pr.values
        raw = pr.record.pop("_raw_code")
        pr.record = None
        if len(code_segments(code)) == 1:
            close_ana()
            b, a = _amt(_v(vals, 4)), _amt(_v(vals, 5))
            side = "borc" if b else ("alacak" if a else None)
            ana_code, ana_amount, ana_sum = code, (b or a), 0
            if side is None:
                out.warnings.append(f"Satır {pr.row_no}: {code} ana hesap satırında tutar yok.")
        nxt = codes[i + 1] if i + 1 < len(codes) else None
        has_child = nxt is not None and nxt.startswith(code + ".")
        is_leaf = raw.startswith(" ") or not has_child
        if not is_leaf:
            continue
        amount = parse_amount(_v(vals, 3), "tr")
        if amount is None:
            amount = _amt(_v(vals, 4)) or _amt(_v(vals, 5))
        if side is None:
            pr.cls, pr.note = "hatali", "Borç/alacak tarafı belirlenemedi"
            out.errors.append(f"Satır {pr.row_no}: {code} için borç/alacak tarafı belirlenemedi.")
            continue
        ana_sum += amount
        pr.cls = "kayit"
        pr.record = {"fis_no": fis["fis"], "madde_no": fis["madde"], "fis_tipi": fis["tip"], "tarih": fis["tarih"],
                     "hesap_kodu": code, "hesap_adi": None if is_empty(_v(vals, 1)) else str(_v(vals, 1)).strip(),
                     "aciklama": None if is_empty(_v(vals, 2)) else str(_v(vals, 2)).strip(),
                     "borc": amount if side == "borc" else 0, "alacak": amount if side == "alacak" else 0}
        if side == "borc":
            leaf_b += amount
        else:
            leaf_a += amount
    close_ana()
    if fis.get("toplam"):
        tb, ta, rn = fis["toplam"]
        if tb != leaf_b or ta != leaf_a:
            out.errors.append(f"Madde {fis['madde']} (satır {rn}): fiş toplamı borç {kurus_str(tb)} / alacak {kurus_str(ta)}, "
                              f"okunan satırların toplamı borç {kurus_str(leaf_b)} / alacak {kurus_str(leaf_a)}.")


# ---- Defteri kebir ----------------------------------------------------------

def _parse_kebir(sheet, out: Parsed):
    group = None
    totals = {}
    for row_no, vals in sheet.rows:
        a = _v(vals, 0)
        if all(is_empty(v) for v in vals):
            out.rows.append(ParsedRow(row_no, vals, "bos"))
            continue
        heads = fold(" ".join(str(c) for c in vals if c is not None))
        if "madde no" in heads and "fis no" in heads:
            out.rows.append(ParsedRow(row_no, vals, "baslik"))
            continue
        if isinstance(a, str) and GROUP_RE.match(a) and all(is_empty(v) for v in vals[1:]):
            group = GROUP_RE.match(a).group(1)
            totals[group] = {"b": 0, "a": 0}
            out.rows.append(ParsedRow(row_no, vals, "grup", note=f"Ana hesap grubu {group}"))
            continue
        if fold(_v(vals, 7)) == "nakli yekun" and group:
            b, al = _amt(_v(vals, 8)), _amt(_v(vals, 9))
            totals[group]["b"] += b
            totals[group]["a"] += al
            out.rows.append(ParsedRow(row_no, vals, "acilis", opening={"hesap_kodu": group, "borc": b, "alacak": al}))
            continue
        if fold(a).startswith("toplam"):
            out.rows.append(ParsedRow(row_no, vals, "ara_toplam"))
            if group:
                tb, ta = _amt(_v(vals, 8)), _amt(_v(vals, 9))
                if tb != totals[group]["b"] or ta != totals[group]["a"]:
                    out.errors.append(f"{group} grubu (satır {row_no}): Toplam satırı borç {kurus_str(tb)} / alacak {kurus_str(ta)}, "
                                      f"nakli yekün + satırlar borç {kurus_str(totals[group]['b'])} / alacak {kurus_str(totals[group]['a'])}.")
            continue
        try:
            tarih = parse_date(a)
        except ParseError:
            tarih = None
        code, _ = parse_account_code(_v(vals, 5))
        if tarih and code:
            b, al = _amt(_v(vals, 8)), _amt(_v(vals, 9))
            if group:
                totals[group]["b"] += b
                totals[group]["a"] += al
                if not code.startswith(group):
                    out.warnings.append(f"Satır {row_no}: {code} hesabı {group} grubunun altında görünüyor.")
            out.rows.append(ParsedRow(row_no, vals, "kayit", record={
                "fis_no": _num_id(_v(vals, 2)) or "?", "madde_no": _num_id(_v(vals, 1)), "fis_tipi": None,
                "evrak_no": None if is_empty(_v(vals, 3)) else str(_v(vals, 3)).strip(), "tarih": tarih,
                "hesap_kodu": code, "hesap_adi": None if is_empty(_v(vals, 6)) else str(_v(vals, 6)).strip(),
                "aciklama": None if is_empty(_v(vals, 7)) else str(_v(vals, 7)).strip(), "borc": b, "alacak": al}))
            continue
        out.rows.append(ParsedRow(row_no, vals, "belirsiz" if any(isinstance(v, (int, float)) for v in vals) else "bilgi"))


# ---- Muavin -----------------------------------------------------------------

def _parse_muavin(sheet, out: Parsed):
    account = None
    for row_no, vals in sheet.rows:
        a = _v(vals, 0)
        if all(is_empty(v) for v in vals):
            out.rows.append(ParsedRow(row_no, vals, "bos"))
            continue
        heads = fold(" ".join(str(c) for c in vals if c is not None))
        if heads.startswith("tarih tip fis no"):
            out.rows.append(ParsedRow(row_no, vals, "baslik"))
            continue
        if isinstance(a, str) and MUAVIN_ACC_RE.match(a) and is_empty(_v(vals, 1)):
            account = MUAVIN_ACC_RE.match(a).group(1)
            out.rows.append(ParsedRow(row_no, vals, "grup", note=f"Hesap {account}"))
            continue
        label = fold(_v(vals, 3)) or fold(a)
        if label.startswith("nakli yekun") and not label.startswith("nakli yekun haric") and account and is_empty(a):
            out.rows.append(ParsedRow(row_no, vals, "acilis", opening={"hesap_kodu": account, "borc": _amt(_v(vals, 4)), "alacak": _amt(_v(vals, 5))}))
            continue
        if any(w in fold(a) for w in ("nakli yekun", "genel toplam", "toplam")):
            out.rows.append(ParsedRow(row_no, vals, "ara_toplam"))
            continue
        try:
            tarih = parse_date(a)
        except ParseError:
            tarih = None
        if tarih and account:
            out.rows.append(ParsedRow(row_no, vals, "kayit", record={
                "fis_no": _num_id(_v(vals, 2)) or "?", "madde_no": None,
                "fis_tipi": None if is_empty(_v(vals, 1)) else str(_v(vals, 1)).strip(), "tarih": tarih,
                "hesap_kodu": account, "hesap_adi": None,
                "aciklama": None if is_empty(_v(vals, 3)) else str(_v(vals, 3)).strip(),
                "borc": _amt(_v(vals, 4)), "alacak": _amt(_v(vals, 5))}))
            continue
        out.rows.append(ParsedRow(row_no, vals, "bilgi"))


# ---- Mizan ------------------------------------------------------------------

MIZAN_COLS = {"borc": "borc", "alacak": "alacak", "borc bakiyesi": "bakiye_borc", "alacak bakiyesi": "bakiye_alacak"}


def _parse_mizan(sheet, out: Parsed):
    """Luca mizanı: HESAP KODU | HESAP ADI | (döviz) | BORÇ | ALACAK | BORÇ BAKİYESİ | ALACAK BAKİYESİ.
    1-2 haneli kodlar (sınıf ve grup) toplam satırıdır; 3 haneli ve alt kodlar hesap satırı olarak alınır.
    BORÇ/ALACAK yalnızca "Tarih Aralığı"ndaki hareketleri içerir; açılış kolonu yoktur."""
    cols = None
    sums = {"borc": 0, "alacak": 0, "bakiye_borc": 0, "bakiye_alacak": 0}
    for row_no, vals in sheet.rows:
        a = _v(vals, 0)
        fa = fold(a)
        if all(is_empty(v) for v in vals):
            out.rows.append(ParsedRow(row_no, vals, "bos"))
            continue
        if fa == "hesap kodu":
            cols = {MIZAN_COLS[fold(v)]: i for i, v in enumerate(vals) if fold(v) in MIZAN_COLS}
            out.rows.append(ParsedRow(row_no, vals, "baslik"))
            continue
        if cols is None:
            out.rows.append(ParsedRow(row_no, vals, "bilgi"))
            continue
        amounts = {k: _amt(_v(vals, i)) for k, i in cols.items()}
        if fa.startswith("genel toplam"):
            out.rows.append(ParsedRow(row_no, vals, "ara_toplam", note="Genel toplam"))
            diff = [k for k in sums if amounts.get(k, 0) != sums[k]]
            if diff:
                out.errors.append(f"Satır {row_no}: GENEL TOPLAM satırı ana hesapların toplamıyla tutmuyor "
                                  f"(borç {kurus_str(amounts.get('borc', 0))} / {kurus_str(sums['borc'])}, "
                                  f"alacak {kurus_str(amounts.get('alacak', 0))} / {kurus_str(sums['alacak'])}).")
            continue
        code, warn = parse_account_code(a)
        if not code:
            out.rows.append(ParsedRow(row_no, vals, "belirsiz" if any(amounts.values()) else "bilgi"))
            continue
        if code.isdigit() and len(code) < 3:
            out.rows.append(ParsedRow(row_no, vals, "ara_toplam", note=f"{'Sınıf' if len(code) == 1 else 'Grup'} toplamı {code}"))
            continue
        if len(code_segments(code)) == 1:
            for k in sums:
                sums[k] += amounts.get(k, 0)
        out.rows.append(ParsedRow(row_no, vals, "kayit", note=warn, tb={
            "hesap_kodu": code, "hesap_adi": None if is_empty(_v(vals, 1)) else str(_v(vals, 1)).strip(),
            "borc": amounts.get("borc", 0), "alacak": amounts.get("alacak", 0),
            "bakiye_borc": amounts.get("bakiye_borc", 0), "bakiye_alacak": amounts.get("bakiye_alacak", 0)}))
    if cols is None:
        out.errors.append("Mizan başlık satırı (HESAP KODU) bulunamadı.")
