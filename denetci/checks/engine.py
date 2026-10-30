"""Analiz çalıştırma: son onaylı mizan ve yevmiye sürümleriyle kuralları çalıştırır, sonucu saklar."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import ENGINE_VERSION, RULESET_VERSION
from ..importer.load import latest_batch
from ..models import AnalysisRun, Client, Finding, FindingCase, FindingEvidence, JournalLine, OpeningBalance, Period, TrialBalanceLine
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


def build_context(session: Session, client: Client, period: Period):
    """Bir dönemin son onaylı belge sürümlerinden kontrol bağlamını kurar. Belge yoksa (None, {}) döner."""
    batches = {d: latest_batch(session, client.id, period.id, d) for d in ("mizan", "yevmiye", "kebir", "muavin")}
    if not any(batches.values()):
        return None, batches
    mizan_b = batches["mizan"]
    mizan = list(session.scalars(select(TrialBalanceLine).where(
        TrialBalanceLine.client_id == client.id, TrialBalanceLine.batch_id == mizan_b.id))) if mizan_b else None

    def lines(b):
        return list(session.scalars(select(JournalLine).where(JournalLine.client_id == client.id,
                                                               JournalLine.batch_id == b.id))) if b else None

    ctx = Context(client.cerceve, period.baslangic, period.bitis, mizan, lines(batches["yevmiye"]),
                  kebir=lines(batches["kebir"]), muavin=lines(batches["muavin"]))
    for d in ("kebir", "muavin"):
        if batches[d]:
            ctx.openings[d] = list(session.scalars(select(OpeningBalance).where(
                OpeningBalance.client_id == client.id, OpeningBalance.batch_id == batches[d].id)))
    ctx.devreden_kdv = period.devreden_kdv
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
