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
    page = tc.get(r.headers["location"]).text
    for text in ("Gelir tablosu özeti", "Bilanço özeti", "Oranlar", "Kontroller", "Net satışlar", "<svg"):
        assert text in page, text
    assert "Fiş denkliği" in page or "denk değil" in page
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
