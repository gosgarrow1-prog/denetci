"""İçe aktarma akışı: yükle -> algıla -> önizle/onayla -> standart modele aktar -> sonuç raporu."""

import hashlib
import os
import stat
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import IZINLI_UZANTILAR, MAX_YUKLEME_BAYT, data_dir
from ..models import Client, ImportBatch, JournalLine, OpeningBalance, Period, RawRow, SourceFile, TrialBalanceLine
from ..parsing import ParseError, code_segments, detect_number_format, is_empty, parse_account_code, parse_amount, parse_date
from . import luca
from .tables import DOC_FIELDS, REQUIRED, Sheet, UnsupportedFile, classify_row, detect, read_tables


class ImportError_(ValueError):
    pass


class DuplicateFile(ImportError_):
    def __init__(self, existing: SourceFile):
        super().__init__(f"Bu dosya daha önce yüklenmiş: {existing.orig_name}")
        self.existing = existing


def _json_safe(v):
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return str(v)


def store_file(session: Session, client: Client, filename: str, content: bytes) -> SourceFile:
    suffix = Path(filename).suffix.lower()
    if suffix not in IZINLI_UZANTILAR:
        raise ImportError_("Yalnızca .xlsx ve .csv dosyaları destekleniyor. Luca'dan bu biçimlerde dışa aktarın.")
    if len(content) == 0:
        raise ImportError_("Dosya boş.")
    if len(content) > MAX_YUKLEME_BAYT:
        raise ImportError_("Dosya 20 MB sınırını aşıyor.")
    sha = hashlib.sha256(content).hexdigest()
    existing = session.scalar(select(SourceFile).where(SourceFile.client_id == client.id, SourceFile.sha256 == sha))
    if existing:
        # Geri alınmış (iptal) bir yüklemenin dosyası yeniden yüklenebilir.
        active = session.scalar(select(ImportBatch).where(ImportBatch.source_file_id == existing.id,
                                                          ImportBatch.status != "iptal"))
        if active:
            raise DuplicateFile(existing)
        return existing
    folder = data_dir() / "dosyalar" / str(client.id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sha}{suffix}"
    path.write_bytes(content)
    os.chmod(path, stat.S_IRUSR | stat.S_IRGRP)  # orijinal dosya salt okunur
    sf = SourceFile(client_id=client.id, sha256=sha, orig_name=Path(filename).name[:256], size=len(content), stored_path=str(path))
    session.add(sf)
    session.flush()
    return sf


def create_draft(session: Session, client: Client, period: Period, filename: str, content: bytes) -> ImportBatch:
    sf = store_file(session, client, filename, content)
    try:
        sheets = read_tables(Path(sf.stored_path))
    except UnsupportedFile as exc:
        raise ImportError_(str(exc)) from exc
    except Exception as exc:  # bozuk dosya
        raise ImportError_("Dosya okunamadı. Bozuk olabilir veya desteklenmeyen bir biçimde olabilir.") from exc
    batch = ImportBatch(client_id=client.id, period_id=period.id, source_file_id=sf.id, status="taslak")
    sniffed = luca.sniff(sheets)
    det = None if sniffed else detect(sheets)
    if sniffed:
        batch.profile, batch.sheet = sniffed[0], sniffed[1].name
        batch.doc_type = luca.PROFILE_DOC[sniffed[0]]
        batch.mapping = {}
    elif det:
        batch.sheet = det.sheet
        batch.header_row = det.header_row
        batch.mapping = det.mapping
        batch.doc_type = det.doc_type
        sheet = next(s for s in sheets if s.name == det.sheet)
        batch.number_format = _detect_format(sheet, det.mapping, det.header_row)
    else:
        batch.sheet = sheets[0].name if sheets else None
    session.add(batch)
    session.flush()
    return batch


def _detect_format(sheet: Sheet, mapping: dict, header_row: int) -> str:
    idx = [mapping[k] for k in ("borc", "alacak", "bakiye_borc", "bakiye_alacak") if k in mapping]
    samples = [vals[i] for rn, vals in sheet.rows if rn > header_row for i in idx if i < len(vals)]
    return detect_number_format(samples)


