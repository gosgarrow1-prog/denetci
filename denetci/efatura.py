"""e-Fatura / e-Arşiv listesinin defterle fatura fatura eşleştirilmesi.

Eşleştirme önce fatura numarasıyla yapılır: Luca yevmiye açıklamasında fatura numarası yazar
(ör. "EF ABC2026000000123 01/09 FİRMA ADI"). Numara bulunamazsa aynı KDV tutarı ve ±3 gün içindeki tarihle eşleştirilir.
Giden faturada defter tarafı 391 alacak (KDV) ve 600–602 alacak (matrah); gelen faturada 191 borç (KDV) ve 191 dışındaki
borç kayıtları (matrah) alınır. Tutarlar kuruştur.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select

NO_RE = re.compile(r"\b([A-Z0-9]{3}20\d{2}\d{9})\b")
SATIS = ("600", "601", "602")


def _tok(text) -> set:
    return set(NO_RE.findall((text or "").upper()))


@dataclass
class Fis:
    key: tuple
    tarih: object
    no: str
    lines: list
    tokens: set = field(default_factory=set)

    def kdv(self, yon, lines=None):
        ls = lines if lines is not None else self.lines
        if yon == "giden":
            return sum(j.alacak - j.borc for j in ls if j.hesap_kodu.startswith("391"))
        return sum(j.borc - j.alacak for j in ls if j.hesap_kodu.startswith("191"))

    def matrah(self, yon, lines=None):
        ls = lines if lines is not None else self.lines
        if yon == "giden":
            return sum(j.alacak - j.borc for j in ls if j.hesap_kodu.startswith(SATIS))
        return sum(j.borc for j in ls if not j.hesap_kodu.startswith("191"))


def fisler(lines) -> list[Fis]:
    g = defaultdict(list)
    for j in lines:
        g[(j.fis_no, j.tarih)].append(j)
    out = []
    for k, ls in g.items():
        f = Fis(k, k[1], ls[0].madde_no or ls[0].fis_no, ls)
        for j in ls:
            f.tokens |= _tok(j.aciklama) | _tok(getattr(j, "evrak_no", None))
        out.append(f)
    return out


def yon_bul(session, client, rows) -> str | None:
    """Listedeki fatura numaraları defterde 391'li mi 191'li fişlerde mi geçiyor."""
    from .models import ImportBatch, JournalLine

    nos = {r.no.upper() for r in rows}
    if not nos:
        return None
    bas, bit = min(r.tarih for r in rows) - timedelta(days=40), max(r.tarih for r in rows) + timedelta(days=40)
    q = (select(JournalLine).join(ImportBatch, ImportBatch.id == JournalLine.batch_id)
         .where(JournalLine.client_id == client.id, JournalLine.tarih >= bas, JournalLine.tarih <= bit,
                ImportBatch.status == "onaylandi"))
    sat = al = 0
    seen = set()
    by_fis = defaultdict(list)
    for j in session.scalars(q):
        by_fis[(j.batch_id, j.fis_no, j.tarih)].append(j)
    for ls in by_fis.values():
        toks = set().union(*(_tok(j.aciklama) for j in ls)) & nos
        if not toks or toks <= seen:
            continue
        seen |= toks
        if any(j.hesap_kodu.startswith(("391",) + SATIS) and j.alacak for j in ls):
            sat += 1
        elif any(j.hesap_kodu.startswith("191") and j.borc for j in ls):
            al += 1
    if sat > al:
        return "giden"
    if al > sat:
        return "gelen"
    return None


@dataclass
class Eslesme:
    fatura: object
    durum: str  # eslesti, tutar, yok
    fis: Fis | None = None
    yontem: str | None = None  # numara, tutar
    defter_kdv: int | None = None
    defter_matrah: int | None = None

    @property
    def kdv_fark(self):
        return None if self.defter_kdv is None else self.defter_kdv - self.fatura.kdv_toplam

    @property
    def matrah_fark(self):
        return None if self.defter_matrah is None else self.defter_matrah - self.fatura.matrah_toplam


@dataclass
class Sonuc:
    yon: str
    eslesmeler: list
    defter_fazla: list  # listede olmayan KDV'li fişler

    def say(self, durum):
        return sum(1 for e in self.eslesmeler if e.durum == durum)


def eslestir(ctx, faturalar, yon: str) -> Sonuc | None:
    lines = ctx.yevmiye or ctx.kebir
    if not lines:
        return None
    lines = [j for j in lines if ctx.period_start <= j.tarih <= ctx.period_end]
    fs = fisler(lines)
    by_tok = defaultdict(list)
    for f in fs:
        for t in f.tokens:
            by_tok[t].append(f)
    tol = ctx.tolerance
    used = set()
    out = []
    bekleyen = []
    for fa in sorted(faturalar, key=lambda x: (x.tarih, x.no)):
        cand = by_tok.get(fa.no.upper())
        if not cand:
            bekleyen.append(fa)
            continue
        f = cand[0]
        own = [j for j in f.lines if fa.no.upper() in _tok(j.aciklama)]
        # Satırların bir kısmında numara yazıyorsa ve fişte başka fatura yoksa fişin tamamı alınır.
        if len(f.tokens) == 1 or not any(j.hesap_kodu.startswith(("391", "191")) for j in own):
            own = f.lines
        e = Eslesme(fa, "eslesti", f, "numara", f.kdv(yon, own), f.matrah(yon, own))
        used.add(f.key)
        out.append(e)
    # Numarası bulunamayanlar: aynı KDV, ±3 gün
    for fa in bekleyen:
        hit = None
        for f in fs:
            if f.key in used or abs((f.tarih - fa.tarih).days) > 3:
                continue
            k = f.kdv(yon)
            if k and abs(k - fa.kdv_toplam) <= tol and (not f.tokens or fa.no.upper() in f.tokens):
                hit = f
                break
        if hit:
            used.add(hit.key)
            out.append(Eslesme(fa, "eslesti", hit, "tutar", hit.kdv(yon), hit.matrah(yon)))
        else:
            out.append(Eslesme(fa, "yok"))
    for e in out:
        if e.durum == "eslesti":
            bad = abs(e.kdv_fark or 0) > tol
            if yon == "giden" and e.fatura.matrah_toplam and e.defter_matrah is not None:
                bad |= abs(e.matrah_fark) > tol
            if bad:
                e.durum = "tutar"
    fazla = [f for f in fs if f.key not in used and f.kdv(yon) > tol]
    out.sort(key=lambda e: ({"yok": 0, "tutar": 1, "eslesti": 2}[e.durum], e.fatura.tarih))
    return Sonuc(yon, out, fazla)


def neden(e: Eslesme, yon: str) -> list[str]:
    """Eşleşmeyen veya tutarı farklı fatura için olası nedenler."""
    if e.durum == "yok":
        if yon == "giden":
            return ["Fatura deftere işlenmemiş ya da sonraki aya kaydedilmiş olabilir.",
                    "Faturanın açıklamasına numara yazılmamış ve tutarı da farklı kaydedilmiş olabilir.",
                    "Fatura iptal edilmiş (GİB'de iptal/itiraz) ama listede duruyor olabilir."]
        return ["Alış faturası deftere işlenmemiş ya da sonraki ayda kaydedilmiş olabilir (indirim süresine dikkat).",
                "Fatura reddedilmiş veya iade edilmiş olabilir.",
                "KDV'si indirilmeyen bir gider olarak 191 kullanılmadan kaydedilmiş olabilir."]
    if e.durum == "tutar":
        out = []
        if e.kdv_fark:
            out.append("KDV tutarı farklı: faturadaki oran yerine başka oranla kaydedilmiş ya da KDV elle yuvarlanmış olabilir.")
            if yon == "gelen":
                out.append("Tevkifatlı faturada KDV'nin yalnızca satıcıya ödenen kısmı 191'e alınmış olabilir.")
        if e.matrah_fark:
            out.append("Matrah farklı: iskonto, kur farkı veya faturadaki ek bir kalem ayrı bir hesaba kaydedilmiş olabilir.")
        if e.yontem == "tutar":
            out.append("Bu fatura numarasıyla değil, tutar ve tarihle eşleştirildi; doğru fişle eşleştiğini kontrol edin.")
        return out
    return []
