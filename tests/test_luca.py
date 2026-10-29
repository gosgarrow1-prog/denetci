"""Luca biçimindeki SENTETİK dosyalarla okuma ve kontrol testleri. Gerçek mükellef verisi kullanılmaz."""

from sqlalchemy import select

from denetci import synthetic_luca
from denetci.checks.engine import run_analysis
from denetci.importer.load import confirm, create_draft
from denetci.models import Finding, FindingEvidence, JournalLine, OpeningBalance, RawRow, TrialBalanceLine


def _import(session, c, p, files):
    batches = {}
    for kind, path in files.items():
        b = create_draft(session, c, p, path.name, path.read_bytes())
        assert b.profile == f"luca_{kind}" and b.doc_type == kind
        confirm(session, b, b.doc_type, {}, "tr")
        assert b.status == "onaylandi"
        assert b.summary["hatalar"] == []
        batches[kind] = b
    return batches


def _lines(session, b):
    return list(session.scalars(select(JournalLine).where(JournalLine.batch_id == b.id)))


def test_luca_parsing_clean(session, client_period, data_root):
    c, p = client_period
    files = synthetic_luca.generate(data_root / "luca_t")
    b = _import(session, c, p, files)
    yev, keb, mua = _lines(session, b["yevmiye"]), _lines(session, b["kebir"]), _lines(session, b["muavin"])
    # Yalnızca yaprak (üç seviyeli) hesaplar kayıt olur; ana ve ara seviye satırları toplanmaz.
    assert yev and all(len(j.hesap_kodu.split(".")) == 3 for j in yev)
    assert sum(j.borc for j in yev) == sum(j.alacak for j in yev)
    assert sum(j.borc for j in yev) == sum(j.borc for j in keb)
    assert len(yev) == len(keb)
    assert all(j.madde_no and j.fis_tipi == "Mahsup" for j in yev)
    assert {j.hesap_kodu for j in mua} <= set(synthetic_luca.MUAVIN_HESAPLAR)
    counts = b["yevmiye"].summary["sayilar"]
    assert counts["ust_hesap"] > 0 and counts["kayit"] == len(yev)
    assert b["yevmiye"].summary["tarih_araligi"] == ["2026-07-01", "2026-09-30"]
    # Kebirdeki nakli yekün ana hesap düzeyinde saklanır.
    opens = {o.hesap_kodu: o for o in session.scalars(select(OpeningBalance).where(OpeningBalance.batch_id == b["kebir"].id))}
    assert opens["100"].borc == 12_400_00 and opens["500"].alacak == 600_000_00
    # Yevmiye kayıtları ham satıra bağlıdır.
    raw = session.get(RawRow, yev[0].raw_row_id)
    assert raw.row_class == "kayit" and raw.cells[0].strip() == yev[0].hesap_kodu


def test_luca_clean_analysis(session, client_period, data_root):
    c, p = client_period
    _import(session, c, p, synthetic_luca.generate(data_root / "luca_t"))
    run = run_analysis(session, c, p)
    status = {r["id"]: r["status"] for r in run.rule_results}
    assert status["K-00"] == "gecti"
    assert status["K-02"] == "gecti"
    assert status["K-05K"] == "gecti" and status["K-05M"] == "gecti"
    assert status["K-09"] == "gecti"
    assert status["K-01"] == "calistirilamadi"  # mizan yok
    assert run.reliability == "kismen"
    assert set(run.source_batches) == {"yevmiye", "kebir", "muavin"}
    assert not list(session.scalars(select(Finding).where(Finding.run_id == run.id)))


def test_luca_known_errors(session, client_period, data_root):
    c, p = client_period
    _import(session, c, p, synthetic_luca.generate(data_root / "luca_h", hatali=True))
    run = run_analysis(session, c, p)
    findings = list(session.scalars(select(Finding).where(Finding.run_id == run.id)))
    found = {(f.rule_id, f.hesap_kodu or f.title) for f in findings}
    yev = session.scalar(select(JournalLine).where(JournalLine.aciklama == "Genel gider banka ödemesi"))
    assert found == {
        ("K-02", f"Madde {yev.madde_no} denk değil"),
        ("K-09", "100"),
        ("K-05K", "102"), ("K-05K", "770"),
        ("K-05M", "102.02.001"),
    }
    assert run.reliability == "kritik"
    k09 = next(f for f in findings if f.rule_id == "K-09")
    assert "14.09.2026" in k09.explanation
    labels = [e.label for e in session.scalars(select(FindingEvidence).where(FindingEvidence.finding_id == k09.id))]
    assert any("Nakli yekün" in lb for lb in labels) and any("eksiye" in lb for lb in labels)


