"""Yeni arayüz akışları: DEMO, toplu yükleme (dönem otomatik), panel, mükellef listesi. Yalnızca sentetik veri."""

import re

from openpyxl import Workbook
from sqlalchemy import select

from denetci import db, synthetic_luca
from denetci.models import AnalysisRun, Client, Period
from tests.test_web import app_client, csrf, login  # noqa: F401


def test_empty_portfolio_offers_demo(app_client):
    login(app_client)
    page = app_client.get("/").text
    assert "Henüz mükellef yok" in page and 'action="/demo"' in page


def test_demo_dashboard(app_client):
    tc = app_client
    login(tc)
    r = tc.post("/demo", data={"csrf": csrf(tc, "/")}, follow_redirects=False)
    loc = r.headers["location"]
    page = tc.get(loc).text
    for text in ("Gösterge Paneli", "Dönem özeti", "Dikkat Gerektirenler", "Analizler ve Gereken Belgeler", "KDV Ön Kontrol",
                 "Kontroller", "Net satışlar", "ÖNEMLİ"):
        assert text in page, text
    assert "Fiş denkliği" in page or "denk değil" in page
    fin = tc.get(loc + "/finansal").text
    for text in ("Gelir tablosu özeti", "Bilanço özeti", "Oranlar", "<svg"):
        assert text in fin, text
    for sub in ("/kdv", "/mutabakat", "/gecici-vergi", "/belgeler"):
        assert tc.get(loc + sub).status_code == 200, sub
    assert tc.get(loc + "/olmayan").status_code == 404
    # İkinci tıklama yeni DEMO oluşturmaz.
    tc.post("/demo", data={"csrf": csrf(tc, "/")})
    with db.SessionLocal() as s:
        assert len(s.scalars(select(Client).where(Client.is_demo.is_(True))).all()) == 1


def test_multi_upload_creates_period_and_runs(app_client, data_root):
    tc = app_client
    login(tc)
    tok = csrf(tc, "/")
    r = tc.post("/mukellef/yeni", data={"csrf": tok, "unvan": "DEMO Yükleme"}, follow_redirects=False)
    cid = int(r.headers["location"].rsplit("/", 1)[1])
    files = synthetic_luca.generate(data_root / "multi", mizan=True)
    r = tc.post(f"/mukellef/{cid}/yukle", data={"csrf": tok},
                files=[("dosya", (p.name, p.read_bytes())) for p in files.values()], follow_redirects=False)
    assert r.status_code == 303 and "?donem=" in r.headers["location"]
    page = tc.get(r.headers["location"]).text
    assert page.count("kayıt aktarıldı") == 4
    with db.SessionLocal() as s:
        periods = s.scalars(select(Period).where(Period.client_id == cid)).all()
        assert [(p.baslangic.isoformat(), p.bitis.isoformat(), p.tur) for p in periods] == [("2026-07-01", "2026-09-30", "uc_aylik")]
        assert s.scalar(select(AnalysisRun).where(AnalysisRun.client_id == cid))
    # Aynı dosyalar tekrar yüklenirse çoğalmaz.
    r = tc.post(f"/mukellef/{cid}/yukle", data={"csrf": tok},
                files=[("dosya", (p.name, p.read_bytes())) for p in files.values()], follow_redirects=True)
    assert r.text.count("tekrar alınmadı") == 4


