"""Analiz çalıştırma: son onaylı mizan ve yevmiye sürümleriyle kuralları çalıştırır, sonucu saklar."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import ENGINE_VERSION, RULESET_VERSION
from .. import beyannameler
from ..importer.load import latest_batch
from ..models import AnalysisRun, ImportBatch, Client, Finding, FindingCase, FindingEvidence, JournalLine, OpeningBalance, Period, TrialBalanceLine
from .registry import ALL_RULES
from .rules import Context

RELIABILITY_LABELS = {
    "uygun": "Analize uygun",
    "kismen": "Kısmen uygun",
    "kritik": "Kritik veri sorunu var",
}


class NoData(ValueError):
    pass


ONCEKI_DONEM_SAYISI = 3  # oran karşılaştırmalarında kullanılan önceki dönem sayısı


def _range(b):
    rng = b.summary.get("tarih_araligi") or b.summary.get("kayit_tarihleri")
    return (date.fromisoformat(rng[0]), date.fromisoformat(rng[1])) if rng else None


def kapsayan_yevmiye(session: Session, client: Client, start: date, end: date, yilbasindan: bool = False):
    """Verilen aralığı kapsayan, başka bir döneme yüklenmiş en son onaylı yevmiye (ör. Ocak–Eylül yevmiyesi
    Temmuz–Eylül çeyreğini kapsar). yilbasindan: aralık yılın 1 Ocak'ından başlamalı (bakiyeler için)."""
    best = None
    for b in session.scalars(select(ImportBatch).where(ImportBatch.client_id == client.id, ImportBatch.doc_type == "yevmiye",
                                                       ImportBatch.status == "onaylandi")):
        rng = _range(b)
        if not rng or rng[0] > start or rng[1] < end:
            continue
        if yilbasindan and rng[0] != date(end.year, 1, 1):
            continue
        if best is None or b.id > best.id:
            best = b
    return best


def _yevmiye_lines(session: Session, client: Client, b) -> list:
    """Aynı istek içinde aynı yevmiye satırları bir kez okunur."""
    cache = session.info.setdefault("_yevmiye_cache", {})
    if b.id not in cache:
        cache[b.id] = list(session.scalars(select(JournalLine).where(JournalLine.client_id == client.id,
                                                                     JournalLine.batch_id == b.id)))
    return cache[b.id]


def build_context(session: Session, client: Client, period: Period):
    """Bir dönemin son onaylı belge sürümlerinden kontrol bağlamını kurar. Belge yoksa (None, {}) döner.

    Döneme ayrıca dosya yüklenmemişse, dönemi kapsayan daha geniş bir yevmiye (ör. Ocak–Eylül) kullanılır: dönemin
    kayıtları ondan ayrılır. Yılbaşından başlayan yevmiye, dönem başı ve sonu bakiyeleri için de kullanılır."""
    batches = {d: latest_batch(session, client.id, period.id, d) for d in ("mizan", "yevmiye", "kebir", "muavin")}
    turetilmis = None
    if not any(batches.values()):
        turetilmis = kapsayan_yevmiye(session, client, period.baslangic, period.bitis)
        if turetilmis is None:
            return None, batches
        batches["yevmiye"] = turetilmis
    mizan_b = batches["mizan"]
    mizan = list(session.scalars(select(TrialBalanceLine).where(
        TrialBalanceLine.client_id == client.id, TrialBalanceLine.batch_id == mizan_b.id))) if mizan_b else None

    def lines(b):
        return list(session.scalars(select(JournalLine).where(JournalLine.client_id == client.id,
                                                               JournalLine.batch_id == b.id))) if b else None

    if turetilmis is not None:
        yev = [j for j in _yevmiye_lines(session, client, turetilmis) if period.baslangic <= j.tarih <= period.bitis]
    else:
        yev = lines(batches["yevmiye"])
    ctx = Context(client.cerceve, period.baslangic, period.bitis, mizan, yev,
                  kebir=lines(batches["kebir"]), muavin=lines(batches["muavin"]))
    ctx.turetilmis = turetilmis is not None
    ytd = kapsayan_yevmiye(session, client, date(period.bitis.year, 1, 1), period.bitis, yilbasindan=True)
    if ytd is not None:
        ctx.ytd = [j for j in _yevmiye_lines(session, client, ytd) if j.tarih <= period.bitis]
    for d in ("kebir", "muavin"):
        if batches[d]:
            ctx.openings[d] = list(session.scalars(select(OpeningBalance).where(
                OpeningBalance.client_id == client.id, OpeningBalance.batch_id == batches[d].id)))
    ctx.devreden_kdv = period.devreden_kdv
    ctx.devreden_kaynak = "girildi" if period.devreden_kdv is not None else None
    decls = beyannameler.aktif(session, client.id)
    if beyannameler.tek_ay(period.baslangic, period.bitis):
        if ctx.devreden_kdv is None and (d := beyannameler.onceki_ay_kdv(decls, period.baslangic)):
            ctx.devreden_kdv, ctx.devreden_kaynak = d.alanlar.get("sonraki_devreden"), "beyanname"
        if d := beyannameler.bul(decls, "kdv", period.bitis.year, period.bitis.month):
            ctx.kdv_beyan = d.alanlar
    if d := beyannameler.bul(decls, "gecici", period.bitis.year, period.bitis.month):
        ctx.gv_beyan = d.alanlar
    if mizan_b and mizan_b.summary.get("bakiye_turu") == "hareket":
        ctx.mizan_hareket = True
    for d in ("mizan", "yevmiye", "kebir", "muavin"):
        b = batches[d]
        if b:
            rng = b.summary.get("tarih_araligi") or b.summary.get("kayit_tarihleri")
            if b.summary.get("tarih_araligi"):
                ctx.declared.add(d)
            if rng:
                ctx.ranges[d] = (date.fromisoformat(rng[0]), date.fromisoformat(rng[1]))
    if turetilmis is not None:
        # Yevmiyeden ayrılan kayıtlar dönemi tam kapsar.
        ctx.ranges["yevmiye"] = (period.baslangic, period.bitis)
    return ctx, batches


