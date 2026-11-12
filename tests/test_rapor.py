"""Konu başlıklarına göre raporlar."""
from datetime import date

from denetci import raporlar
from denetci.finance import Line, Summary
from denetci.web.charts import aylik_grafik, gosterge, selale, yatay_cubuk
from tests.test_web import app_client, login  # noqa: F401


def test_secilen_konular():
    assert raporlar.secilen([]) == ["genel"]
    assert raporlar.secilen(["xx", "kdv", "genel"]) == ["genel", "kdv"]
    assert raporlar.baslik(["genel"]) == "Dönem Kontrol Raporu"
    assert raporlar.baslik(["finansal"]) == "Finansal analiz Raporu"
    assert raporlar.baslik(["genel", "kdv"]) == "Dönem Raporu"


def _fin():
    s = Summary()
    s.aktif, s.pasif = 1_000_000_00, 1_000_000_00
    s.flows_window = (date(2026, 7, 1), date(2026, 9, 30))
    s.gelir_tablosu = [Line("Net satışlar", 900_000_00, "ara_toplam"), Line("Satışların maliyeti", -600_000_00),
                       Line("Faaliyet giderleri", -200_000_00), Line("Dönem kârı (vergi öncesi)", 100_000_00, "toplam")]
    s.kalemler = {"net_satis": 900_000_00, "brut_kar": 300_000_00, "faaliyet_kar": 100_000_00, "donem_kari": 100_000_00,
                  "ticari_alacaklar": 300_000_00, "stoklar": 200_000_00, "ticari_borclar": 150_000_00, "ozkaynak": 400_000_00}
    return s


def test_finansal_gostergeler_ve_dupont():
    f = raporlar.finansal(_fin())
    g = {x.ad: x for x in f.gostergeler}
    assert f.gun == 92
    assert round(g["Alacak tahsil süresi"].deger) == round(300 / 900 * 92)
    assert round(g["Stok devir süresi"].deger) == round(200 / 600 * 92)
    assert g["Alacak tahsil süresi"].metin.endswith("gün")
    d = f.dupont
    assert abs(d["marj"] * d["devir"] * d["carpan"] - d["roe"]) < 1e-9


def test_grafikler_svg():
    fin = _fin()
    assert "<svg" in selale(fin.gelir_tablosu) and "Kârı azaltan" in selale(fin.gelir_tablosu)
    assert "<svg" in yatay_cubuk([("770 Genel yönetim giderleri", 100_00)])
    assert "<svg" in gosterge(1.2, (1.5, 2.0), 4) and str(gosterge(None, None, 4)) == ""
    assert str(aylik_grafik([])) == ""


def test_rapor_sayfasi_konular(app_client):  # noqa: F811
    tc = app_client
    login(tc)
    cid = tc.post("/demo", data={"csrf": __import__("tests.test_web", fromlist=["csrf"]).csrf(tc, "/")},
                  follow_redirects=False).headers["location"].rsplit("/", 1)[1]
    r = tc.get(f"/mukellef/{cid}/rapor").text
    assert "Rapora girecek konular" in r and "Konulara göre durum" in r
    r = tc.get(f"/mukellef/{cid}/rapor?konu=finansal").text
    assert "Finansal analiz Raporu" in r and "Oranlar ve ne anlama geldikleri" in r and "<svg" in r
    assert "Mükellefe gönderilecek kısa özet" not in r
    hepsi = "&".join(f"konu={k.key}" for k in raporlar.KONULAR)
    r = tc.get(f"/mukellef/{cid}/rapor?{hepsi}").text
    assert "İçindekiler" in r and r.count('class="r-konu"') == len(raporlar.KONULAR)