def load_sheet(session: Session, batch: ImportBatch) -> Sheet:
    sf = session.get(SourceFile, batch.source_file_id)
    sheets = read_tables(Path(sf.stored_path))
    return next((s for s in sheets if s.name == batch.sheet), sheets[0])


def preview(session: Session, batch: ImportBatch, limit: int = 40) -> list[dict]:
    """Önizleme: satır, sınıf ve okunan değerler. Veritabanına yazmaz."""
    sheet = load_sheet(session, batch)
    if batch.profile:
        parsed = luca.parse(batch.profile, sheet)
        return [{"row_no": r.row_no, "cells": [_json_safe(v) for v in r.values], "cls": r.cls} for r in parsed.rows[:limit]]
    out = []
    for row_no, values in sheet.rows:
        if batch.header_row and row_no <= batch.header_row:
            cls = "baslik" if row_no == batch.header_row else "bilgi"
        else:
            cls, _ = classify_row(values, batch.mapping)
        out.append({"row_no": row_no, "cells": [_json_safe(v) for v in values], "cls": cls})
        if len(out) >= limit:
            break
    return out


def confirm(session: Session, batch: ImportBatch, doc_type: str, mapping: dict[str, int], number_format: str) -> ImportBatch:
    """Onaylanan eşlemeyle tüm satırları standart modele aktarır. Aynı taslak ikinci kez
    onaylanırsa hiçbir şey çoğalmaz."""
    if batch.status == "onaylandi":
        return batch
    if batch.status != "taslak":
        raise ImportError_("Bu aktarım iptal edilmiş.")
    if batch.profile:
        return _confirm_profile(session, batch)
    if doc_type not in DOC_FIELDS:
        raise ImportError_("Belge türü seçilmeli.")
    missing = [f for f in REQUIRED[doc_type] if f not in mapping]
    if missing:
        raise ImportError_("Zorunlu kolonlar eşleştirilmedi: " + ", ".join(missing))
    batch.doc_type, batch.mapping, batch.number_format = doc_type, mapping, number_format

    sheet = load_sheet(session, batch)
    counts = {k: 0 for k in ("kayit", "ara_toplam", "baslik", "bilgi", "bos", "belirsiz", "hatali")}
    warnings: list[str] = []
    errors: list[str] = []
    period = session.get(Period, batch.period_id)
    tb_lines: list[TrialBalanceLine] = []
    last_fis, last_tarih = None, None
    out_of_period = 0

    def cell(values, field):
        i = mapping.get(field)
        return values[i] if i is not None and i < len(values) else None

    for row_no, values in sheet.rows:
        if batch.header_row and row_no < batch.header_row:
            cls, code, note = "bilgi", None, None
        elif batch.header_row and row_no == batch.header_row:
            cls, code, note = "baslik", None, None
        else:
            cls, code = classify_row(values, mapping)
            note = None
        raw = RawRow(client_id=batch.client_id, batch_id=batch.id, sheet=sheet.name, row_no=row_no,
                     cells=[_json_safe(v) for v in values], row_class=cls)
        session.add(raw)
        if cls != "kayit":
            counts[cls] += 1
            if cls == "belirsiz":
                warnings.append(f"Satır {row_no}: tutar var ama hesap kodu okunamadı; kayıt olarak alınmadı.")
            continue
        session.flush()
        try:
            _, code_warning = parse_account_code(cell(values, "hesap_kodu"))
            if code_warning:
                warnings.append(f"Satır {row_no}: {code_warning}.")
            borc = parse_amount(cell(values, "borc"), number_format) or 0
            alacak = parse_amount(cell(values, "alacak"), number_format) or 0
            if doc_type == "yevmiye":
                fis = cell(values, "fis_no")
                tarih = parse_date(cell(values, "tarih"))
                if is_empty(fis) and last_fis is not None:
                    fis, note = last_fis, "Fiş no önceki satırdan alındı"
                if tarih is None and last_tarih is not None:
                    tarih = last_tarih
                if is_empty(fis) or tarih is None:
                    raise ParseError("Fiş no veya tarih eksik")
                fis = str(fis).strip()
                last_fis, last_tarih = fis, tarih
                if period and not (period.baslangic <= tarih <= period.bitis):
                    out_of_period += 1
                session.add(JournalLine(client_id=batch.client_id, batch_id=batch.id, raw_row_id=raw.id, fis_no=fis, tarih=tarih,
                                        hesap_kodu=code, hesap_adi=_str(cell(values, "hesap_adi")),
                                        aciklama=_str(cell(values, "aciklama")), borc=borc, alacak=alacak))
            else:
                tb = TrialBalanceLine(client_id=batch.client_id, batch_id=batch.id, raw_row_id=raw.id, hesap_kodu=code,
                                      hesap_adi=_str(cell(values, "hesap_adi")),
                                      acilis_borc=_opt_amount(values, mapping, "acilis_borc", number_format),
                                      acilis_alacak=_opt_amount(values, mapping, "acilis_alacak", number_format),
                                      borc=borc, alacak=alacak,
                                      bakiye_borc=_opt_amount(values, mapping, "bakiye_borc", number_format),
                                      bakiye_alacak=_opt_amount(values, mapping, "bakiye_alacak", number_format))
                tb_lines.append(tb)
                session.add(tb)
            counts["kayit"] += 1
            raw.note = note
        except ParseError as exc:
            raw.row_class = "hatali"
            raw.note = str(exc)
            counts["hatali"] += 1
            errors.append(f"Satır {row_no}: {exc}")

    if doc_type == "mizan":
        _set_hierarchy(tb_lines, warnings)
    if out_of_period:
        warnings.append(f"{out_of_period} yevmiye satırının tarihi dönem dışında.")

    _finalize(session, batch, len(sheet.rows), counts, warnings, errors, {})
    return batch


