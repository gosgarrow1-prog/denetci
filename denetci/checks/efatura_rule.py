"""K-30: e-Fatura / e-Arşiv listesi ile defter (fatura fatura)."""

from ..efatura import eslestir
from ..parsing import kurus_str
from .kdv import KDV_DAYANAK
from .rules import D0, Context, Draft, Evidence, Result, Rule

K30 = Rule("K-30", "1.0", "e-Fatura listesi ile defter",
           "Yüklenen gelen/giden fatura listesindeki her fatura, yevmiyedeki kayıtla fatura numarasından (bulunamazsa KDV "
           "tutarı ve tarihten) eşleştirilir; deftere işlenmemiş veya KDV/matrahı farklı kaydedilmiş faturalar listelenir.",
           "K", ("THP_VUK",), (), KDV_DAYANAK, False,
           "İptal edilen veya reddedilen faturalar listede kalabilir; fatura sonraki aya kaydedilmiş olabilir; açıklamasında "
           "numara olmayan kayıtlar tutarla eşleştirilir ve yanlış eşleşebilir.", D0, "Orta")

YON_ADI = {"giden": "Giden (satış)", "gelen": "Gelen (alış)"}


def _ev(f):
    return [Evidence(j.raw_row_id, f"Yevmiye {j.tarih:%d.%m.%Y} {j.madde_no or j.fis_no}, {j.hesap_kodu}") for j in f.lines[:8]]


def k30(ctx: Context) -> Result:
    if not ctx.faturalar:
        return Result(K30, "uygulanmadi", "Uygulanmadı: bu dönem için e-Fatura / e-Arşiv listesi yüklenmemiş.")
    drafts, parts = [], []
    for yon, rows in ctx.faturalar.items():
        s = eslestir(ctx, rows, yon)
        if s is None:
            return Result(K30, "calistirilamadi", "Çalıştırılamadı: dönemin yevmiyesi (veya kebiri) yüklenmemiş.")
        yok = [e for e in s.eslesmeler if e.durum == "yok"]
        tutar = [e for e in s.eslesmeler if e.durum == "tutar"]
        parts.append(f"{YON_ADI[yon]}: {len(rows)} faturadan {s.say('eslesti')} eşleşti")
        if yok:
            kdv = sum(e.fatura.kdv_toplam for e in yok)
            liste = "; ".join(f"{e.fatura.no} ({e.fatura.tarih:%d.%m.%Y}, KDV {kurus_str(e.fatura.kdv_toplam)})" for e in yok[:15])
            drafts.append(Draft(f"{YON_ADI[yon]} {len(yok)} fatura defterde bulunamadı", K30.default_severity,
                                "391" if yon == "giden" else "191", kdv, f"efatura-yok-{yon}",
                                f"Listede olup yevmiyede numarası ya da tutarı bulunamayan faturalar: {liste}"
                                + (" …" if len(yok) > 15 else "") + f". Toplam KDV {kurus_str(kdv)} TL.",
                                K30.exceptions, "Faturaların Luca'da kayıtlı olup olmadığını ve hangi aya kaydedildiğini "
                                                "kontrol edin; kayıtlıysa açıklamaya fatura numarasını yazın.", []))
        if tutar:
            fark = sum(e.kdv_fark or 0 for e in tutar)
            liste = "; ".join(f"{e.fatura.no}: KDV fatura {kurus_str(e.fatura.kdv_toplam)} / defter {kurus_str(e.defter_kdv)}"
                              for e in tutar[:15])
            ev = [x for e in tutar[:5] for x in _ev(e.fis)]
            drafts.append(Draft(f"{YON_ADI[yon]} {len(tutar)} fatura defterde farklı tutarla kayıtlı", K30.default_severity,
                                "391" if yon == "giden" else "191", fark, f"efatura-tutar-{yon}",
                                f"Defterle eşleşen ama tutarı farklı faturalar: {liste}" + (" …" if len(tutar) > 15 else "") + ".",
                                K30.exceptions, "Faturanın KDV oranını, tevkifatını ve kalemlerini Luca'daki fişle karşılaştırın.", ev))
    if drafts:
        return Result(K30, "bulgu", "; ".join(parts) + ".", drafts)
    return Result(K30, "gecti", "; ".join(parts) + "; tüm faturalar defterle uyumlu.")


EFATURA_RULES = [(K30, k30)]
