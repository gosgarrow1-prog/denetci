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
    for text in ("Gösterge Paneli", "Dönem özeti", "Dikkat Gerektirenler", "KDV hesap özeti", "Oranlara göre matrah ve KDV", "Analizler ve Gereken Belgeler", "KDV Ön Kontrol",
                 "Kontroller", "Net satışlar", "ÖNEMLİ"):
        assert text in page, text
    assert "Fiş denkliği" in page or "denk değil" in page
    fin = tc.get(loc + "/finansal").text
    for text in ("Gelir tablosu özeti", "Bilanço özeti", "Oranlar", "<svg"):
        assert text in fin, text
    risk = tc.get(loc + "/risk").text
    for text in ("Hata ve Risk Taraması", "Ters bakiye veren hesaplar", "Kasadan yüksek tutarlı tahsilat ve ödeme",
                 "Tatil günü gider kayıtları", "Mükerrer fatura kaydı"):
        assert text in risk, text
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
        got = sorted((p.baslangic.isoformat(), p.bitis.isoformat(), p.tur) for p in periods)
        # Çeyrek yevmiyesi aylara da ayrılır.
        assert got == [("2026-07-01", "2026-07-31", "aylik"), ("2026-07-01", "2026-09-30", "uc_aylik"),
                       ("2026-08-01", "2026-08-31", "aylik"), ("2026-09-01", "2026-09-30", "aylik")]
        assert len(s.scalars(select(AnalysisRun).where(AnalysisRun.client_id == cid)).all()) == 4
        aug = next(p for p in periods if p.baslangic.month == 8)
        from denetci.checks.engine import build_context
        c = s.get(Client, cid)
        ctx, _ = build_context(s, c, aug)
        assert ctx.turetilmis and ctx.yevmiye and all(j.tarih.month == 8 for j in ctx.yevmiye)
        pids = [p.id for p in periods]
    # Ayrılan alt dönemler dahil her dönemde tüm sayfalar açılmalı (dönem raporu hatası 02.11.2026).
    for pid in pids:
        for x in ("", "/belgeler", "/kdv", "/mutabakat", "/finansal", "/ceyrek", "/gecici-vergi", "/fatura-mutabakati", "/rapor"):
            assert tc.get(f"/mukellef/{cid}{x}?donem={pid}").status_code == 200, (x, pid)
        hepsi = "&".join(f"konu={k}" for k in ("genel", "finansal", "kdv", "gecici", "ceyrek", "risk", "mutabakat", "fatura"))
        assert tc.get(f"/mukellef/{cid}/rapor?donem={pid}&{hepsi}").status_code == 200, pid
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
    assert "devreden KDV" in page and "eksik" in page
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
    assert "3. dönem (Ocak–Eylül)" in page and "17.11.2026" in page and "Tahmini geçici vergi matrahı" in page
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
    assert '<optgroup label="Aylık · 2026">' in page and '<optgroup label="3 aylık (çeyrek) · 2026">' in page
    assert '<optgroup label="Geçici vergi, yılbaşından · 2026">' in page and "Ocak–Eylül 2026 (3. geçici vergi dönemi)" in page
    assert "3. çeyrek: Temmuz–Eylül 2026 ●" in page  # DEMO dosyaları bu dönemde
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


def test_report_and_finance_show_recommendations(app_client):
    tc = app_client
    login(tc)
    loc = tc.post("/demo", data={"csrf": csrf(tc, "/")}, follow_redirects=False).headers["location"]
    cid = int(loc.rsplit("/", 1)[1].split("?")[0])
    rep = tc.get(f"/mukellef/{cid}/rapor").text
    assert "3. Değerlendirme ve öneriler" in rep and "Dayanak:" in rep
    assert "Değerlendirme ve Öneriler" in tc.get(f"/mukellef/{cid}/finansal").text


