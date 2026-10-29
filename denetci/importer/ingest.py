"""Toplu dosya yükleme: dönemi belirler, Luca biçimindeki dosyaları doğrudan aktarır.

- Luca dosyasında "Tarih Aralığı" varsa dönem ondan belirlenir; yoksa seçilen dönem kullanılır.
- Luca biçimi tanınan dosya, satır toplamları doğrulanarak hemen aktarılır (sonuç özeti dosya sayfasında).
- Tanınmayan dosya kolon eşleştirme için taslak olarak bekletilir.
"""

import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Client, ImportBatch, Period
from . import luca
from .load import DuplicateFile, ImportError_, confirm, create_draft
from .tables import UnsupportedFile, read_tables


@dataclass
class Outcome:
    filename: str
    status: str  # aktarildi, esleme, tekrar, hata
    message: str
    batch: ImportBatch | None = None
    period: Period | None = None


def peek_range(filename: str, content: bytes) -> tuple[date, date] | None:
    """Luca dosyasındaki "Tarih Aralığı"nı döner (yoksa None). Hiçbir şey kaydetmez."""
    suffix = Path(filename).suffix.lower()
    if suffix not in (".xlsx", ".csv"):
        return None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"dosya{suffix}"
            path.write_bytes(content)
            sheets = read_tables(path)
    except (UnsupportedFile, Exception):
        return None
    sniffed = luca.sniff(sheets)
    if not sniffed:
        return None
    rng = luca._info(sniffed[1].rows).get("tarih_araligi")
    return (date.fromisoformat(rng[0]), date.fromisoformat(rng[1])) if rng else None


def _period_type(start: date, end: date) -> str:
    months = (end.year - start.year) * 12 + end.month - start.month + 1
    if start.day == 1 and months == 12 and start.month == 1:
        return "yillik"
    return {1: "aylik", 3: "uc_aylik"}.get(months, "ara")


def find_or_create_period(session: Session, client: Client, start: date, end: date) -> Period:
    p = session.scalar(select(Period).where(Period.client_id == client.id, Period.baslangic == start, Period.bitis == end))
    if p:
        return p
    p = Period(client_id=client.id, baslangic=start, bitis=end, tur=_period_type(start, end))
    session.add(p)
    session.flush()
    return p


def ingest(session: Session, client: Client, filename: str, content: bytes, period: Period | None) -> Outcome:
    rng = peek_range(filename, content)
    if rng:
        period = find_or_create_period(session, client, *rng)
    if period is None:
        return Outcome(filename, "hata", "Dönem belirlenemedi: dosyada tarih aralığı yok. Önce bir dönem seçin.")
    try:
        with session.begin_nested():
            batch = create_draft(session, client, period, filename, content)
    except DuplicateFile as exc:
        return Outcome(filename, "tekrar", f"Daha önce yüklenmiş ({exc.existing.orig_name}); tekrar alınmadı.", period=period)
    except ImportError_ as exc:
        return Outcome(filename, "hata", str(exc), period=period)
    if batch.profile:
        try:
            with session.begin_nested():
                confirm(session, batch, batch.doc_type, {}, "tr")
        except ImportError_ as exc:
            return Outcome(filename, "hata", str(exc), batch, period)
        sm = batch.summary
        msg = f"{luca.PROFILE_LABELS[batch.profile]}, {sm['sayilar'].get('kayit', 0)} kayıt aktarıldı"
        if sm.get("hata_sayisi"):
            msg += f"; {sm['hata_sayisi']} hata"
        if sm.get("uyari_sayisi"):
            msg += f"; {sm['uyari_sayisi']} uyarı"
        return Outcome(filename, "aktarildi", msg + ".", batch, period)
    return Outcome(filename, "esleme", "Biçim tanınmadı; kolonları eşleştirip onaylayın.", batch, period)
