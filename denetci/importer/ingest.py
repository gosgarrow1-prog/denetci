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

from ..parsing import kurus_str
from ..models import Client, Declaration, ImportBatch, Period
from . import beyanname, luca
from .load import DuplicateFile, ImportError_, confirm, create_draft, store_file
from .tables import read_tables


@dataclass
class Outcome:
    filename: str
    status: str  # aktarildi, esleme, tekrar, hata
    message: str
    batch: ImportBatch | None = None
    period: Period | None = None
    declaration: Declaration | None = None


def peek(filename: str, content: bytes) -> dict:
    """Luca dosyasının başlık bilgisini döner (tarih aralığı, firma unvanı). Hiçbir şey kaydetmez."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return beyanname.peek(content)
    if suffix not in (".xlsx", ".csv"):
        return {}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"dosya{suffix}"
            path.write_bytes(content)
            sheets = read_tables(path)
    except Exception:
        return {}
    sniffed = luca.sniff(sheets)
    return luca._info(sniffed[1].rows) if sniffed else {}


def peek_range(filename: str, content: bytes) -> tuple[date, date] | None:
    """Luca dosyasındaki "Tarih Aralığı"nı döner (yoksa None)."""
    rng = peek(filename, content).get("tarih_araligi")
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


def ingest_beyanname(session: Session, client: Client, filename: str, content: bytes) -> Outcome:
    """Beyanname PDF'ini okuyup mükellefe kaydeder. Aynı tür ve dönemin önceki kaydı yerine geçer."""
    try:
        b = beyanname.parse(content)
    except beyanname.BeyannameHatasi as exc:
        return Outcome(filename, "hata", str(exc))
    try:
        with session.begin_nested():
            sf = store_file(session, client, filename, content, allowed={".pdf"})
    except DuplicateFile as exc:
        return Outcome(filename, "tekrar", f"Daha önce yüklenmiş ({exc.existing.orig_name}); tekrar alınmadı.")
    except ImportError_ as exc:
        return Outcome(filename, "hata", str(exc))
    eski = list(session.scalars(select(Declaration).where(Declaration.client_id == client.id, Declaration.tur == b.tur,
                                                          Declaration.yil == b.yil, Declaration.ay == b.ay,
                                                          Declaration.durum == "aktif")))
    for d in eski:
        d.durum = "iptal"
    d = Declaration(client_id=client.id, source_file_id=sf.id, tur=b.tur, alt=b.alt, yil=b.yil, ay=b.ay, vkn=b.vkn,
                    unvan=(b.unvan or "")[:256] or None, onay=b.onay, duzeltme=b.duzeltme, alanlar=b.alanlar)
    session.add(d)
    if b.vkn and not client.vkn:
        client.vkn = b.vkn
    if b.vergi_dairesi and not client.vergi_dairesi:
        client.vergi_dairesi = b.vergi_dairesi[:128]
    session.flush()
    msg = f"{beyanname.TUR_ADLARI[b.tur]} ({beyanname.donem_adi(b.tur, b.yil, b.ay)}) okundu"
    if b.tur == "kdv":
        msg += f"; sonraki döneme devreden KDV {_tl(b.alanlar.get('sonraki_devreden'))} TL"
    else:
        msg += f"; hesaplanan geçici vergi {_tl(b.alanlar.get('nihai', b.alanlar.get('hesaplanan')))} TL"
    if eski:
        msg += "; aynı dönemin önceki beyannamesinin yerine geçti"
    return Outcome(filename, "aktarildi", msg + ".", declaration=d)


def _tl(kurus) -> str:
    return kurus_str(kurus or 0)


def ingest(session: Session, client: Client, filename: str, content: bytes, period: Period | None) -> Outcome:
    if Path(filename).suffix.lower() == ".pdf":
        return ingest_beyanname(session, client, filename, content)
    rng = peek_range(filename, content)
    if rng:
        period = find_or_create_period(session, client, *rng)
    if period is None:
        return Outcome(filename, "hata", "Dönem belirlenemedi: dosyada tarih aralığı yok. Önce bir dönem seçin.")
    if period.kesin:
        return Outcome(filename, "hata", "Bu dönem kesinleştirilmiş; dosya alınmadı. Değişiklik için önce dönemin kilidini açın.",
                       period=period)
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