def test_recommendation_rules():
    from types import SimpleNamespace
    from denetci.finance import Ratio
    from denetci.oneriler import build
    fin = SimpleNamespace(has_balance=True, has_income=True, aktif=1000_00, flows_window=None, gelir_tablosu=[],
                          oranlar=[Ratio("cari", "", 0.8, ""), Ratio("brut_marj", "", 0.2, "")],
                          kalemler={"ozkaynak": -5_00, "kasa": 200_00, "ortaklardan_alacak": 50_00, "net_satis": 100_00,
                                    "donem_kari": -1_00, "kvyk": 10, "devreden_kdv": 0})
    prev = SimpleNamespace(oranlar=[Ratio("brut_marj", "", 0.3, "")])
    basliklar = [o.baslik for o in build(fin, prev)]
    for b in ("Kısa vadeli borçlar dönen varlıklardan fazla", "Özkaynak eksiye düşmüş", "Kasa bakiyesi yüksek",
              "Ortaklardan alacak bakiyesi var", "Dönem zararla sonuçlanıyor", "Brüt kâr marjı önceki döneme göre düştü"):
        assert b in basliklar, b
    assert build(None) == []


def test_gecici_vergi_uses_cumulative_period(app_client):
    tc = app_client
    login(tc)
    tok = csrf(tc, "/")
    tc.post("/demo", data={"csrf": tok})
    with db.SessionLocal() as s:
        q3 = s.scalars(select(Period)).first()
        cid, qid = q3.client_id, q3.id
    r = tc.get(f"/mukellef/{cid}/gecici-vergi?donem={qid}", follow_redirects=False)
    assert r.status_code == 303
    gid = int(r.headers["location"].split("donem=")[1])
    with db.SessionLocal() as s:
        g = s.get(Period, gid)
        assert (g.baslangic.isoformat(), g.bitis.isoformat()) == ("2026-01-01", "2026-09-30")
    page = tc.get(r.headers["location"]).text
    assert "Temmuz–Eylül 2026 dosyalarından" in page and "Tahmini geçici vergi matrahı" in page and "K-27" in page
    tc.post(f"/mukellef/{cid}/donem/{gid}/gecici-vergi",
            data={"csrf": tok, "kkeg": "1.234,00", "istisna": "", "gecmis_zarar": "", "indirim": "", "onceki_odenen": "",
                  "tur": "kurum", "oran": "", "maliyet": "yok"})
    # Çeyrek döneminin raporu kümülatif dönemin girdilerini kullanır.
    assert "Kanunen kabul edilmeyen giderler (KKEG)" in tc.get(f"/mukellef/{cid}/rapor?donem={qid}").text


def test_assistant_guides_to_documents(app_client):
    tc = app_client
    login(tc)
    loc = tc.post("/demo", data={"csrf": csrf(tc, "/")}, follow_redirects=False).headers["location"]
    cid = int(loc.rsplit("/", 1)[1].split("?")[0])
    page = tc.get(f"/mukellef/{cid}").text
    assert "Denetçi Asistan" in page and "ne yapmak istiyorsunuz" in page and "asistan=gecici" in page
    page = tc.get(f"/mukellef/{cid}?asistan=gecici").text
    assert "Hangi geçici vergi dönemi?" in page and "2026 / 3. dönem (Ocak–Eylül)" in page
    r = tc.get(f"/mukellef/{cid}/asistan?gorev=gecici&bas=2026-01-01&bit=2026-09-30", follow_redirects=False)
    assert "asistan=gecici" in r.headers["location"]
    page = tc.get(r.headers["location"]).text
    assert "Nasıl alınır?" in page and "01.01.2026 – 30.09.2026" in page and "Analizi aç" in page
    # Aylık KDV: Eylül, çeyrek yevmiyesinden ayrılır ve analiz edilir.
    r = tc.get(f"/mukellef/{cid}/asistan?gorev=kdv&bas=2026-09-01&bit=2026-09-30", follow_redirects=False)
    page = tc.get(r.headers["location"]).text
    assert "yevmiyesinden" in page and "KDV Ön Kontrol" in page and "kontrol edildi" in page


