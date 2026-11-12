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


def Y(yev, ytd=None, start=date(2026, 9, 1)):
    ctx = Context("THP_VUK", start, date(2026, 9, 30), None, yev)
    ctx.ytd = ytd if ytd is not None else yev
    return ctx


ACILIS = [J(date(2026, 1, 1), "102.01", borc=100_000_00, fis="A"), J(date(2026, 1, 1), "500.01", alacak=100_000_00, fis="A")]


def test_ortaklardan_alacak_adat():
    from denetci.checks.risk import k14
    ver = [J(date(2026, 9, 11), "131.01", borc=365_000_00, fis="5"), J(date(2026, 9, 11), "102.01", alacak=365_000_00, fis="5")]
    r = k14(Y(ver, ACILIS + ver))
    assert r.status == "bulgu"
    # 20 gün × 365.000 TL / 30 gün
    assert r.drafts[0].amount == 365_000_00 * 20 // 30
    assert "yaklaşık 2.000,00 TL faiz" in r.drafts[0].explanation  # %10 × 365.000 × 20/365
    faiz = [J(date(2026, 9, 30), "131.01", borc=5_000_00, fis="6", aciklama="Eylül adat faizi"),
            J(date(2026, 9, 30), "642.01", alacak=5_000_00, fis="6")]
    assert k14(Y(ver + faiz, ACILIS + ver + faiz)).status == "gecti"
    assert k14(Y([], ACILIS)).status == "gecti"
    assert k14(C([])).status == "uygulanmadi"


def test_ortulu_sermaye():
    from denetci.checks.risk import k15
    al = lambda t: [J(date(2026, 9, 5), "102.01", borc=t, fis="7"), J(date(2026, 9, 5), "331.01", alacak=t, fis="7")]
    assert k15(Y(al(300_000_00), ACILIS + al(300_000_00))).status == "gecti"
    r = k15(Y(al(450_000_00), ACILIS + al(450_000_00)))
    assert r.status == "bulgu" and r.drafts[0].amount == 150_000_00
    # Dönem sonunda geri ödense de dönem içi en yüksek bakiye esas alınır.
    geri = [J(date(2026, 9, 25), "331.01", borc=450_000_00, fis="8"), J(date(2026, 9, 25), "102.01", alacak=450_000_00, fis="8")]
    yev = al(450_000_00) + geri
    assert k15(Y(yev, ACILIS + yev)).status == "bulgu"


def test_binek_oto():
    from denetci.checks.risk import k16
    d = date(2026, 9, 10)

    def L(code, ad, borc=0, alacak=0, fis="1"):
        j = J(d, code, borc, alacak, fis)
        j.hesap_adi = ad
        return j
    gider = [L("770.05", "TAŞIT AKARYAKIT GİDERLERİ", borc=10_000_00), L("770.01", "KIRTASİYE", borc=3_000_00),
             L("320.01", "SATICI", alacak=13_000_00)]
    r = k16(Y(gider))
    assert r.status == "bulgu" and r.drafts[0].amount == 3_000_00
    ayrilmis = [L("770.05", "TAŞIT AKARYAKIT GİDERLERİ", borc=7_000_00), L("689.01", "BİNEK OTO KKEG", borc=3_000_00),
                L("320.01", "SATICI", alacak=10_000_00)]
    assert k16(Y(ayrilmis)).status == "gecti"
    assert k16(Y(gider[1:])).status == "gecti"


def test_amortisman_ve_yansitma():
    from denetci.checks.risk import k17, k18
    assert k17(C(mizan=[M("255.01", 100_000_00), M("257.01", -40_000_00)])).status == "gecti"
    r = k17(C(mizan=[M("255.01", 100_000_00), M("257.01", -130_000_00)]))
    assert r.status == "bulgu" and r.drafts[0].amount == 30_000_00
    assert k17(C()).status == "uygulanmadi"
    # Ara dönemde hiç yansıtma yoksa yalnızca bilgi; kısmi yansıtma bulgudur.
    assert k18(C(mizan=[M("770.01", 50_000_00)])).status == "bilgi"
    r = k18(C(mizan=[M("770.01", 50_000_00), M("771", -45_000_00), M("760.01", 10_000_00), M("761", -10_000_00)]))
    assert r.status == "bulgu" and [(d.hesap_kodu, d.amount) for d in r.drafts] == [("770", 5_000_00)]
    yil = Context("THP_VUK", date(2026, 12, 1), date(2026, 12, 31), [M("770.01", 50_000_00)], None)
    assert k18(yil).status == "bulgu"


def MH(code, net, borc=0, alacak=0):
    m = M(code, net)
    m.borc, m.alacak = borc, alacak
    return m


def test_donemsellik_supheli_avans_kapanis():
    from denetci.checks.risk import k19, k20, k31, k32
    # 180'de bakiye var, gidere aktarım yok → bulgu; 380'den gelire aktarım var → sorun yok
    r = k19(C(mizan=[MH("180.01", 12_000_00, borc=12_000_00), MH("380.01", -6_000_00, borc=1_000_00, alacak=7_000_00)]))
    assert r.status == "bulgu" and [d.hesap_kodu for d in r.drafts] == ["180"]
    assert k19(C(mizan=[MH("180.01", 11_000_00, borc=12_000_00, alacak=1_000_00)])).status == "gecti"
    # Karşılık şüpheli alacaktan fazla → bulgu; eksik karşılık → bilgi
    assert k20(C(mizan=[M("128.01", 1_000_00), M("129.01", -2_000_00)])).status == "bulgu"
    assert k20(C(mizan=[M("128.01", 1_000_00)])).status == "bilgi"
    assert k20(C(mizan=[M("120.01", 1_000_00)])).status == "gecti"
    # Hareketsiz avans
    r = k31(C(mizan=[MH("159.01", 5_000_00), MH("340.01", -3_000_00, borc=1_000_00)]))
    assert r.status == "bulgu" and [d.hesap_kodu for d in r.drafts] == ["159"]
    # Kapanış: 692 ≠ 690 − 691
    assert k32(C(mizan=[M("120.01", 1_00)])).status == "uygulanmadi"
    r = k32(C(mizan=[M("690.01", -100_000_00), M("691.01", 25_000_00), M("692.01", -70_000_00), M("370.01", -25_000_00)]))
    assert r.status == "bulgu" and [d.hesap_kodu for d in r.drafts] == ["692"]
    assert k32(C(mizan=[M("690.01", -100_000_00), M("691.01", 25_000_00), M("692.01", -75_000_00),
                        M("370.01", -25_000_00), M("590.01", -75_000_00)])).status == "gecti"