def _finalize(session, batch, n_rows, counts, warnings, errors, info):
    prev = session.scalar(select(ImportBatch).where(
        ImportBatch.client_id == batch.client_id, ImportBatch.period_id == batch.period_id,
        ImportBatch.doc_type == batch.doc_type, ImportBatch.status == "onaylandi").order_by(ImportBatch.version.desc()))
    batch.version = (prev.version + 1) if prev else 1
    batch.supersedes_id = prev.id if prev else None
    batch.status = "onaylandi"
    dates = session.execute(select(func.min(JournalLine.tarih), func.max(JournalLine.tarih))
                            .where(JournalLine.batch_id == batch.id)).one()
    summary = {"satir_sayisi": n_rows, "sayilar": counts, "uyarilar": warnings[:200], "hatalar": errors[:200],
               "uyari_sayisi": len(warnings), "hata_sayisi": len(errors), **info}
    if dates[0]:
        summary["kayit_tarihleri"] = [dates[0].isoformat(), dates[1].isoformat()]
    batch.summary = summary
    session.flush()


def _confirm_profile(session: Session, batch: ImportBatch) -> ImportBatch:
    """Tanınan program biçimi (Luca) ile aktarım."""
    sheet = load_sheet(session, batch)
    parsed = luca.parse(batch.profile, sheet)
    period = session.get(Period, batch.period_id)
    counts: dict[str, int] = {}
    out_of_period = 0
    tb_lines: list[TrialBalanceLine] = []
    for pr in parsed.rows:
        raw = RawRow(client_id=batch.client_id, batch_id=batch.id, sheet=sheet.name, row_no=pr.row_no,
                     cells=[_json_safe(v) for v in pr.values], row_class=pr.cls, note=pr.note)
        session.add(raw)
        counts[pr.cls] = counts.get(pr.cls, 0) + 1
        if pr.record is None and pr.opening is None and pr.tb is None:
            continue
        session.flush()
        if pr.tb:
            ln = TrialBalanceLine(client_id=batch.client_id, batch_id=batch.id, raw_row_id=raw.id, **pr.tb)
            session.add(ln)
            tb_lines.append(ln)
        if pr.record:
            r = pr.record
            if period and not (period.baslangic <= r["tarih"] <= period.bitis):
                out_of_period += 1
            session.add(JournalLine(client_id=batch.client_id, batch_id=batch.id, raw_row_id=raw.id, fis_no=r["fis_no"],
                                    madde_no=r.get("madde_no"), fis_tipi=r.get("fis_tipi"), evrak_no=r.get("evrak_no"),
                                    tarih=r["tarih"], hesap_kodu=r["hesap_kodu"], hesap_adi=r.get("hesap_adi"),
                                    aciklama=r.get("aciklama"), borc=r["borc"], alacak=r["alacak"]))
        if pr.opening:
            o = pr.opening
            session.add(OpeningBalance(client_id=batch.client_id, batch_id=batch.id, raw_row_id=raw.id,
                                       hesap_kodu=o["hesap_kodu"], borc=o["borc"], alacak=o["alacak"]))
    warnings = list(parsed.warnings)
    info = dict(parsed.info)
    if tb_lines:
        _set_hierarchy(tb_lines, warnings)
        rng, donem = info.get("tarih_araligi"), info.get("donem")
        # Tarih aralığı dönem başından başlamıyorsa mizan devirleri içermez: bakiyeler yalnızca aralıktaki
        # hareketlerin farkıdır, gerçek dönem sonu bakiyesi değildir.
        if rng and donem and rng[0] > donem[0]:
            info["bakiye_turu"] = "hareket"
            warnings.insert(0, "Bu mizan yalnızca tarih aralığındaki hareketleri içeriyor (önceki aylardan devir yok). "
                               "Bakiye kolonları gerçek bakiye değildir; kasa gibi bakiye kontrolleri bu mizandan yapılmaz. "
                               "Gerçek bakiyeler için mizanı Luca'dan dönem başından itibaren alın.")
        else:
            info["bakiye_turu"] = "kumulatif"
    if out_of_period:
        warnings.append(f"{out_of_period} satırın tarihi dönem dışında.")
    session.flush()
    _finalize(session, batch, len(sheet.rows), counts, warnings, parsed.errors, info)
    return batch