def test_client_list_import(app_client, data_root):
    tc = app_client
    login(tc)
    tok = csrf(tc, "/")
    tc.post("/mukellef/yeni", data={"csrf": tok, "unvan": "Mevcut Örnek Ltd. Şti.", "vkn": "1111111111"})
    wb = Workbook()
    ws = wb.active
    ws.append(["MÜKELLEF LİSTESİ (SENTETİK)"])
    ws.append(["Firma Kodu", "Unvanı", "Vergi Dairesi", "Vergi No"])
    ws.append(["001", "Mevcut Örnek Ltd. Şti.", "Kadıköy", "1111111111"])
    ws.append(["002", "Yeni Örnek A.Ş.", "Beşiktaş", 2222222222])
    ws.append(["003", "Yeni Örnek A.Ş.", "Beşiktaş", 2222222222])
    ws.append(["004", "Kişi Örnek", "Şişli", "12345678901"])
    path = data_root / "liste.xlsx"
    wb.save(path)
    r = tc.post("/mukellef/liste", data={"csrf": tok}, files={"dosya": ("liste.xlsx", path.read_bytes())})
    assert "2</strong> yeni mükellef" in r.text and "kayıtlı" in r.text and "tekrar" in r.text
    veri = re.search(r'name="veri" value="([^"]+)"', r.text).group(1)
    secs = re.findall(r'name="sec" value="(\d+)"', r.text)
    r = tc.post("/mukellef/liste/onayla", data={"csrf": tok, "veri": veri, "sec": secs}, follow_redirects=False)
    assert r.status_code == 303
    with db.SessionLocal() as s:
        clients = {c.unvan: c for c in s.scalars(select(Client))}
    assert set(clients) == {"Mevcut Örnek Ltd. Şti.", "Yeni Örnek A.Ş.", "Kişi Örnek"}
    assert clients["Yeni Örnek A.Ş."].vkn == "2222222222" and clients["Yeni Örnek A.Ş."].luca_kodu == "002"
    # Bozulmuş veri reddedilir.
    assert tc.post("/mukellef/liste/onayla", data={"csrf": tok, "veri": veri + "x", "sec": secs}).status_code == 400


def test_kdv_asks_for_devreden_and_uses_it(app_client, data_root):
    tc = app_client
    login(tc)
    tok = csrf(tc, "/")
    r = tc.post("/mukellef/yeni", data={"csrf": tok, "unvan": "DEMO KDV"}, follow_redirects=False)
    cid = int(r.headers["location"].rsplit("/", 1)[1])
    files = synthetic_luca.generate(data_root / "kdv", mizan=True)
    r = tc.post(f"/mukellef/{cid}/yukle", data={"csrf": tok},
                files=[("dosya", (p.name, p.read_bytes())) for p in files.values()], follow_redirects=False)
    pid = int(r.headers["location"].split("donem=")[1])
    page = tc.get(f"/mukellef/{cid}/kdv?donem={pid}").text
    assert "Önceki ayın devreden KDV" in page and "eksik" in page
    # Tutarın yanlış girilmesi bulgu üretir, boşaltınca kontrol yine uygulanmaz.
    tc.post(f"/mukellef/{cid}/donem/{pid}/devreden-kdv", data={"csrf": tok, "tutar": "999.999,99"})
    with db.SessionLocal() as s:
        p = s.get(Period, pid)
        assert p.devreden_kdv == 99999999
        run = s.scalars(select(AnalysisRun).where(AnalysisRun.period_id == pid).order_by(AnalysisRun.id.desc())).first()
        k22 = next(x for x in run.rule_results if x["id"] == "K-22")
        assert k22["status"] in ("bulgu", "uygulanmadi")
    page = tc.get(f"/mukellef/{cid}/kdv?donem={pid}").text
    assert "999.999,99" in page
    tc.post(f"/mukellef/{cid}/donem/{pid}/devreden-kdv", data={"csrf": tok, "tutar": ""})
    with db.SessionLocal() as s:
        assert s.get(Period, pid).devreden_kdv is None
    r = tc.post(f"/mukellef/{cid}/donem/{pid}/devreden-kdv", data={"csrf": tok, "tutar": "abc"}, follow_redirects=True)
    assert "Tutar okunamadı" in r.text


def _new_client(tc, tok, unvan):
    r = tc.post("/mukellef/yeni", data={"csrf": tok, "unvan": unvan}, follow_redirects=False)
    return int(r.headers["location"].rsplit("/", 1)[1])


