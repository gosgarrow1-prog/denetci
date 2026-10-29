import os
from datetime import date

import pytest

from denetci import db, synthetic
from denetci.models import Client, Period


@pytest.fixture()
def data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("DENETCI_VERI", str(tmp_path / "veri"))
    db.reset_engine()
    db.init_db()
    yield tmp_path
    db.reset_engine()


@pytest.fixture()
def session(data_root):
    s = db.SessionLocal()
    yield s
    s.close()


@pytest.fixture()
def client_period(session):
    c = Client(unvan="DEMO A", is_demo=True)
    session.add(c)
    session.flush()
    p = Period(client_id=c.id, baslangic=date(2026, 7, 1), bitis=date(2026, 9, 30), tur="uc_aylik")
    session.add(p)
    session.flush()
    return c, p


@pytest.fixture()
def files_hatali(data_root):
    return synthetic.generate(data_root / "syn_h", hatali=True)


@pytest.fixture()
def files_temiz(data_root):
    return synthetic.generate(data_root / "syn_t", hatali=False)