def test_luca_partial_coverage(session, client_period, data_root):
    c, p = client_period
    _import(session, c, p, synthetic_luca.generate(data_root / "luca_k", kismi=True))
    run = run_analysis(session, c, p)
    found = {(f.rule_id, f.title) for f in session.scalars(select(Finding).where(Finding.run_id == run.id))}
    assert found == {("K-00", "Yevmiye defteri dönemin tamamını kapsamıyor")}
    res = {r["id"]: r for r in run.rule_results}
    # Karşılaştırma yalnızca ortak aralıkta yapılır, bu yüzden kısmi yevmiye fark üretmez.
    assert res["K-05K"]["status"] == "gecti" and "05.09.2026" in res["K-05K"]["message"]


def test_luca_new_version_supersedes(session, client_period, data_root):
    c, p = client_period
    first = _import(session, c, p, {"yevmiye": synthetic_luca.generate(data_root / "v1", kismi=True)["yevmiye"]})
    second = _import(session, c, p, {"yevmiye": synthetic_luca.generate(data_root / "v2")["yevmiye"]})
    assert second["yevmiye"].version == 2 and second["yevmiye"].supersedes_id == first["yevmiye"].id
    run = run_analysis(session, c, p)
    assert run.source_batches["yevmiye"] == second["yevmiye"].id


def test_luca_mizan_movement_only(session, client_period, data_root):
    """Tarih aralığı dönem başından başlamayan Luca mizanı: bakiyeler gerçek bakiye değildir."""
    c, p = client_period
    b = _import(session, c, p, synthetic_luca.generate(data_root / "luca_m", hatali=True, mizan=True))
    mz = b["mizan"]
    assert mz.summary["bakiye_turu"] == "hareket"
    assert "devir yok" in mz.summary["uyarilar"][0]
    tl = list(session.scalars(select(TrialBalanceLine).where(TrialBalanceLine.batch_id == mz.id)))
    # Sınıf (1) ve grup (10) satırları hesap olarak alınmaz.
    assert all(len(t.hesap_kodu) >= 3 for t in tl)
    leaves = [t for t in tl if t.is_leaf]
    assert leaves and all(len(t.hesap_kodu.split(".")) == 3 for t in leaves)
    run = run_analysis(session, c, p)
    status = {r["id"]: r["status"] for r in run.rule_results}
    assert status["K-01"] == status["K-03"] == status["K-04"] == "gecti"
    found = {(f.rule_id, f.hesap_kodu) for f in session.scalars(select(Finding).where(Finding.run_id == run.id))}
    assert {h for r, h in found if r == "K-05"} == {"102.01.001", "102.02.001", "770.02.001"}
    # Kasa bakiyesi hareket mizanından değil, nakli yekün içeren kebirden kontrol edilir.
    assert ("K-09", "100") in found


def test_luca_mizan_movement_only_without_ledger(session, client_period, data_root):
    c, p = client_period
    files = synthetic_luca.generate(data_root / "luca_m2", mizan=True)
    _import(session, c, p, {"mizan": files["mizan"]})
    run = run_analysis(session, c, p)
    k09 = next(r for r in run.rule_results if r["id"] == "K-09")
    assert k09["status"] == "calistirilamadi" and "hareket" in k09["message"]


def test_luca_mizan_cumulative(session, client_period, data_root):
    c, p = client_period
    files = synthetic_luca.generate(data_root / "luca_mk", hatali=True, mizan=True, kumulatif=True)
    b = _import(session, c, p, {"mizan": files["mizan"]})
    assert b["mizan"].summary["bakiye_turu"] == "kumulatif"
    run = run_analysis(session, c, p)
    status = {r["id"]: r["status"] for r in run.rule_results}
    assert status["K-01"] == status["K-03"] == "gecti"
    k09 = list(session.scalars(select(Finding).where(Finding.run_id == run.id, Finding.rule_id == "K-09")))
    assert [f.hesap_kodu for f in k09] == ["100.01.001"] and k09[0].amount == 48_250_00