def test_back_button(app_client):
    tc = app_client
    login(tc)
    assert "back-btn" not in tc.get("/").text  # ana sayfada gerek yok
    loc = tc.post("/demo", data={"csrf": csrf(tc, "/")}, follow_redirects=False).headers["location"]
    cid = re.search(r"/mukellef/(\d+)", loc).group(1)
    kdv = tc.get(f"/mukellef/{cid}/kdv").text
    assert 'class="back-btn" href="/mukellef/' + cid + '"' in kdv and "history.back()" in kdv
    assert 'class="back-btn" href="/"' in tc.get(f"/mukellef/{cid}").text


def test_client_list_alphabetical(app_client):
    from denetci.web.app import tr_sira
    names = ["Zeytin Ltd", "Çelik AŞ", "ışık Tic", "Ilgaz", "İnci", "Cam", "Şeker", "Su", "Ölçü", "Ok", "aydın"]
    assert sorted(names, key=tr_sira) == ["aydın", "Cam", "Çelik AŞ", "Ilgaz", "ışık Tic", "İnci", "Ok", "Ölçü", "Su",
                                          "Şeker", "Zeytin Ltd"]
    tc = app_client
    login(tc)
    for n in ("Şeker Gıda", "Ahmet Ticaret", "Çınar İnşaat", "Bora Ltd"):
        tc.post("/mukellef/yeni", data={"csrf": csrf(tc, "/"), "unvan": n})
    page = tc.get("/").text
    pos = [page.index(n) for n in ("Ahmet Ticaret", "Bora Ltd", "Çınar İnşaat", "Şeker Gıda")]
    assert pos == sorted(pos)


def test_kdv_page_rate_table(app_client):
    tc = app_client
    login(tc)
    loc = tc.post("/demo", data={"csrf": csrf(tc, "/")}, follow_redirects=False).headers["location"]
    page = tc.get(loc + "/kdv").text
    for text in ("KDV hesap özeti", "Ödenecek KDV", "Oranlara göre matrah ve KDV", "Hesaplanan KDV (satışlar)",
                 "İndirilecek KDV (alışlar)", "Uyumlu", "305.000,00", "61.000,00", "K-25", "K-26"):
        assert text in page, text


def test_gecici_steps(app_client):
    tc = app_client
    login(tc)
    loc = tc.post("/demo", data={"csrf": csrf(tc, "/")}, follow_redirects=False).headers["location"]
    r = tc.get(loc + "/gecici-vergi")
    page = r.text
    for text in ("Hazırlık aşamaları", "Yılbaşından mizan veya yevmiye", "Değerleme ve tahakkuklar", "Sıradaki adım",
                 "Yapıldı olarak işaretle"):
        assert text in page, text
    pid = re.search(r'/donem/(\d+)/gv-adim', page).group(1)
    cid = re.search(r"/mukellef/(\d+)", loc).group(1)
    tc.post(f"/mukellef/{cid}/donem/{pid}/gv-adim", data={"csrf": csrf(tc, "/"), "adim": "degerleme", "deger": "1"})
    assert "İşareti kaldır" in tc.get(f"/mukellef/{cid}/gecici-vergi?donem={pid}").text


def test_eksik_aylar_adlariyla():
    from datetime import date
    from types import SimpleNamespace
    from denetci import analyses
    p = SimpleNamespace(baslangic=date(2026, 7, 1), bitis=date(2026, 9, 30), devreden_kdv=None, gv={})
    d = SimpleNamespace(tur="kdv", yil=2026, ay=8)
    k = analyses.build(p, {}, None, None, False, [d])[0]
    n = {x.label: x for x in k.needs}
    b = n["KDV beyannameleri (Temmuz–Eylül 2026)"]
    assert not b.ok and "Eksik: Temmuz ve Eylül 2026" in b.hint
    assert "Haziran 2026 devreden KDV'si" in n


