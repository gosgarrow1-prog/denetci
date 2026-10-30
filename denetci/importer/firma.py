"""Dosyadaki firma unvanı ile mükellef unvanının karşılaştırılması (yanlış mükellefe yüklemeyi önlemek için).

Hukuki ve genel ekler (San., Tic., Ltd. Şti., A.Ş., İmalat, İthalat...) yok sayılır; kalan ayırt edici
kelimelerin en az yarısı ortaksa unvanlar aynı firma sayılır.
"""

import re

from ..parsing import fold

GENEL = {
    "ve", "san", "sanayi", "tic", "ticaret", "ltd", "sti", "limited", "sirketi", "sirket", "as", "anonim", "imalat",
    "ithalat", "ihracat", "ic", "dis", "ins", "insaat", "paz", "pazarlama", "mlz", "malz", "malzemeleri", "ltdsti",
    "sanve", "ticltdsti", "koll", "kollektif", "komandit", "turzm", "turizm", "ith", "ihr", "ihc", "ve",
}


def tokens(name: str) -> set[str]:
    words = re.split(r"[^0-9a-z]+", fold(name or ""))
    return {w for w in words if len(w) >= 2 and w not in GENEL}


def score(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def eslesir(dosyadaki: str, unvan: str) -> bool:
    return score(dosyadaki, unvan) >= 0.5


def en_iyi(dosyadaki: str, clients) -> object | None:
    """Dosyadaki unvana en çok benzeyen mükellef (eşleşme eşiğini geçen yoksa None)."""
    best, best_s = None, 0.0
    for c in clients:
        sc = score(dosyadaki, c.unvan)
        if sc > best_s:
            best, best_s = c, sc
    return best if best_s >= 0.5 else None