def test_wrong_client_upload_is_held_and_redirected(app_client, data_root):
    from denetci.models import ImportBatch
    tc = app_client
    login(tc)
    tok = csrf(tc, "/")
    wrong = _new_client(tc, tok, "Başka Mobilya Ltd. Şti.")
    right = _new_client(tc, tok, "Örnek Ticaret A.Ş. (sentetik)")
    files = synthetic_luca.generate(data_root / "yanlis", mizan=True)
    r = tc.post(f"/mukellef/{wrong}/yukle", data={"csrf": tok},
                files=[("dosya", (p.name, p.read_bytes())) for p in files.values()], follow_redirects=False)
    assert r.headers["location"] == f"/mukellef/{wrong}/yukle/kontrol"
    page = tc.get(r.headers["location"]).text
    assert "başka bir firmaya ait" in page and "SENTETİK ÖRNEK TİCARET" in page and "(önerilen)" in page
    with db.SessionLocal() as s:
        assert not s.scalars(select(ImportBatch).where(ImportBatch.client_id == wrong)).all()
    ids = re.findall(r'name="hedef_([^"]+)"', page)
    assert len(ids) == 4
    r = tc.post(f"/mukellef/{wrong}/yukle/kontrol", data={"csrf": tok, **{f"hedef_{i}": str(right) for i in ids}},
                follow_redirects=False)
    assert r.headers["location"].startswith(f"/mukellef/{right}?donem=")
    with db.SessionLocal() as s:
        assert {b.doc_type for b in s.scalars(select(ImportBatch).where(ImportBatch.client_id == right))} == {"mizan", "yevmiye", "kebir", "muavin"}


def test_undo_clear_finalize_report(app_client, data_root):
    from denetci.models import ImportBatch
    tc = app_client
    login(tc)
    tok = csrf(tc, "/")
    cid = _new_client(tc, tok, "DEMO Örnek Ticaret")
    files = synthetic_luca.generate(data_root / "islem", mizan=True)
    r = tc.post(f"/mukellef/{cid}/yukle", data={"csrf": tok},
                files=[("dosya", (p.name, p.read_bytes())) for p in files.values()], follow_redirects=False)
    pid = int(r.headers["location"].split("donem=")[1])
    with db.SessionLocal() as s:
        muavin = s.scalar(select(ImportBatch).where(ImportBatch.client_id == cid, ImportBatch.doc_type == "muavin"))
    # Geri al: muavin çıkar, aynı dosya yeniden yüklenebilir.
    tc.post(f"/mukellef/{cid}/aktarim/{muavin.id}/geri-al", data={"csrf": tok})
    with db.SessionLocal() as s:
        assert s.get(ImportBatch, muavin.id).status == "iptal"
    r = tc.post(f"/mukellef/{cid}/yukle", data={"csrf": tok},
                files=[("dosya", (files["muavin"].name, files["muavin"].read_bytes()))], follow_redirects=True)
    assert "kayıt aktarıldı" in r.text
    # Not ve kesinleştirme
    tc.post(f"/mukellef/{cid}/donem/{pid}/not", data={"csrf": tok, "notlar": "Müşteriden banka ekstresi istenecek."})
    tc.post(f"/mukellef/{cid}/donem/{pid}/kesinlestir", data={"csrf": tok})
    page = tc.get(f"/mukellef/{cid}?donem={pid}").text
    assert "Kesinleşti" in page and "Kilidi aç" in page
    r = tc.post(f"/mukellef/{cid}/yukle", data={"csrf": tok},
                files=[("dosya", (files["mizan"].name, files["mizan"].read_bytes() + b""))], follow_redirects=True)
    r = tc.post(f"/mukellef/{cid}/donem/{pid}/temizle", data={"csrf": tok}, follow_redirects=True)
    assert "kesinleştirilmiş" in r.text
    rep = tc.get(f"/mukellef/{cid}/rapor?donem={pid}").text
    for text in ("Dönem Kontrol Raporu", "KESİN", "Kullanılan belgeler", "Bulgular", "Kontrol sonuçları",
                 "Müşteriden banka ekstresi istenecek.", "window.print()"):
        assert text in rep, text
    # Kilidi aç, temizle
    tc.post(f"/mukellef/{cid}/donem/{pid}/kilidi-ac", data={"csrf": tok})
    tc.post(f"/mukellef/{cid}/donem/{pid}/temizle", data={"csrf": tok})
    with db.SessionLocal() as s:
        assert not s.scalars(select(ImportBatch).where(ImportBatch.client_id == cid, ImportBatch.status != "iptal")).all()
        assert not s.scalars(select(AnalysisRun).where(AnalysisRun.period_id == pid)).all()
    assert tc.get(f"/mukellef/{cid}?donem={pid}").status_code == 200