def test_yuklenen_dosya_arsive_kopyalanir(app_client, data_root, monkeypatch, tmp_path):
    monkeypatch.setenv("DENETCI_ARSIV", str(tmp_path / "arsiv"))
    tc = app_client
    login(tc)
    tok = csrf(tc, "/")
    r = tc.post("/mukellef/yeni", data={"csrf": tok, "unvan": "DEMO ARŞİV A.Ş."}, follow_redirects=False)
    cid = int(r.headers["location"].rsplit("/", 1)[1])
    files = synthetic_luca.generate(data_root / "ar", mizan=True)
    tc.post(f"/mukellef/{cid}/yukle", data={"csrf": tok},
            files=[("dosya", (p.name, p.read_bytes())) for p in files.values()], follow_redirects=False)
    kopyalar = list((tmp_path / "arsiv" / "DEMO ARŞİV A.Ş").rglob("*.xlsx"))
    assert len(kopyalar) == len(files)
    assert any(k.name.startswith("Yevmiye - ") for k in kopyalar)
    assert "arsiv" in tc.get(f"/mukellef/{cid}/belgeler").text
    z = tc.get(f"/mukellef/{cid}/arsiv.zip")
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    import io, zipfile
    adlar = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert len(adlar) == len(files) and all(a.startswith("2026/") for a in adlar)
    r = tc.post("/mukellef/yeni", data={"csrf": tok, "unvan": "DEMO BOŞ A.Ş."}, follow_redirects=False)
    assert tc.get(r.headers["location"] + "/arsiv.zip").status_code == 404


def test_sunucu_kipinde_telefon_kodu_ve_yedek(data_root, monkeypatch):
    import os, zipfile
    from datetime import datetime, timedelta
    from denetci import config, yedek
    assert not config.telefon_kodu()
    monkeypatch.setenv("DENETCI_SUNUCU", "1")
    assert config.telefon_kodu() and config.sunucu()
    (config.data_dir() / "dosyalar").mkdir()
    (config.data_dir() / "dosyalar" / "a.xlsx").write_bytes(b"x")
    eski = config.yedek_dir() / "denetci-2026-01-01-0300.zip"
    eski.write_bytes(b"")
    t = (datetime.now() - timedelta(days=20)).timestamp()
    os.utime(eski, (t, t))
    yol = yedek.al()
    adlar = zipfile.ZipFile(yol).namelist()
    assert "veri/denetci.db" in adlar and "veri/dosyalar/a.xlsx" in adlar
    assert not eski.exists() and yol.exists()


