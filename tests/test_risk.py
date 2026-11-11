"""Hata ve risk taraması kontrolleri (K-10..K-13)."""

from datetime import date
from types import SimpleNamespace

from denetci.checks.risk import k10, k11, k12, k13, tatil_adi
from denetci.checks.rules import Context

_n = 0


def J(d, code, borc=0, alacak=0, fis="1", aciklama=None):
    global _n
    _n += 1
    return SimpleNamespace(tarih=d, hesap_kodu=code, borc=borc, alacak=alacak, hesap_adi=None, raw_row_id=_n,
                           fis_no=fis, madde_no=fis, aciklama=aciklama, evrak_no=None)


def M(code, net):
    return SimpleNamespace(hesap_kodu=code, hesap_adi="X", is_leaf=True, raw_row_id=1, acilis_borc=None, acilis_alacak=None,
                           borc=0, alacak=0, bakiye_borc=max(net, 0), bakiye_alacak=max(-net, 0))


def C(yev=None, mizan=None):
    return Context("THP_VUK", date(2026, 9, 1), date(2026, 9, 30), mizan, yev)


def test_ters_bakiye():
    r = k10(C(mizan=[M("120.01", 500_00), M("120.02", -200_00), M("320.01", 300_00), M("320.02", -900_00),
                     M("257.01", -100_00), M("100.01", -50_00), M("580.01", 10_00)]))
    assert r.status == "bulgu"
    assert {d.hesap_kodu: d.amount for d in r.drafts} == {"120": 200_00, "320": 300_00}


def test_ters_bakiye_veri_yoksa_calismaz():
    assert k10(C()).status == "calistirilamadi"
    assert k10(C(mizan=[M("120.01", 5_00)])).status == "gecti"


def test_kasa_siniri_ve_banka_virmani():
    d = date(2026, 9, 10)
    yev = [J(d, "100.01", borc=45_000_00, fis="1"), J(d, "120.01", alacak=45_000_00, fis="1"),
           J(d, "100.01", borc=80_000_00, fis="2"), J(d, "102.01", alacak=80_000_00, fis="2"),   # bankadan çekilen: sayılmaz
           J(d, "320.01", borc=31_000_00, fis="3"), J(d, "100.01", alacak=31_000_00, fis="3"),
           J(d, "100.01", borc=30_000_00, fis="4"), J(d, "120.01", alacak=30_000_00, fis="4")]   # sınırda: sayılmaz
    r = k11(C(yev))
    assert {d.key: d.amount for d in r.drafts} == {"kasa-tahsilat": 45_000_00, "kasa-odeme": 31_000_00}
    assert k11(C(yev[2:4])).status == "gecti"


def test_tatil_gunu():
    assert tatil_adi(date(2026, 9, 6)) == "pazar"
    assert tatil_adi(date(2026, 10, 29)) == "resmî tatil"
    assert tatil_adi(date(2026, 3, 21)) == "bayram"
    assert tatil_adi(date(2026, 9, 7)) is None
    yev = [J(date(2026, 9, 6), "770.01", borc=1_000_00), J(date(2026, 9, 7), "770.01", borc=5_000_00),
           J(date(2026, 9, 13), "780.01", borc=700_00), J(date(2026, 9, 13), "100.01", alacak=1_000_00)]
    r = k12(C(yev))
    assert r.status == "bulgu" and r.drafts[0].amount == 1_000_00
    # Ay sonu pazar gününe denk gelirse tahakkuk kayıtları sayılmaz.
    ctx = Context("THP_VUK", date(2026, 5, 1), date(2026, 5, 31), None, [J(date(2026, 5, 31), "770.01", borc=1_000_00)])
    assert k12(ctx).status == "gecti"


def test_mukerrer_fatura():
    no = "ABC2026000000123"
    d1, d2 = date(2026, 9, 3), date(2026, 9, 18)
    fatura = lambda d, fis: [J(d, "770.01", borc=1_000_00, fis=fis, aciklama=f"EF {no} FİRMA"),
                             J(d, "191.01", borc=200_00, fis=fis, aciklama=f"EF {no} FİRMA"),
                             J(d, "320.01", alacak=1_200_00, fis=fis, aciklama=f"EF {no} FİRMA")]
    odeme = [J(d2, "320.01", borc=1_200_00, fis="9", aciklama=f"{no} fatura ödemesi"),
             J(d2, "102.01", alacak=1_200_00, fis="9", aciklama=f"{no} fatura ödemesi")]
    assert k13(C(fatura(d1, "1") + odeme)).status == "gecti"
    r = k13(C(fatura(d1, "1") + fatura(d2, "2") + odeme))
    assert r.status == "bulgu" and len(r.drafts) == 1
    assert r.drafts[0].severity == "Yüksek" and r.drafts[0].amount == 1_200_00
    assert k13(C([J(d1, "770.01", borc=1_00), J(d1, "100.01", alacak=1_00)])).status == "uygulanmadi"
