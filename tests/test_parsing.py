from datetime import date, datetime

import pytest

from denetci.parsing import ParseError, detect_number_format, fold, kurus_str, parse_account_code, parse_amount, parse_date


@pytest.mark.parametrize("text,expected", [
    ("1.234.567,89", 123456789),
    ("1234567,89", 123456789),
    ("(1.234,56)", -123456),
    ("1.234,56-", -123456),
    ("-1.234,56", -123456),
    ("12,5", 1250),
    ("1.000", 100000),
    (" 48.250,00 TL", 4825000),
    ("0,005", 1),  # yarım kuruş yukarı yuvarlanır
])
def test_turkish_amounts(text, expected):
    assert parse_amount(text, "tr") == expected


def test_empty_amounts():
    for v in (None, "", "-", "  "):
        assert parse_amount(v) is None


def test_numeric_cells_exact():
    assert parse_amount(0.1 + 0.2) == 30  # kayan nokta hatası kuruşa taşınmaz
    assert parse_amount(186350.0) == 18635000


def test_english_amounts():
    assert parse_amount("1,234,567.89", "en") == 123456789


def test_invalid_amount():
    with pytest.raises(ParseError):
        parse_amount("12a,00")


def test_detect_format():
    assert detect_number_format(["1.234,56", "12,00", "3.000"]) == "tr"
    assert detect_number_format(["1,234.56", "12.00"]) == "en"


def test_dates():
    assert parse_date("31.12.2025") == date(2025, 12, 31)
    assert parse_date("31/12/2025") == date(2025, 12, 31)
    assert parse_date(datetime(2026, 7, 5, 10, 0)) == date(2026, 7, 5)
    assert parse_date(46208) == date(2026, 7, 5)  # Excel seri tarihi
    with pytest.raises(ParseError):
        parse_date("32.13.2025")


def test_account_codes_keep_leading_zeros():
    assert parse_account_code("0100.01")[0] == "0100.01"
    assert parse_account_code("100.01.001")[0] == "100.01.001"
    code, warn = parse_account_code(100.1)
    assert code == "100.1" and warn
    assert parse_account_code("GENEL TOPLAM")[0] is None


def test_fold_turkish():
    assert fold("HESAP ADI") == fold("Hesap Adı") == "hesap adi"
    assert fold("Borç Bakiye") == "borc bakiye"
    assert fold("İŞLEM TARİHİ") == "islem tarihi"


def test_kurus_str():
    assert kurus_str(4825000) == "48.250,00"
    assert kurus_str(-100) == "-1,00"
