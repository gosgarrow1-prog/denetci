"""Beyanname PDF okuma ve kullanımı. Yalnızca sentetik PDF'ler (tests/pdfmaker.py)."""

from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from denetci import db
from denetci.checks import gecici_rules, kdv
from denetci.checks.rules import Context
from denetci.importer.beyanname import BeyannameHatasi, parse
from denetci.models import AnalysisRun, Client, Declaration, Period
from tests.pdfmaker import gecici_pdf, kdv_pdf, make_pdf
from tests.test_web import app_client, csrf, login  # noqa: F401

UNVAN = "ORNEK KAGIT AMBALAJ SAN TIC LTD STI"


def test_parse_kdv_and_gecici():
    b = parse(kdv_pdf(ay="Ağustos", sonraki_devreden=4_321_00))
    assert (b.tur, b.yil, b.ay, b.vkn) == ("kdv", 2026, 8, "1234567890")
    assert b.unvan == UNVAN and b.vergi_dairesi == "MERKEZ"
    assert b.alanlar["sonraki_devreden"] == 432100 and b.alanlar["hesaplanan"] == 3_600_000
    assert b.alanlar["indirimler_toplami"] == 4_000_000
    g = parse(gecici_pdf(donem=2))
    assert (g.tur, g.alt, g.yil, g.ay) == ("gecici", "kurum", 2026, 6)
    assert g.alanlar["hesaplanan"] == 10_000_000 and g.alanlar["onceki"] == 3_000_000 and g.alanlar["ticari_kar"] == 40_000_000


def test_parse_rejects_other_pdf():
    with pytest.raises(BeyannameHatasi):
        parse(make_pdf([[(40, "Fatura")], [(40, "Toplam"), (300, "100,00")]]))
    with pytest.raises(BeyannameHatasi):
        parse(b"%PDF-1.4 bozuk")


def _new_client(tc, tok, unvan, vkn=""):
    r = tc.post("/mukellef/yeni", data={"csrf": tok, "unvan": unvan, "vkn": vkn}, follow_redirects=False)
    return int(r.headers["location"].rsplit("/", 1)[1])


def test_upload_declarations_and_undo(app_client):
    tc = app_client
    login(tc)
    tok = csrf(tc, "/")
    cid = _new_client(tc, tok, "Örnek Kağıt Ambalaj San. Tic. Ltd. Şti.")
    r = tc.post(f"/mukellef/{cid}/yukle", data={"csrf": tok},
                files=[("dosya", ("kdv.pdf", kdv_pdf(ay="Temmuz"))), ("dosya", ("gv.pdf", gecici_pdf()))], follow_redirects=False)
    assert "/belgeler" in r.headers["location"]
    page = tc.get(r.headers["location"]).text
    assert "KDV beyannamesi (Temmuz 2026) okundu" in page and "Geçici vergi beyannamesi (2026 / 2. dönem) okundu" in page
    assert "Beyannameler" in page and "kdv.pdf" in page
    with db.SessionLocal() as s:
        c = s.get(Client, cid)
        assert c.vkn == "1234567890"  # boş VKN beyannameden dolduruldu
        decls = s.scalars(select(Declaration).where(Declaration.client_id == cid)).all()
        assert sorted((d.tur, d.ay) for d in decls) == [("gecici", 6), ("kdv", 7)]
        kid = next(d.id for d in decls if d.tur == "kdv")
    # Aynı dosya tekrar alınmaz.
    r = tc.post(f"/mukellef/{cid}/yukle", data={"csrf": tok}, files=[("dosya", ("kdv.pdf", kdv_pdf(ay="Temmuz")))],
                follow_redirects=True)
    assert "tekrar alınmadı" in r.text
    # Geri alınca yeniden yüklenebilir.
    tc.post(f"/mukellef/{cid}/beyanname/{kid}/geri-al", data={"csrf": tok})
    with db.SessionLocal() as s:
        assert s.get(Declaration, kid).durum == "iptal"
    r = tc.post(f"/mukellef/{cid}/yukle", data={"csrf": tok}, files=[("dosya", ("kdv.pdf", kdv_pdf(ay="Temmuz")))],
                follow_redirects=True)
    assert "okundu" in r.text
    # Bozuk PDF hata verir.
    r = tc.post(f"/mukellef/{cid}/yukle", data={"csrf": tok}, files=[("dosya", ("x.pdf", b"%PDF-1.4 bozuk"))],
                follow_redirects=True)
    assert "PDF okunamadı" in r.text or "PDF'te metin bulunamadı" in r.text


def test_declaration_for_wrong_client_is_held(app_client):
    tc = app_client
    login(tc)
    tok = csrf(tc, "/")
    right = _new_client(tc, tok, "Örnek Kağıt Ambalaj Ltd. Şti.", vkn="1234567890")
    wrong = _new_client(tc, tok, "Örnek Kağıt Ambalaj Ltd. Şti. Şube", vkn="9999999999")
    # Unvan benzese de VKN farklıysa sorulur ve VKN'si tutan mükellef önerilir.
    r = tc.post(f"/mukellef/{wrong}/yukle", data={"csrf": tok}, files=[("dosya", ("kdv.pdf", kdv_pdf()))],
                follow_redirects=True)
    assert "başka bir firmaya ait" in r.text and "VKN 1234567890" in r.text
    assert f'<option value="{right}" selected>' in r.text
    with db.SessionLocal() as s:
        assert not s.scalars(select(Declaration)).all()


