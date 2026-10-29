"""Analiz çalıştırma: son onaylı mizan ve yevmiye sürümleriyle kuralları çalıştırır, sonucu saklar."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import ENGINE_VERSION, RULESET_VERSION
from ..importer.load import latest_batch
from ..models import AnalysisRun, Client, Finding, FindingCase, FindingEvidence, JournalLine, Period, TrialBalanceLine
from .rules import ALL_RULES, Context

RELIABILITY_LABELS = {
    "uygun": "Analize uygun",
    "kismen": "Kısmen uygun",
    "kritik": "Kritik veri sorunu var",
}


class NoData(ValueError):
    pass


def run_analysis(session: Session, client: Client, period: Period) -> AnalysisRun:
    mizan_b = latest_batch(session, client.id, period.id, "mizan")
    yev_b = latest_batch(session, client.id, period.id, "yevmiye")
    if not mizan_b and not yev_b:
        raise NoData("Bu dönem için onaylanmış mizan veya yevmiye yok.")
    mizan = list(session.scalars(select(TrialBalanceLine).where(
        TrialBalanceLine.client_id == client.id, TrialBalanceLine.batch_id == mizan_b.id))) if mizan_b else None
    yev = list(session.scalars(select(JournalLine).where(
        JournalLine.client_id == client.id, JournalLine.batch_id == yev_b.id))) if yev_b else None

    ctx = Context(client.cerceve, period.baslangic, period.bitis, mizan, yev)
    results = [fn(ctx) for rule, fn in ALL_RULES if client.cerceve in rule.frameworks]

    run = AnalysisRun(client_id=client.id, period_id=period.id, mizan_batch_id=mizan_b.id if mizan_b else None,
                      yevmiye_batch_id=yev_b.id if yev_b else None, ruleset_version=RULESET_VERSION,
                      engine_version=ENGINE_VERSION, reliability=_reliability(results))
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
    if any(r.status == "calistirilamadi" for r in results):
        return "kismen"
    if any(by_id.get(i) and by_id[i].status == "bulgu" for i in ("K-03", "K-04", "K-05")):
        return "kismen"
    return "uygun"
