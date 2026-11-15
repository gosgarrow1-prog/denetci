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
    with __import__("denetci.db", fromlist=["x"]).SessionLocal() as s:
        c = Client(unvan="ORNEK LTD", buro_id=1)
        s.add(c)
        s.commit()
        cid = c.id
    r = app_client.get("/takip?ay=2026-10")
    assert r.status_code == 200 and "ORNEK LTD" in r.text and "Bekliyor" in r.text
    tok = csrf(app_client, "/takip?ay=2026-10")
    r = app_client.post("/takip/isaretle", data={"csrf": tok, "client_id": cid, "yil": 2026, "ay": 10, "is_turu": "muhtasar"},
                        follow_redirects=False)
    assert r.status_code == 303
    assert "Verildi" in app_client.get("/takip?ay=2026-10").text
    tok = csrf(app_client, "/takip?ay=2026-10")
    app_client.post("/takip/isaretle", data={"csrf": tok, "client_id": cid, "yil": 2026, "ay": 10, "is_turu": "muhtasar",
                                             "islem": "geri"})
    from denetci import db
    with db.SessionLocal() as s:
        assert s.query(IsDurumu).count() == 0
    assert app_client.get("/takip?ay=bozuk").status_code == 200
