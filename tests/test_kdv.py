"""KDV ön kontrolleri (K-21, K-22, K-22D, K-23). Yalnızca sentetik veri."""

from datetime import date
from types import SimpleNamespace

from sqlalchemy import select

from denetci import synthetic_luca
from denetci.checks.engine import run_analysis
from denetci.checks.kdv import k21, k22, k22d, k23
from denetci.checks.rules import Context
from denetci.importer.load import confirm, create_draft
from denetci.models import Finding, Period


def tb(code, name="", ab=None, aa=None, b=0, a=0):
    net = (ab or 0) - (aa or 0) + b - a
    return SimpleNamespace(hesap_kodu=code, hesap_adi=name, acilis_borc=ab, acilis_alacak=aa, borc=b, alacak=a,
                           bakiye_borc=max(net, 0), bakiye_alacak=max(-net, 0), is_leaf=True, raw_row_id=1)


def jl(code, d, b=0, a=0, name=None):
    return SimpleNamespace(hesap_kodu=code, hesap_adi=name, tarih=d, borc=b, alacak=a, raw_row_id=1, madde_no="1", fis_no="1")


def month(y, m, mizan=None, yevmiye=None):
    from calendar import monthrange
    return Context("THP_VUK", date(y, m, 1), date(y, m, monthrange(y, m)[1]), mizan, yevmiye)


def _import(session, c, p, files):
    for path in files.values():
        b = create_draft(session, c, p, path.name, path.read_bytes())
        confirm(session, b, b.doc_type, b.mapping, b.number_format)


def test_clean_generic_set(session, client_period, files_temiz):
    c, p = client_period
    _import(session, c, p, files_temiz)
    run = run_analysis(session, c, p)
    res = {r["id"]: r for r in run.rule_results}
    assert res["K-21"]["status"] == "gecti"
    assert res["K-22"]["status"] == "uygulanmadi"
    assert res["K-22D"]["status"] == "gecti"
    # 61.000 / (50.000 + 55.000 + 200.000)
    assert res["K-23"]["status"] == "bilgi" and "%20,00" in res["K-23"]["message"]
    assert run.reliability == "uygun"


def test_missing_mahsup_found_from_kebir(session, client_period, data_root):
    c, p = client_period
    files = synthetic_luca.generate(data_root / "luca_nomahsup", mizan=True, mahsup=False)
    _import(session, c, p, files)
    run = run_analysis(session, c, p)
    f = session.scalars(select(Finding).where(Finding.run_id == run.id, Finding.rule_id == "K-21")).one()
    assert f.amount == 35_000_00 and "kebir" in f.explanation


def test_previous_period_is_used(session, client_period, files_temiz, data_root):
    """Önceki dönemin belgeleri dönemler arası kontrollere girer."""
    c, p = client_period
    q2 = Period(client_id=c.id, baslangic=date(2026, 4, 1), bitis=date(2026, 6, 30), tur="uc_aylik")
    session.add(q2)
    session.flush()
    from denetci import synthetic
    prev_files = synthetic.generate(data_root / "q2", hatali=False)
    from openpyxl import load_workbook
    wb = load_workbook(prev_files["mizan"])
    wb.active["A1"] = "DEMO — SENTETİK VERİ (önceki dönem kopyası)"  # aynı dosya iki kez yüklenemez
    q2_path = data_root / "q2" / "q2_mizan.xlsx"
    wb.save(q2_path)
    b = create_draft(session, c, q2, q2_path.name, q2_path.read_bytes())
    confirm(session, b, b.doc_type, b.mapping, b.number_format)
    _import(session, c, p, files_temiz)
    res = {r["id"]: r for r in run_analysis(session, c, p).rule_results}
    assert res["K-22"]["status"] == "gecti"
    assert res["K-23"]["status"] == "gecti" and "04.2026: %20,00" in res["K-23"]["message"]


def test_mahsup_present_passes(session, client_period, data_root):
    c, p = client_period
    _import(session, c, p, synthetic_luca.generate(data_root / "luca_mahsup", mizan=True))
    run = run_analysis(session, c, p)
    assert {r["id"]: r["status"] for r in run.rule_results}["K-21"] == "gecti"


def test_k21_requires_month_end():
    ctx = Context("THP_VUK", date(2026, 9, 1), date(2026, 9, 15), [tb("191.01", b=100_00)], None)
    assert k21(ctx).status == "uygulanmadi"


def test_k22_continuity():
    prev = month(2026, 8, mizan=[tb("190.01", ab=1_000_00, b=4_000_00)])  # ay sonu 5.000 B
    cur = month(2026, 9, mizan=[tb("190.01", ab=4_000_00)])
    cur.previous = [prev]
    r = k22(cur)
    assert r.status == "bulgu" and r.drafts[0].amount == -1_000_00
    cur.mizan = [tb("190.01", ab=5_000_00)]
    assert k22(cur).status == "gecti"
    # Arada boşluk varsa (önceki dönem bitişik değil) karşılaştırılmaz.
    cur.previous = [month(2026, 7, mizan=[tb("190.01", b=5_000_00)])]
    assert k22(cur).status == "uygulanmadi"


def test_k22d_devreden_and_payable_same_month():
    d = date(2026, 9, 30)
    lines = [jl("190.01", d, b=1_000_00), jl("191.01", d, a=1_000_00),
             jl("360.01", d, a=500_00, name="ÖDENECEK KDV"), jl("191.01", d, b=500_00)]
    r = k22d(month(2026, 9, yevmiye=lines))
    assert r.status == "bulgu" and r.drafts[0].amount == 500_00
    # Tevkifat hesabı ayrı ödenir; bulgu üretmez.
    lines[2] = jl("360.06", d, a=500_00, name="2/10 TEVKİFATLI KDV")
    assert k22d(month(2026, 9, yevmiye=lines)).status == "gecti"


def _sales_month(m, kdv, sales):
    return month(2026, m, mizan=[tb("391.01", a=kdv), tb("600.01", a=sales), tb("120.01", b=kdv + sales)])


def test_k23_rate_deviation():
    prev = [_sales_month(m, 200_000_00, 1_000_000_00) for m in (8, 7, 6)]
    cur = _sales_month(9, 100_000_00, 1_000_000_00)
    cur.ranges["mizan"] = (cur.period_start, cur.period_end)
    for p in prev:
        p.ranges["mizan"] = (p.period_start, p.period_end)
    cur.previous = prev
    r = k23(cur)
    assert r.status == "bulgu" and r.drafts[0].amount == -100_000_00
    assert "%10,00" in r.drafts[0].explanation and "%20,00" in r.drafts[0].explanation
    near = _sales_month(9, 190_000_00, 1_000_000_00)
    near.previous = prev
    assert k23(near).status == "gecti"
    assert k23(month(2026, 9, mizan=[tb("100.01", b=1)])).status == "uygulanmadi"
