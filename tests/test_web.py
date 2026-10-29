import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from denetci import db
from denetci.auth import hash_password, new_totp_secret, totp
from denetci.models import Client, Finding, ImportBatch, Period, User
from denetci.web.app import create_app


@pytest.fixture()
def app_client(data_root):
    secret = new_totp_secret()
    with db.SessionLocal() as s:
        s.add(User(username="burak", password_hash=hash_password("cok-gizli-parola-123"), totp_secret=secret))
        s.commit()
    tc = TestClient(create_app())
    tc.secret = secret
    return tc


def csrf(tc, url):
    return re.search(r'name="csrf" value="([^"]+)"', tc.get(url).text).group(1)


def login(tc):
    tok = csrf(tc, "/giris")
    r = tc.post("/giris", data={"csrf": tok, "kullanici": "burak", "parola": "cok-gizli-parola-123", "kod": totp(tc.secret)},
                follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"


def test_requires_login(app_client):
    r = app_client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/giris"


def test_wrong_totp_rejected(app_client, monkeypatch):
    monkeypatch.setenv("DENETCI_TELEFON_KODU", "1")
    tok = csrf(app_client, "/giris")
    r = app_client.post("/giris", data={"csrf": tok, "kullanici": "burak", "parola": "cok-gizli-parola-123", "kod": "000000"})
    assert "hatalı" in r.text
    assert app_client.get("/", follow_redirects=False).status_code == 303


def test_password_only_by_default(app_client):
    assert 'name="kod"' not in app_client.get("/giris").text
    tok = csrf(app_client, "/giris")
    r = app_client.post("/giris", data={"csrf": tok, "kullanici": "burak", "parola": "yanlis-parola-12345"})
    assert "Kullanıcı adı veya parola hatalı" in r.text
    tok = csrf(app_client, "/giris")
    r = app_client.post("/giris", data={"csrf": tok, "kullanici": "burak", "parola": "cok-gizli-parola-123"},
                        follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"


def test_no_signup_route(app_client):
    assert app_client.get("/kayit").status_code == 404


def test_csrf_required(app_client):
    login(app_client)
    r = app_client.post("/mukellef/yeni", data={"unvan": "X"})
    assert r.status_code == 403
    r = app_client.post("/mukellef/yeni", data={"unvan": "X", "csrf": "yanlis"})
    assert r.status_code == 403


def test_end_to_end_upload_check_and_trace(app_client, files_hatali):
    tc = app_client
    login(tc)
    tok = csrf(tc, "/")
    r = tc.post("/mukellef/yeni", data={"csrf": tok, "unvan": "DEMO Örnek"}, follow_redirects=False)
    cid = int(r.headers["location"].rsplit("/", 1)[1])
    r = tc.post(f"/mukellef/{cid}/donem", data={"csrf": tok, "baslangic": "2026-07-01", "bitis": "2026-09-30", "tur": "uc_aylik"},
                follow_redirects=False)
    pid = int(r.headers["location"].rsplit("/", 1)[1])
    for kind, path in files_hatali.items():
        r = tc.post(f"/mukellef/{cid}/donem/{pid}/yukle", data={"csrf": tok},
                    files={"dosya": (path.name, path.read_bytes())}, follow_redirects=False)
        bpath = r.headers["location"]
        page = tc.get(bpath).text
        assert "Önizleme" in page and "ara toplam" in page
        bid = int(bpath.rsplit("/", 1)[1])
        with db.SessionLocal() as s:
            mapping = s.get(ImportBatch, bid).mapping
        form = {"csrf": tok, "doc_type": kind, "number_format": "tr", **{f"map_{k}": str(v) for k, v in mapping.items()}}
        r = tc.post(bpath + "/onayla", data=form, follow_redirects=False)
        assert r.status_code == 303
        assert "Aktarılan kayıt" in tc.get(bpath).text
    r = tc.post(f"/mukellef/{cid}/donem/{pid}/analiz", data={"csrf": tok}, follow_redirects=False)
    run_url = r.headers["location"]
    page = tc.get(run_url).text
    assert "Kritik veri sorunu var" in page and "Merkez Kasa alacak bakiyesi veriyor" in page
    fid = re.search(rf'/mukellef/{cid}/bulgu/(\d+)">100\.01', page).group(1)
    detail = tc.get(f"/mukellef/{cid}/bulgu/{fid}").text
    assert "48.250,00" in detail and "satır" in detail and "Y-2026-0912" in detail
    # Durum değişikliği
    r = tc.post(f"/mukellef/{cid}/bulgu/{fid}/durum", data={"csrf": tok, "durum": "inceleniyor", "not": "Banka ekstresi istendi"},
                follow_redirects=False)
    assert r.status_code == 303
    assert "Banka ekstresi istendi" in tc.get(f"/mukellef/{cid}/bulgu/{fid}").text


def test_client_isolation(app_client, files_hatali):
    """B mükellefinin adresiyle A'nın bulgusuna, dönemine veya aktarımına erişilemez."""
    from denetci.checks.engine import run_analysis
    from denetci.importer.load import confirm, create_draft

    with db.SessionLocal() as s:
        a = Client(unvan="A")
        b = Client(unvan="B")
        s.add_all([a, b])
        s.flush()
        pa = Period(client_id=a.id, baslangic=date(2026, 7, 1), bitis=date(2026, 9, 30), tur="uc_aylik")
        s.add(pa)
        s.flush()
        batch = create_draft(s, a, pa, "m.xlsx", files_hatali["mizan"].read_bytes())
        confirm(s, batch, "mizan", batch.mapping, "tr")
        run = run_analysis(s, a, pa)
        f = s.query(Finding).filter(Finding.run_id == run.id).first()
        s.commit()
        ids = (a.id, b.id, pa.id, batch.id, run.id, f.id)
    a_id, b_id, pa_id, batch_id, run_id, f_id = ids
    login(app_client)
    assert app_client.get(f"/mukellef/{a_id}/bulgu/{f_id}").status_code == 200
    for url in (f"/mukellef/{b_id}/bulgu/{f_id}", f"/mukellef/{b_id}/donem/{pa_id}",
                f"/mukellef/{b_id}/aktarim/{batch_id}", f"/mukellef/{b_id}/analiz/{run_id}"):
        assert app_client.get(url).status_code == 404, url


def test_luca_upload_without_mapping(app_client, data_root):
    from denetci import synthetic_luca

    tc = app_client
    login(tc)
    tok = csrf(tc, "/")
    r = tc.post("/mukellef/yeni", data={"csrf": tok, "unvan": "DEMO Luca"}, follow_redirects=False)
    cid = int(r.headers["location"].rsplit("/", 1)[1])
    r = tc.post(f"/mukellef/{cid}/donem", data={"csrf": tok, "baslangic": "2026-07-01", "bitis": "2026-09-30", "tur": "uc_aylik"},
                follow_redirects=False)
    pid = int(r.headers["location"].rsplit("/", 1)[1])
    for kind, path in synthetic_luca.generate(data_root / "luca_web", hatali=True).items():
        r = tc.post(f"/mukellef/{cid}/donem/{pid}/yukle", data={"csrf": tok},
                    files={"dosya": (path.name, path.read_bytes())}, follow_redirects=False)
        bpath = r.headers["location"]
        page = tc.get(bpath).text
        assert "biçimi tanındı" in page and "map_" not in page
        r = tc.post(bpath + "/onayla", data={"csrf": tok, "doc_type": kind}, follow_redirects=False)
        assert r.status_code == 303
        assert "Aktarılan kayıt" in tc.get(bpath).text
    r = tc.post(f"/mukellef/{cid}/donem/{pid}/analiz", data={"csrf": tok}, follow_redirects=False)
    page = tc.get(r.headers["location"]).text
    assert "Defteri kebir" in page and "Muavin defter" in page
    assert "Yevmiye–kebir mutabakatı" in page and "Kasa" in page