def test_kdv_page_uses_previous_month_declaration(app_client):
    tc = app_client
    login(tc)
    tok = csrf(tc, "/")
    cid = _new_client(tc, tok, UNVAN)
    tc.post(f"/mukellef/{cid}/yukle", data={"csrf": tok},
            files=[("dosya", ("kdv7.pdf", kdv_pdf(ay="Temmuz", sonraki_devreden=4_321_00)))])
    with db.SessionLocal() as s:
        p = Period(client_id=cid, baslangic=date(2026, 8, 1), bitis=date(2026, 8, 31), tur="aylik")
        s.add(p)
        s.commit()
        pid = p.id
    page = tc.get(f"/mukellef/{cid}/kdv?donem={pid}").text
    assert "Temmuz 2026 beyannamesinden okundu" in page and "4.321,00" in page


def _line(code, borc=0, alacak=0, d=date(2026, 8, 15)):
    return SimpleNamespace(tarih=d, hesap_kodu=code, borc=borc, alacak=alacak, hesap_adi=None, raw_row_id=1,
                           madde_no="1", fis_no="1")


def test_k24_compares_declaration_with_books():
    ctx = Context("THP_VUK", date(2026, 8, 1), date(2026, 8, 31), None,
                  [_line("391.01", alacak=3_600_000), _line("191.01", borc=2_900_000), _line("120", borc=21_600_000)])
    assert kdv.k24(ctx).status == "uygulanmadi"
    ctx.kdv_beyan = parse(kdv_pdf()).alanlar
    r = kdv.k24(ctx)
    assert r.status == "bulgu" and [d.hesap_kodu for d in r.drafts] == ["191"]
    assert r.drafts[0].amount == -100_000  # defter 29.000 < beyan 30.000
    ctx.yevmiye[1].borc = 3_000_000
    assert kdv.k24(ctx).status == "gecti"


def test_k22_uses_declared_devreden_source():
    ctx = Context("THP_VUK", date(2026, 8, 1), date(2026, 8, 31), None, None,
                  kebir=[_line("190", borc=500_000, d=date(2026, 7, 31))])
    ctx.devreden_kdv, ctx.devreden_kaynak = 400_000, "beyanname"
    r = kdv.k22(ctx)
    assert r.status == "bulgu" and "beyannamesindeki (PDF)" in r.drafts[0].explanation


def test_k28_and_gv_mahsup_from_previous_declaration(app_client):
    tc = app_client
    login(tc)
    tok = csrf(tc, "/")
    loc = tc.post("/demo", data={"csrf": tok}, follow_redirects=False).headers["location"]
    with db.SessionLocal() as s:
        p = s.scalars(select(Period)).first()
        c = s.get(Client, p.client_id)
        unvan, cid, pid = c.unvan, c.id, p.id
    vkn = "5555555555"
    tc.post(f"/mukellef/{cid}/yukle", data={"csrf": tok},
            files=[("dosya", ("gv2.pdf", gecici_pdf(donem=2, vkn=vkn, unvan=(unvan, ""), hesaplanan=12_345_00, onceki=0))),
                   ("dosya", ("gv3.pdf", gecici_pdf(donem=3, vkn=vkn, unvan=(unvan, ""), ticari_kar=1_00)))])
    from denetci import beyannameler
    from denetci.checks.engine import build_context
    from denetci.gecici import hesapla
    with db.SessionLocal() as s:
        p, c = s.get(Period, pid), s.get(Client, cid)
        assert len(beyannameler.aktif(s, cid)) == 2
        h = hesapla(build_context(s, c, p)[0], p, c, beyannameler.aktif(s, cid))
        assert h.mahsup == 12_345_00 and h.mahsup_kaynak == "2026 / 2. dönem beyannamesi"
        assert h.beyanname["ticari_kar"] == 100
        run = s.scalars(select(AnalysisRun).where(AnalysisRun.period_id == pid).order_by(AnalysisRun.id.desc())).first()
        k28 = next(x for x in run.rule_results if x["id"] == "K-28")
        assert k28["status"] == "bulgu"
    page = tc.get(loc + "/gecici-vergi").text
    assert "2026 / 2. dönem beyannamesinden okundu" in page and "Verilen beyanname ile karşılaştırma" in page
    assert "K-28" in page


def test_k28_passes_when_profit_matches():
    kebir = [_line("600", alacak=500_000_00, d=date(2026, 5, 1)), _line("770", borc=100_000_00, d=date(2026, 5, 1))]
    ctx = Context("THP_VUK", date(2026, 4, 1), date(2026, 6, 30), None, None, kebir=kebir)
    ctx.gv_beyan = parse(gecici_pdf(ticari_kar=400_000_00)).alanlar
    assert gecici_rules.k28(ctx).status == "gecti"
    ctx.gv_beyan = parse(gecici_pdf(ticari_kar=390_000_00)).alanlar
    r = gecici_rules.k28(ctx)
    assert r.status == "bulgu" and r.drafts[0].amount == 10_000_00
