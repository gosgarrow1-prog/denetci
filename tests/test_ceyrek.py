from datetime import date

from denetci import ceyrek, db
from denetci.models import Client, ImportBatch, JournalLine, Period, RawRow, SourceFile


def _yevmiye(s, c, p, satirlar):
    sf = SourceFile(client_id=c.id, sha256="x" * 64, orig_name="y.xlsx", size=1, stored_path="/yok")
    s.add(sf); s.flush()
    b = ImportBatch(client_id=c.id, period_id=p.id, source_file_id=sf.id, doc_type="yevmiye", status="onaylandi",
                    summary={"tarih_araligi": [p.baslangic.isoformat(), p.bitis.isoformat()]})
    s.add(b); s.flush()
    for i, (d, kod, ad, borc, alacak) in enumerate(satirlar):
        r = RawRow(client_id=c.id, batch_id=b.id, sheet="S", row_no=i + 1, cells=[], row_class="kayit")
        s.add(r); s.flush()
        s.add(JournalLine(client_id=c.id, batch_id=b.id, raw_row_id=r.id, fis_no=str(i), tarih=d, hesap_kodu=kod,
                          hesap_adi=ad, borc=borc, alacak=alacak, fis_tipi="Mahsup"))
    s.flush()


def test_ceyrek_analizi(session):
    c = Client(unvan="DEMO Ç"); session.add(c); session.flush()
    p = Period(client_id=c.id, baslangic=date(2026, 1, 1), bitis=date(2026, 9, 30), tur="ara")
    session.add(p); session.flush()
    rows = []
    for m in range(4, 10):
        rows += [(date(2026, m, 10), "600.01", "SATIŞ", 0, 1000_00)]
        if m < 9:
            rows.append((date(2026, m, 28), "621.01", "SMM", 700_00, 0))
        if m != 8:
            rows.append((date(2026, m, 5), "770.01.001", "KİRA GİDERİ", 100_00, 0))
    _yevmiye(session, c, p, rows)
    a = ceyrek.analiz(session, c, date(2026, 9, 30))
    assert a.ad == "Temmuz–Eylül 2026" and [k.var for k in a.aylar] == [True, True, True]
    assert a.toplam.kal["net_satis"] == 3000_00 and a.onceki.kal["net_satis"] == 3000_00
    metin = " ".join(u.metin for u in a.uyarilar)
    assert "Eylül ayında 1.000,00 TL satış var" in metin
    assert "770.01.001 KİRA GİDERİ: Ağustos ayında kayıt yok" in metin
    assert not a.gecen_yil.var
    # Ocak–Mart yüklenmemiş: veri yok
    assert ceyrek.analiz(session, c, date(2025, 3, 31)) is None
