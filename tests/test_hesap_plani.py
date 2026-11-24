"""Tekdüzen Hesap Planı uyumu kontrolleri (K-40..K-44)."""

from datetime import date
from types import SimpleNamespace

from denetci.checks.hesap_plani import k40, k41, k42, k43, k44
from denetci.checks.rules import Context

_n = 0
D = date(2026, 9, 10)


def J(code, borc=0, alacak=0, ad=None):
    global _n
    _n += 1
    return SimpleNamespace(tarih=D, hesap_kodu=code, borc=borc, alacak=alacak, hesap_adi=ad, raw_row_id=_n,
                           fis_no="1", madde_no="1", aciklama=None, evrak_no=None)


def M(code, borc=0, alacak=0, ad="X", leaf=True):
    return SimpleNamespace(hesap_kodu=code, hesap_adi=ad, is_leaf=leaf, raw_row_id=1, acilis_borc=None,
                           acilis_alacak=None, borc=borc, alacak=alacak, bakiye_borc=max(borc - alacak, 0),
                           bakiye_alacak=max(alacak - borc, 0))


def C(yev=None, mizan=None):
    return Context("THP_VUK", date(2026, 9, 1), date(2026, 9, 30), mizan, yev)


def test_tekduzende_olmayan_kod_ve_grup_kaydi():
    yev = [J("120.01", 100_00), J("104.01", alacak=50_00), J("12", alacak=50_00), J("950.01", 10_00), J("X10", 1_00)]
    r = k40(C(yev))
    assert r.status == "bulgu"
    assert {d.hesap_kodu: d.key for d in r.drafts} == {"104": "thp-kod-104", "12": "thp-kod-12", "X10": "thp-kod-X10"}
    # Mizanın grup başlık satırları sayılmaz
    assert k40(C(mizan=[M("1", leaf=False), M("10", leaf=False), M("100", leaf=False), M("100.01", 5_00)])).status == "gecti"
    assert k40(C()).status == "calistirilamadi"


def test_ana_hesap_adi():
    miz = [M("336", ad="ORTAKLARA BORÇLAR", leaf=False), M("320", ad="SATICILAR", leaf=False),
           M("102", ad="BANKALAR HESABI", leaf=False), M("120.01", ad="ABC LTD")]
    r = k41(C(mizan=miz))
    assert [d.hesap_kodu for d in r.drafts] == ["336"]


def test_alt_hesap_duzeni():
    r = k42(C([J("120.01", 100_00), J("120", 30_00), J("600", alacak=130_00)]))
    assert [d.hesap_kodu for d in r.drafts] == ["120"] and r.drafts[0].amount == 30_00
    assert k42(C([J("600", alacak=10_00), J("100.01", 10_00)])).status == "gecti"


def test_gelir_gider_ters_bakiye():
    miz = [M("600.01", borc=10_00, alacak=100_00), M("601.01", borc=50_00), M("770.01", 20_00),
           M("632.01", alacak=5_00), M("690.01", borc=10_00)]
    r = k43(C(mizan=miz))
    assert {d.hesap_kodu for d in r.drafts} == {"601", "632"}


def test_7a_7b_birlikte():
    assert k44(C([J("770.01", 10_00), J("100.01", alacak=10_00)])).status == "gecti"
    r = k44(C([J("770.01", 10_00), J("794.01", 10_00), J("100.01", alacak=20_00)]))
    assert r.status == "bulgu"
