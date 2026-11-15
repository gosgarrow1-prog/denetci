from datetime import date

from denetci import takip
from denetci.models import Client, IsDurumu
from test_web import app_client, csrf, login  # noqa: F401


def test_son_gunler_tatile_kayar():
    # Vergino takvimiyle uyumlu: Ekim 2026 KDV 28 Kasım Cumartesi → 30 Kasım Pazartesi
    assert takip.son_gun("kdv", 2026, 10) == date(2026, 11, 30)
    assert takip.son_gun("muhtasar", 2026, 10) == date(2026, 11, 26)
    # Geçici vergi: izleyen 2. ayın 17'si (beyan ve ödeme aynı gün)
    assert takip.son_gun("gecici", 2026, 9) == date(2026, 11, 17)
    assert takip.son_gun("gecici", 2026, 8) is None


def test_yaklasan_liste():
    liste = takip.yaklasan(date(2026, 11, 14), 45)
    assert (date(2026, 11, 17), "Geçici vergi beyanı") in [(v.tarih, v.ad) for v in liste]
    assert liste == sorted(liste, key=lambda v: (v.tarih, v.ad))


def test_takip_sayfasi_ve_isaretleme(app_client):
    login(app_client)
    from denetci import db
    with db.SessionLocal() as s:
        c = Client(unvan="ORNEK LTD", buro_id=1)
        s.add(c)
        s.commit()
        cid = c.id
    page = app_client.get("/takip").text
    assert "Bu Ay Ne Kaldı?" in page and "ORNEK LTD" in page and "Belge yükle" in page
    tok = csrf(app_client, "/takip")
    # Bugüne en yakın muhtasar dönemini işaretle
    import re
    m = re.search(r'name="yil" value="(\d+)"><input type="hidden" name="ay" value="(\d+)"><input type="hidden" name="is_turu" value="muhtasar"', page)
    yil, ay = int(m.group(1)), int(m.group(2))
    r = app_client.post("/takip/isaretle", data={"csrf": tok, "client_id": cid, "yil": yil, "ay": ay, "is_turu": "muhtasar"},
                        follow_redirects=False)
    assert r.status_code == 303
    assert "Tamamlananlar (1)" in app_client.get("/takip").text
    tok = csrf(app_client, "/takip")
    app_client.post("/takip/isaretle", data={"csrf": tok, "client_id": cid, "yil": yil, "ay": ay, "is_turu": "muhtasar",
                                             "islem": "geri"})
    with db.SessionLocal() as s:
        assert s.query(IsDurumu).count() == 0


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