def test_buro_logosu_ve_whatsapp_ozeti(app_client, monkeypatch, tmp_path):
    import json
    tc = app_client
    login(tc)
    r = tc.post("/demo", data={"csrf": csrf(tc, "/")}, follow_redirects=False)
    loc = r.headers["location"].split("?")[0]
    # Büro klasörü yokken Denetçi adıyla çalışır.
    monkeypatch.setenv("DENETCI_BURO", str(tmp_path / "yok"))
    assert tc.get("/buro/logo-acik.png").status_code == 404
    rapor = tc.get(loc + "/rapor").text
    assert "Mükellefe gönderilecek kısa özet" in rapor and "konuya bakılması gerekiyor" in rapor
    assert 'src="/buro/logo-acik.png"' not in rapor
    # Büro bilgileri ve logo eklenince raporda ve menüde görünür.
    d = tmp_path / "buro"
    d.mkdir()
    (d / "buro.json").write_text(json.dumps({"unvan": "Örnek Müşavirlik", "telefonlar": ["0212 000 0000"],
                                                         "kullanicilar": {"x": {"ad": "Başka Kişi"}}}), encoding="utf-8")
    (d / "logo-acik.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (d / "logo-koyu.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setenv("DENETCI_BURO", str(d))
    rapor = tc.get(loc + "/rapor").text
    assert 'src="/buro/logo-acik.png"' in rapor and "Örnek Müşavirlik" in rapor and "Kaşe / İmza" in rapor
    assert "Örnek Müşavirlik\n0212 000 0000" in rapor
    # Tanımlı olmayan kullanıcıda kullanıcı adı yazılır; tanımlıysa ad ve unvan.
    import re as _re
    hazir = _re.search(r'r-hazirlayan">(.*?)</div>\s*</div>', rapor, _re.S).group(1)
    assert "Örnek Müşavirlik" in hazir and "<strong>burak</strong>" in hazir and "Başka Kişi" not in hazir
    from denetci import buro
    assert buro.hazirlayan("x") == {"ad": "Başka Kişi", "unvan": ""}
    assert buro.hazirlayan("yok") == {"ad": "yok", "unvan": ""}
    assert 'src="/buro/logo-koyu.png"' in tc.get(loc).text
    assert tc.get("/buro/logo-koyu.png").status_code == 200
    assert tc.get("/buro/logo-baska.png").status_code == 404


def test_misafir_yalnizca_demo_gorur(app_client, monkeypatch):
    from denetci.auth import hash_password, new_totp_secret
    from denetci.models import User
    from tests.test_web import csrf as _csrf
    monkeypatch.setenv("DENETCI_SUNUCU", "1")  # telefon kodu açık; misafire sorulmaz
    tc = app_client
    with db.SessionLocal() as s:
        gercek = Client(unvan="GERÇEK MÜKELLEF A.Ş.")
        s.add(gercek)
        s.add(User(username="misafir", password_hash=hash_password("misafir-parola-123"), totp_secret=new_totp_secret(), misafir=True))
        s.commit()
        gid = gercek.id
    r = tc.post("/giris", data={"csrf": _csrf(tc, "/giris"), "kullanici": "misafir", "parola": "misafir-parola-123"},
                follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert "GERÇEK MÜKELLEF" not in tc.get("/").text
    assert tc.get(f"/mukellef/{gid}").status_code == 404
    assert tc.post("/mukellef/yeni", data={"csrf": _csrf(tc, "/"), "unvan": "X"}).status_code == 403
    loc = tc.post("/demo", data={"csrf": _csrf(tc, "/")}, follow_redirects=False).headers["location"]
    page = tc.get(loc).text
    assert "Gösterge Paneli" in page and "GERÇEK MÜKELLEF" not in page


def test_tasinan_veride_dosya_yollari_duzelir(data_root, capsys):
    from denetci import config
    from denetci.__main__ import main
    from denetci.models import SourceFile
    hedef = config.data_dir() / "dosyalar" / "3" / "abc.xlsx"
    hedef.parent.mkdir(parents=True)
    hedef.write_bytes(b"x")
    with db.SessionLocal() as s:
        c = Client(unvan="DEMO Y", is_demo=True)
        s.add(c)
        s.flush()
        s.add(SourceFile(client_id=c.id, sha256="a" * 64, orig_name="a.xlsx", size=1,
                         stored_path="C:\\Users\\Burak\\DenetciVeri\\dosyalar\\3\\abc.xlsx"))
        s.commit()
    main(["yollari-duzelt"])
    assert "Bulunamayan dosya: 0" in capsys.readouterr().out
    with db.SessionLocal() as s:
        assert s.scalar(select(SourceFile)).stored_path == str(hedef)


def test_bu_bilgisayari_hatirla(app_client, monkeypatch):
    from denetci.auth import totp
    from denetci.models import User
    from tests.test_web import csrf as _csrf
    monkeypatch.setenv("DENETCI_SUNUCU", "1")
    tc = app_client
    giris = lambda **ek: tc.post("/giris", data={"csrf": _csrf(tc, "/giris"), "kullanici": "burak",
                                                 "parola": "cok-gizli-parola-123", **ek}, follow_redirects=False)
    assert giris().status_code == 200  # kod yok, hatırlanmıyor: giriş yok
    r = giris(kod=totp(tc.secret), hatirla="1")
    assert r.status_code == 303 and "denetci_cihaz" in r.headers.get("set-cookie", "")
    tc.cookies.set("denetci_cihaz", r.cookies.get("denetci_cihaz") or tc.cookies.get("denetci_cihaz"))
    tc.post("/cikis", data={"csrf": _csrf(tc, "/")})
    assert giris().status_code == 303  # hatırlanan bilgisayar: kodsuz giriş
    tc.post("/cikis", data={"csrf": _csrf(tc, "/")})
    with db.SessionLocal() as s:  # anahtar yenilenince hatırlama geçersiz
        u = s.scalar(select(User).where(User.username == "burak"))
        u.totp_secret = "JBSWY3DPEHPK3PXP"
        s.commit()
    assert giris().status_code == 200
