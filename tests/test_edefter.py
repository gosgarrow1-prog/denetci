"""GİB e-Defter (XBRL-GL) okuyucusu: SENTETİK XML ile. Gerçek mükellef verisi kullanılmaz."""

import io
import zipfile

from sqlalchemy import select

from denetci.importer.ingest import ingest, peek
from denetci.models import JournalLine, Period

NS = ('xmlns:edefter="http://www.edefter.gov.tr" xmlns:xbrli="http://www.xbrl.org/2003/instance" '
      'xmlns:gl-cor="http://www.xbrl.org/int/gl/cor/2006-10-25" xmlns:gl-bus="http://www.xbrl.org/int/gl/bus/2006-10-25"')


def _satir(ana, ana_ad, alt, alt_ad, tutar, dc, tarih, aciklama, belge=""):
    sub = (f"<gl-cor:accountSub><gl-cor:accountSubDescription>{alt_ad}</gl-cor:accountSubDescription>"
           f"<gl-cor:accountSubID>{alt}</gl-cor:accountSubID></gl-cor:accountSub>") if alt else ""
    return (f"<gl-cor:entryDetail><gl-cor:lineNumber>1</gl-cor:lineNumber><gl-cor:account>"
            f"<gl-cor:accountMainDescription>{ana_ad}</gl-cor:accountMainDescription><gl-cor:accountMainID>{ana}</gl-cor:accountMainID>"
            f"{sub}</gl-cor:account><gl-cor:amount decimals=\"INF\" unitRef=\"try\">{tutar}</gl-cor:amount>"
            f"<gl-cor:debitCreditCode>{dc}</gl-cor:debitCreditCode><gl-cor:postingDate>{tarih}</gl-cor:postingDate>"
            f"<gl-cor:documentNumber>{belge}</gl-cor:documentNumber><gl-cor:detailComment>{aciklama}</gl-cor:detailComment>"
            f"</gl-cor:entryDetail>")


def _madde(no, tarih, aciklama, satirlar):
    return (f"<gl-cor:entryHeader><gl-cor:enteredBy>X</gl-cor:enteredBy><gl-cor:enteredDate>{tarih}</gl-cor:enteredDate>"
            f"<gl-cor:entryNumber>{no}</gl-cor:entryNumber><gl-cor:entryComment>{aciklama}</gl-cor:entryComment>"
            f"<gl-cor:entryNumberCounter>{no}</gl-cor:entryNumberCounter>{''.join(satirlar)}</gl-cor:entryHeader>")


def defter(tur="journal", vkn="1234567890", unvan="DEMO A", bozuk=False) -> bytes:
    maddeler = [
        _madde(1, "2026-07-01", "Açılış kaydı", [
            _satir("100", "KASA", "100.01", "Merkez kasa", "5000.00", "D", "2026-07-01", "Açılış kaydı"),
            _satir("500", "SERMAYE", "500.01", "Sermaye", "5000.00", "C", "2026-07-01", "Açılış kaydı")]),
        _madde(2, "2026-07-15", "Satış faturası", [
            _satir("120", "ALICILAR", "120.01", "Müşteri", "1200.00", "D", "2026-07-15", "ABC2026000000001 satış", "ABC2026000000001"),
            _satir("600", "YURTİÇİ SATIŞLAR", "600.01", "Yurtiçi", "1000.00", "C", "2026-07-15", "Satış"),
            _satir("391", "HESAPLANAN KDV", "391.01", "%20", "200.00" if not bozuk else "199.00", "C", "2026-07-15", "KDV")]),
    ]
    return (f'<?xml version="1.0" encoding="UTF-8"?><edefter:defter {NS}><xbrli:xbrl><gl-cor:accountingEntries>'
            f"<gl-cor:documentInfo><gl-cor:entriesType>{tur}</gl-cor:entriesType>"
            f"<gl-cor:periodCoveredStart>2026-07-01</gl-cor:periodCoveredStart><gl-cor:periodCoveredEnd>2026-07-31</gl-cor:periodCoveredEnd>"
            f"</gl-cor:documentInfo><gl-cor:entityInformation><gl-bus:organizationIdentifiers>"
            f"<gl-bus:organizationIdentifier>{vkn}</gl-bus:organizationIdentifier>"
            f"<gl-bus:organizationDescription>{unvan}</gl-bus:organizationDescription></gl-bus:organizationIdentifiers>"
            f"</gl-cor:entityInformation>{''.join(maddeler)}</gl-cor:accountingEntries></xbrli:xbrl></edefter:defter>").encode()


def test_edefter_xml_yevmiye(session, client_period):
    c, _ = client_period
    xml = defter()
    info = peek("defter-Y-202607.xml", xml)
    assert info == {"tarih_araligi": ["2026-07-01", "2026-07-31"], "firma": "DEMO A", "vkn": "1234567890"}
    o = ingest(session, c, "defter-Y-202607.xml", xml, None)
    assert o.status == "aktarildi", o.message
    assert o.batch.doc_type == "yevmiye" and o.batch.profile == "edefter_yevmiye"
    assert o.period.baslangic.isoformat() == "2026-07-01" and o.period.bitis.isoformat() == "2026-07-31"
    assert o.batch.summary["hatalar"] == []
    lines = list(session.scalars(select(JournalLine).where(JournalLine.batch_id == o.batch.id).order_by(JournalLine.id)))
    assert len(lines) == 5
    assert lines[0].hesap_kodu == "100.01" and lines[0].borc == 5000_00 and lines[0].fis_tipi == "AÇILIŞ"
    assert lines[2].evrak_no == "ABC2026000000001" and lines[2].madde_no == "2"
    assert sum(j.borc for j in lines) == sum(j.alacak for j in lines) == 6200_00


def test_edefter_zip_kebir_ve_berat_atlanir(session, client_period):
    c, _ = client_period
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("GIB-1234567890-202607-KB-000000.xml", '<?xml version="1.0"?><berat><imza/></berat>')
        zf.writestr("1234567890-202607-K-000000.xml", defter("ledger"))
    o = ingest(session, c, "edefter-202607.zip", buf.getvalue(), None)
    assert o.status == "aktarildi", o.message
    assert o.batch.doc_type == "kebir"


def test_edefter_dengesiz_madde_hata(session, client_period):
    c, _ = client_period
    o = ingest(session, c, "bozuk.xml", defter(bozuk=True), None)
    assert o.status == "aktarildi"
    assert any("Madde 2" in h for h in o.batch.summary["hatalar"])


def test_edefter_baska_xml_reddedilir(session, client_period):
    c, p = client_period
    o = ingest(session, c, "baska.xml", b"<?xml version='1.0'?><kok/>", p)
    assert o.status == "hata" and "e-Defter" in o.message
