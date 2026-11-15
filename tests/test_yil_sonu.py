from denetci.models import Client
from test_web import app_client, csrf, login  # noqa: F401


def test_yil_sonu_sayfasi(app_client):
    from denetci import db
    login(app_client)
    with db.SessionLocal() as s:
        c = Client(unvan="YS LTD", buro_id=1)
        s.add(c)
        s.commit()
        cid = c.id
    r = app_client.get(f"/mukellef/{cid}/yil-sonu")
    assert r.status_code == 200 and "Yıllık amortisman ayrıldı" in r.text and "KONTROL EDİLEMEDİ" in r.text
    tok = csrf(app_client, f"/mukellef/{cid}/yil-sonu")
    r = app_client.post(f"/mukellef/{cid}/yil-sonu/isaretle", data={"csrf": tok, "yil": 2026, "is": "stok"}, follow_redirects=False)
    assert r.status_code == 303
    assert "YAPILDI" in app_client.get(f"/mukellef/{cid}/yil-sonu?yil=2026").text
    assert app_client.post(f"/mukellef/{cid}/yil-sonu/isaretle", data={"csrf": tok, "yil": 2026, "is": "yok"}).status_code == 404


def test_yil_sonu_demo_otomatik(app_client):
    login(app_client)
    tok = csrf(app_client, "/")
    r = app_client.post("/demo", data={"csrf": tok}, follow_redirects=False)
    cid = int(r.headers["location"].split("/mukellef/")[1].split("?")[0].split("/")[0])
    page = app_client.get(f"/mukellef/{cid}/yil-sonu").text
    assert "Yıllık amortisman ayrıldı" in page and ("TAMAM" in page or "BULGU VAR" in page)