def _str(v):
    return None if is_empty(v) else str(v).strip()


def _opt_amount(values, mapping, field, fmt):
    i = mapping.get(field)
    if i is None:
        return None
    v = values[i] if i < len(values) else None
    return parse_amount(v, fmt) or 0


def _set_hierarchy(lines: list[TrialBalanceLine], warnings: list[str]):
    """Ana/alt hesap ilişkisini kurar. Toplamlar yalnızca yaprak hesaplardan alınır."""
    codes = {}
    for ln in lines:
        if ln.hesap_kodu in codes:
            warnings.append(f"Hesap kodu {ln.hesap_kodu} mizanda birden fazla kez geçiyor.")
        codes[ln.hesap_kodu] = ln
    for ln in lines:
        ln.parent_kodu = _find_parent(ln.hesap_kodu, codes)
    parents = {ln.parent_kodu for ln in lines if ln.parent_kodu}
    for ln in lines:
        ln.is_leaf = ln.hesap_kodu not in parents


def _find_parent(code: str, codes: dict) -> str | None:
    segs = code_segments(code)
    if len(segs) > 1:
        for n in range(len(segs) - 1, 0, -1):
            for sep in (".", "-", " "):
                cand = sep.join(segs[:n])
                if cand in codes:
                    return cand
        return None
    # Ayırıcısız kodlar (ör. 10001): en uzun önek olan kod üst hesaptır.
    best = None
    for other in codes:
        if other != code and code.startswith(other) and (best is None or len(other) > len(best)):
            best = other
    return best


def latest_batch(session: Session, client_id: int, period_id: int, doc_type: str) -> ImportBatch | None:
    return session.scalar(select(ImportBatch).where(
        ImportBatch.client_id == client_id, ImportBatch.period_id == period_id, ImportBatch.doc_type == doc_type,
        ImportBatch.status == "onaylandi").order_by(ImportBatch.version.desc()))


def batch_count(session: Session, client_id: int) -> int:
    return session.scalar(select(func.count()).select_from(ImportBatch).where(ImportBatch.client_id == client_id)) or 0
