from unittest import mock

import pytest

from denetci import __main__ as cli


def test_misafir_parola_6_karakter(data_root):
    with mock.patch("getpass.getpass", side_effect=["123456", "123456"]):
        cli.main(["kullanici-ekle", "misafir", "--misafir"])
    with mock.patch("getpass.getpass", side_effect=["654321", "654321"]):
        cli.main(["parola", "misafir"])
    with mock.patch("getpass.getpass", side_effect=["123456", "123456"]), pytest.raises(SystemExit):
        cli.main(["kullanici-ekle", "buro"])