def test_gecici_vergi_page_and_inputs(app_client):
    tc = app_client
    login(tc)
    tok = csrf(tc, "/")
    r = tc.post("/demo", data={"csrf": tok}, follow_redirects=False)
    loc = r.headers["location"]
    page = tc.get(loc + "/gecici-vergi").text
    assert "3. dönem (Temmuz–Eylül)" in page and "17.11.2026" in page and "Tahmini geçici vergi matrahı" in page
    assert "K-27" in page
    with db.SessionLocal() as s:
        p = s.scalars(select(Period)).first()
        cid = p.client_id
    r = tc.post(f"/mukellef/{cid}/donem/{p.id}/gecici-vergi",
                data={"csrf": tok, "kkeg": "10.000,00", "istisna": "", "gecmis_zarar": "", "indirim": "", "onceki_odenen": "5.000,00",
                      "tur": "kurum", "oran": ""}, follow_redirects=True)
    assert "Bilgiler kaydedildi" in r.text and "10.000,00" in r.text
    from denetci.gecici import hesapla
    from denetci.checks.engine import build_context
    with db.SessionLocal() as s:
        p = s.get(Period, p.id)
        c = s.get(Client, cid)
        h = hesapla(build_context(s, c, p)[0], p, c)
    assert h.matrah == h.ticari_kar + 1_000_000 and h.oran == 0.25
    assert h.hesaplanan == round(h.matrah * 0.25) and h.odenecek == max(h.hesaplanan - 500_000, 0)
    r = tc.post(f"/mukellef/{cid}/donem/{p.id}/gecici-vergi", data={"csrf": tok, "kkeg": "abc"}, follow_redirects=True)
    assert "Okunamayan tutar" in r.text
    assert "Geçici vergi ön hesaplaması" in tc.get(f"/mukellef/{cid}/rapor?donem={p.id}").text


def test_period_selector_groups_months_and_quarters(app_client):
    tc = app_client
    login(tc)
    tok = csrf(tc, "/")
    loc = tc.post("/demo", data={"csrf": tok}, follow_redirects=False).headers["location"]
    page = tc.get(loc).text
    assert '<optgroup label="Aylık · 2026">' in page and '<optgroup label="3 aylık · 2026">' in page
    assert "3. dönem: Temmuz–Eylül 2026 ●" in page  # DEMO dosyaları bu dönemde
    cid = int(loc.rsplit("/", 1)[1].split("?")[0])
    r = tc.get(f"/mukellef/{cid}/donem-sec?bas=2026-08-01&bit=2026-08-31&geri=/mukellef/{cid}/kdv", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith(f"/mukellef/{cid}/kdv?donem=")
    with db.SessionLocal() as s:
        p = s.scalars(select(Period).where(Period.client_id == cid, Period.tur == "aylik")).one()
        assert (p.baslangic.isoformat(), p.bitis.isoformat()) == ("2026-08-01", "2026-08-31")
    # Aynı dönem ikinci kez oluşturulmaz; dışarıya yönlendirme yapılmaz.
    r = tc.get(f"/mukellef/{cid}/donem-sec?bas=2026-08-01&bit=2026-08-31&geri=https://ornek.com", follow_redirects=False)
    assert r.headers["location"] == f"/mukellef/{cid}?donem={p.id}"
    assert "Ağustos 2026" in tc.get(r.headers["location"]).text
