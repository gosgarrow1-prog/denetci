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


def test_kredibilite_notu():
    fin = _fin()
    fin.kalemler.update({"donen": 600_000_00, "kvyk": 300_000_00, "yabanci_kaynak": 600_000_00, "dagitilmamis_kar": 50_000_00,
                         "mali_borclar": 200_000_00, "hazir_degerler": 50_000_00, "amortisman": 10_000_00,
                         "finansman_gideri": 20_000_00})
    fin.donem_sonucu_kapanmamis = 100_000_00
    kr = raporlar.kredibilite(fin)
    assert kr.harf in "ABCDE" and kr.en_fazla == 2 * len(kr.gostergeler)
    g = {x.ad: x for x in kr.gostergeler}
    assert g["Cari oran"].puan == 2 and g["Özkaynak / aktif"].puan == 2  # 2,0 ve %40
    z = kr.altman
    assert abs(z["z"] - (6.56 * z["x1"] + 3.26 * z["x2"] + 6.72 * z["x3"] + 1.05 * z["x4"])) < 1e-9
    assert raporlar.kredibilite(None).eksik


def test_nakit_akim_dengesi():
    from types import SimpleNamespace
    from denetci import nakit
    from denetci.checks.rules import Context

    def J(d, code, borc=0, alacak=0, aciklama=None):
        return SimpleNamespace(tarih=d, hesap_kodu=code, borc=borc, alacak=alacak, hesap_adi=None, raw_row_id=1,
                               fis_no="1", madde_no="1", aciklama=aciklama, fis_tipi=None, evrak_no=None)
    a, b = date(2026, 1, 1), date(2026, 2, 10)
    ytd = [J(a, "102.01", borc=1_000_00, aciklama="Açılış fişi"), J(a, "500.01", alacak=1_000_00, aciklama="Açılış fişi"),
           J(b, "120.01", borc=600_00), J(b, "600.01", alacak=600_00),           # veresiye satış
           J(b, "102.01", borc=400_00), J(b, "120.01", alacak=400_00),           # tahsilat
           J(b, "255.01", borc=300_00), J(b, "102.01", alacak=300_00),           # demirbaş alımı
           J(b, "300.01", alacak=200_00), J(b, "102.01", borc=200_00)]           # kredi
    ctx = Context("THP_VUK", a, date(2026, 3, 31), None, None)
    ctx.ytd = ytd
    na = nakit.hesapla(ctx)
    assert na.nakit_bas == 1_000_00 and na.nakit_son == 1_300_00
    assert na.toplam == {"faaliyet": 400_00, "yatirim": -300_00, "finansman": 200_00} and na.fark == 0