def run_analysis(session: Session, client: Client, period: Period) -> AnalysisRun:
    ctx, batches = build_context(session, client, period)
    if ctx is None:
        raise NoData("Bu dönem için onaylanmış belge yok.")
    mizan_b, yev_b = batches["mizan"], batches["yevmiye"]
    # Aynı mükellefin önceki dönemleri (en yakından uzağa); dönemler arası kontroller için.
    earlier = session.scalars(select(Period).where(Period.client_id == client.id, Period.bitis < period.baslangic)
                              .order_by(Period.bitis.desc())).all()
    for p in earlier:
        if len(ctx.previous) >= ONCEKI_DONEM_SAYISI:
            break
        pctx, _ = build_context(session, client, p)
        if pctx is not None:
            ctx.previous.append(pctx)
    results = [fn(ctx) for rule, fn in ALL_RULES if client.cerceve in rule.frameworks]

    run = AnalysisRun(client_id=client.id, period_id=period.id, mizan_batch_id=mizan_b.id if mizan_b else None,
                      yevmiye_batch_id=yev_b.id if yev_b else None, ruleset_version=RULESET_VERSION,
                      engine_version=ENGINE_VERSION, reliability=_reliability(results),
                      source_batches={d: b.id for d, b in batches.items() if b})
    run.rule_results = [{"id": r.rule.id, "version": r.rule.version, "title": r.rule.title, "status": r.status,
                         "message": r.message, "kind": r.rule.kind, "basis": r.rule.basis,
                         "basis_verified": r.rule.basis_verified, "count": len(r.drafts)} for r in results]
    session.add(run)
    session.flush()

    for r in results:
        for d in r.drafts:
            fp = f"{r.rule.id}|{period.id}|{d.key}"
            f = Finding(client_id=client.id, run_id=run.id, rule_id=r.rule.id, rule_version=r.rule.version, kind=r.rule.kind,
                        severity=d.severity, title=d.title, hesap_kodu=d.hesap_kodu, amount=d.amount,
                        explanation=d.explanation, certainty=d.certainty, next_step=d.next_step, fingerprint=fp)
            session.add(f)
            session.flush()
            for ev in d.evidence:
                session.add(FindingEvidence(client_id=client.id, finding_id=f.id, raw_row_id=ev.raw_row_id, label=ev.label))
            case = session.scalar(select(FindingCase).where(FindingCase.client_id == client.id, FindingCase.fingerprint == fp))
            if not case:
                session.add(FindingCase(client_id=client.id, fingerprint=fp))
    session.flush()
    return run


def _reliability(results) -> str:
    by_id = {r.rule.id: r for r in results}
    if any(by_id.get(i) and by_id[i].status == "bulgu" for i in ("K-01", "K-02")):
        return "kritik"
    # Belge türü hiç yüklenmediği için çalışmayan kontroller de "kısmen" sayılır.
    if any(r.status == "calistirilamadi" for r in results):
        return "kismen"
    if any(by_id.get(i) and by_id[i].status == "bulgu" for i in ("K-00", "K-03", "K-04", "K-05", "K-05K", "K-05M")):
        return "kismen"
    return "uygun"
