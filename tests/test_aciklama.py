from types import SimpleNamespace

from denetci import aciklama
from denetci.checks.registry import ALL_RULES


def test_her_kontrolun_neden_onemli_metni_var():
    eksik = [r.id for r, _ in ALL_RULES if r.id not in aciklama.NEDEN]
    assert eksik == []


def test_kesinlik_ve_istisna_tekrarsiz():
    d = aciklama.dikkat("Kayıtlar kesin. Uygunsuzluk olup olmadığı belirsiz: Sınır işlem bazındadır. Ücret ödemeleri de girebilir.",
                        "Sınır işlem bazındadır. Ücret ödemeleri de girebilir.")
    assert d == ["Sınır işlem bazındadır.", "Ücret ödemeleri de girebilir."]
    assert aciklama.dikkat("Aritmetik olarak kesin.", None) == []


def test_adimlar_ve_belgeler():
    m, b = aciklama.adimlar("Ödemenin belgesine bakın; tahsilatları kontrol edin. Gerekli belgeler: ödeme belgesi, banka ekstresi.")
    assert m == ["Ödemenin belgesine bakın.", "Tahsilatları kontrol edin."]
    assert b == ["ödeme belgesi", "banka ekstresi"]


def test_dayanak_ic_notu_ayiklanir():
    metin, teyitsiz = aciklama.dayanak("100 Kasa hesabının işleyişi. Dayanak kaynağı henüz kullanıcı tarafından doğrulanmadı.", False)
    assert "doğrulanmadı" not in metin and teyitsiz


def test_bulgu_ozeti():
    f = SimpleNamespace(kind="A", rule_id="K-02", explanation="Fiş denk değil.", certainty="Aritmetik olarak kesin.",
                        next_step="Fişi Luca'da açın.")
    a = aciklama.bulgu(f)
    assert a["hukum"] == "Kesin hata" and a["neden"] and a["adimlar"] == ["Fişi Luca'da açın."] and a["dikkat"] == []
