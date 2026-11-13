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
from ..models import Client, Declaration, ImportBatch, InvoiceList, InvoiceRow, Period
from . import beyanname, fatura, luca
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
    fatura: InvoiceList | None = None


def peek(filename: str, content: bytes) -> dict:
    """Luca dosyasının başlık bilgisini döner (tarih aralığı, firma unvanı). Hiçbir şey kaydetmez."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return beyanname.peek(content)
    if suffix not in (".xlsx", ".csv", ".xml", ".zip"):
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


def _fatura_listesi(filename: str, content: bytes):
    if Path(filename).suffix.lower() != ".xlsx":
        return None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dosya.xlsx"
            path.write_bytes(content)
            sn = fatura.sniff(read_tables(path))
    except Exception:
        return None
    return fatura.parse(*sn) if sn else None


def ingest_fatura(session: Session, client: Client, filename: str, content: bytes, rows) -> Outcome:
    """e-Fatura listesini kaydeder. Yönü (gelen/giden) defterdeki kayıtlarla eşleşmeden bulunur."""
    from ..efatura import yon_bul

    if not rows:
        return Outcome(filename, "hata", "Fatura listesinde okunabilir fatura satırı yok.")
    try:
        with session.begin_nested():
            sf = store_file(session, client, filename, content)
    except DuplicateFile as exc:
        return Outcome(filename, "tekrar", f"Daha önce yüklenmiş ({exc.existing.orig_name}); tekrar alınmadı.")
    except ImportError_ as exc:
        return Outcome(filename, "hata", str(exc))
    lst = InvoiceList(client_id=client.id, source_file_id=sf.id, baslangic=min(r.tarih for r in rows),
                      bitis=max(r.tarih for r in rows), adet=len(rows))
    session.add(lst)
    session.flush()
    for r in rows:
        session.add(InvoiceRow(list_id=lst.id, client_id=client.id, satir=r.satir, tarih=r.tarih, no=r.no[:64],
                               tip=(r.tip or "")[:32] or None, vkn=(r.vkn or "")[:16] or None, unvan=(r.unvan or "")[:256] or None,
                               tevkifat=(r.tevkifat or "")[:16] or None, ozel_matrah=r.ozel_matrah,
                               kdv={str(k): v for k, v in r.kdv.items()}, matrah={str(k): v for k, v in r.matrah.items()},
                               toplam=r.toplam))
    session.flush()
    lst.yon = yon_bul(session, client, rows)
    lst.yon_kaynak = "defter" if lst.yon else None
    if not lst.yon:
        # Fatura numarasının ilk 3 karakteri faturayı kesen firmanın serisidir: birden çok seri varsa liste başka
        # firmalardan gelen (alış) faturalardır; tek seri ve birden çok fatura varsa büyük olasılıkla giden.
        seriler = {r.no[:3].upper() for r in rows}
        if len(seriler) > 1:
            lst.yon, lst.yon_kaynak = "gelen", "seri"
        elif len(rows) > 2:
            lst.yon, lst.yon_kaynak = "giden", "seri"
    ad = {"giden": "giden (satış)", "gelen": "gelen (alış)"}.get(lst.yon)
    if lst.yon_kaynak == "seri":
        ad += " (fatura serilerine göre tahmin; yanlışsa Belgeler sayfasından değiştirin)"
    msg = (f"Fatura listesi okundu: {len(rows)} fatura, {lst.baslangic:%d.%m.%Y} – {lst.bitis:%d.%m.%Y}, KDV toplamı "
           f"{_tl(sum(r.kdv_toplam for r in rows))} TL")
    msg += (f"; defterle eşleşmeye göre {ad} faturalar." if lst.yon_kaynak == "defter" else f"; {ad} faturalar.") if ad else \
        "; gelen mi giden mi olduğu defterden anlaşılamadı, Belgeler sayfasında seçin."
    return Outcome(filename, "aktarildi", msg, fatura=lst)


def _tl(kurus) -> str:
    return kurus_str(kurus or 0)


def ingest(session: Session, client: Client, filename: str, content: bytes, period: Period | None) -> Outcome:
    o = _ingest(session, client, filename, content, period)
    from .. import arsiv
    arsiv.kaydet(client, o, content)
    return o


def _ingest(session: Session, client: Client, filename: str, content: bytes, period: Period | None) -> Outcome:
    if Path(filename).suffix.lower() == ".pdf":
        return ingest_beyanname(session, client, filename, content)
    if (fl := _fatura_listesi(filename, content)) is not None:
        return ingest_fatura(session, client, filename, content, fl)
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
