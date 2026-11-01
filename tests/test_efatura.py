"""e-Fatura listesi okuma ve defterle eşleştirme. Yalnızca sentetik veri."""

import io
import re
from datetime import date, datetime

from openpyxl import Workbook
from sqlalchemy import select

from denetci import db, synthetic, synthetic_luca as sl
from denetci.models import InvoiceList
from tests.test_web import app_client, csrf, login  # noqa: F401

BASLIK = ["Fatura\nTarihi", "Fatura \nNumarası", "Fatura\nTürü", "VKN\nTCKN ", "Açıklama", "Tevkifat\nOranı", "Özel\nMatrah",
          "%1'lik\nKDV", "%8'lik\nKDV", "%10'lik\nKDV", "%18'lik\nKDV", "%20'lik\nKDV", "%1'lik\nMatrah", "%8'lik\nMatrah",
          "%10'lik\nMatrah", "%18'lik\nMatrah", "%20'lik\nMatrah", "Toplam"]


def _liste(rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "FATURALAR"
    ws.append(BASLIK)
    for t, no, kdv20, mat20 in rows:
        ws.append([datetime(t.year, t.month, t.day), no, "SATIS", 1234567890.0, "ÖRNEK MÜŞTERİ A.Ş.", " ", None,
                   None, None, None, None, kdv20, None, None, None, None, mat20, kdv20 + mat20])
    ws.append([])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _yevmiye(tmp_path):
    fis = [("A1", date(2026, 8, 5), "EF ABC2026000000001 05/08 ÖRNEK MÜŞTERİ",
            [("120.01", 1200, 0), ("600.01", 0, 1000), ("391.01", 0, 200)]),
           ("A2", date(2026, 8, 9), "EF ABC2026000000002 09/08 ÖRNEK MÜŞTERİ",
            [("120.01", 1100, 0), ("600.01", 0, 1000), ("391.01", 0, 100)]),  # KDV eksik kaydedilmiş
           ("A3", date(2026, 8, 20), "PERAKENDE SATIŞ", [("100.01", 600, 0), ("600.01", 0, 500), ("391.01", 0, 100)])]
    path = tmp_path / "yevmiye.xlsx"
    sl.write_yevmiye(path, sl._numbered(fis), date(2026, 8, 1), date(2026, 8, 31))
    return path.read_bytes()


def test_invoice_list_matching(app_client, tmp_path):
    tc = app_client
    login(tc)
    tok = csrf(tc, "/")
    r = tc.post("/mukellef/yeni", data={"csrf": tok, "unvan": synthetic.ACCOUNTS and "SENTETİK ÖRNEK TİCARET A.Ş. — DEMO"},
                follow_redirects=False)
    cid = int(r.headers["location"].rsplit("/", 1)[1])
    liste = _liste([(date(2026, 8, 5), "ABC2026000000001", 200.0, 1000.0),
                    (date(2026, 8, 9), "ABC2026000000002", 200.0, 1000.0),
                    (date(2026, 8, 25), "ABC2026000000003", 50.0, 250.0)])
    r = tc.post(f"/mukellef/{cid}/yukle", data={"csrf": tok},
                files=[("dosya", ("yevmiye.xlsx", _yevmiye(tmp_path))), ("dosya", ("faturalar.xlsx", liste))],
                follow_redirects=True)
    assert "Fatura listesi okundu: 3 fatura" in r.text and "giden (satış)" in r.text
    with db.SessionLocal() as s:
        lst = s.scalar(select(InvoiceList).where(InvoiceList.client_id == cid))
        assert lst.yon == "giden" and lst.yon_kaynak == "defter"
    pid = re.search(r"donem=(\d+)", r.url.query.decode() if isinstance(r.url.query, bytes) else str(r.url)).group(1)
    page = tc.get(f"/mukellef/{cid}/kdv?donem={pid}").text
    for text in ("e-Fatura eşleştirmesi · Giden (satış)", "Eşleşti", "Tutar farklı", "Defterde yok", "K-30",
                 "Defterde olup listede olmayan KDV'li fişler", "PERAKENDE SATIŞ"):
        assert text in page, text
    assert "Giden (satış): 3 faturadan 1 eşleşti" in page
    # Yön elle değiştirilebilir ve liste geri alınabilir.
    tc.post(f"/mukellef/{cid}/fatura/{lst.id}", data={"csrf": tok, "islem": "geri-al"})
    with db.SessionLocal() as s:
        assert s.get(InvoiceList, lst.id).durum == "iptal"
