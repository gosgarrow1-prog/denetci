"""Şirkete yönelik öneriler: bulgulardan, finansal oranlardan ve vergi hesaplarından kurallarla üretilir.

Eşikler genel kabul gören yaklaşık değerlerdir; sektöre ve şirketin durumuna göre değişir. Öneriler mesleki
değerlendirme yerine geçmez ve "değerlendirin" biçiminde yazılır. Tutarlar kuruştur.
"""

from dataclasses import dataclass

from .parsing import kurus_str

ONCELIK_SIRA = {"yuksek": 0, "orta": 1, "bilgi": 2}
ONCELIK_ADI = {"yuksek": "ÖNEMLİ", "orta": "DİKKAT", "bilgi": "BİLGİ"}


@dataclass
class Oneri:
    oncelik: str  # yuksek, orta, bilgi
    alan: str  # Finansal yapı, Kârlılık, Vergi, Muhasebe kayıtları
    baslik: str
    metin: str
    dayanak: str = ""  # hangi veriden çıktığı

    @property
    def grup(self) -> str:
        """sirket: şirketin yönetimine yönelik; kayit: muhasebe kayıtlarında yapılacaklar."""
        return "kayit" if self.alan == "Muhasebe kayıtları" else "sirket"


def _pct(x: float) -> str:
    return f"%{x * 100:.1f}".replace(".", ",")


def _x(x: float) -> str:
    return f"{x:.2f}".replace(".", ",")


def build(fin, prev_fin=None, findings=(), gv=None, period=None) -> list[Oneri]:
    out: list[Oneri] = []
    r = {o.key: o.value for o in fin.oranlar} if fin else {}
    pr = {o.key: o.value for o in prev_fin.oranlar} if prev_fin else {}
    k = fin.kalemler if fin else {}

    # ---- Finansal yapı ----
    if fin and fin.has_balance:
        cari = r.get("cari")
        if cari is not None and cari < 1:
            out.append(Oneri("yuksek", "Finansal yapı", "Kısa vadeli borçlar dönen varlıklardan fazla",
                             f"Cari oran {_x(cari)}. Önümüzdeki aylarda borç ödemelerinde sıkışma yaşanabilir. Alacak "
                             "tahsilatını hızlandırmayı, kısa vadeli kredilerin vadesini uzatmayı veya yapılandırmayı "
                             "değerlendirin.", "Cari oran < 1"))
        elif cari is not None and cari < 1.2:
            out.append(Oneri("orta", "Finansal yapı", "Likidite tamponu dar",
                             f"Cari oran {_x(cari)}. Dönen varlıklar kısa vadeli borçları ancak karşılıyor; aylık nakit "
                             "akış planı yapılması önerilir.", "Cari oran 1–1,2"))
        nakit = r.get("nakit")
        if nakit is not None and nakit < 0.1 and k.get("kvyk", 0) > 0:
            out.append(Oneri("orta", "Finansal yapı", "Nakit mevcudu kısa vadeli borçlara göre düşük",
                             f"Nakit oranı {_x(nakit)}. Yakın vadeli ödemeler tahsilatlara bağlı; vadesi gelen borçlar "
                             "ile beklenen tahsilatları haftalık izlemeyi değerlendirin.", "Nakit oranı < 0,1"))
        asit = r.get("asit")
        if asit is not None and cari is not None and cari >= 1 and asit < 0.7:
            out.append(Oneri("orta", "Finansal yapı", "Likidite büyük ölçüde stoklara bağlı",
                             f"Asit-test oranı {_x(asit)}, cari oran {_x(cari)}. Stoklar nakde dönmeden borçların "
                             "ödenmesi zorlaşabilir; yavaş dönen stokları ve sipariş miktarlarını gözden geçirin.",
                             "Asit-test < 0,7"))
        ozk = k.get("ozkaynak")
        if ozk is not None and ozk < 0:
            out.append(Oneri("yuksek", "Finansal yapı", "Özkaynak eksiye düşmüş",
                             f"Özkaynaklar (dönem sonucu dahil) {kurus_str(ozk)} TL. TTK 376 kapsamında sermaye kaybı "
                             "ve borca batıklık değerlendirmesi gerekebilir; sermaye artırımı veya ortak desteği "
                             "seçeneklerini değerlendirin.", "Özkaynak < 0"))
        elif ozk is not None and k.get("sermaye", 0) > 0 and ozk < k["sermaye"] / 2:
            out.append(Oneri("yuksek", "Finansal yapı", "Özkaynak sermayenin yarısının altında",
                             f"Özkaynaklar {kurus_str(ozk)} TL, ödenmiş sermaye {kurus_str(k['sermaye'])} TL. TTK 376 "
                             "kapsamında sermaye kaybı değerlendirmesi gerekebilir (kanuni yedekler de hesaba katılır).",
                             "Özkaynak < sermaye / 2"))
        else:
            bo = r.get("borc_ozk")
            if bo is not None and bo > 3:
                out.append(Oneri("orta", "Finansal yapı", "Borçluluk yüksek",
                                 f"Yabancı kaynaklar özkaynağın {_x(bo)} katı. Yeni borçlanma öncesi faiz yükünü ve geri "
                                 "ödeme takvimini değerlendirin.", "Borç / özkaynak > 3"))
        aktif = fin.aktif or 0
        kasa = k.get("kasa", 0)
        if aktif > 0 and kasa > 0 and kasa / aktif > 0.05:
            out.append(Oneri("orta", "Nakit yönetimi", "Kasa bakiyesi yüksek",
                             f"100 Kasa bakiyesi {kurus_str(kasa)} TL (aktifin {_pct(kasa / aktif)}). Fiili kasa "
                             "sayımıyla karşılaştırın; gerçekte olmayan tutar ortaklara borç verilmiş sayılabilir "
                             "(adat ve örtülü kazanç riski).", "Kasa / aktif > %5"))
        ortak = k.get("ortaklardan_alacak", 0)
        if ortak > 0:
            out.append(Oneri("orta", "Vergi", "Ortaklardan alacak bakiyesi var",
                             f"131/231 bakiyesi {kurus_str(ortak)} TL. Ortaklara kullandırılan tutarlar için adat "
                             "(faiz) hesaplanıp faturalandırılmazsa transfer fiyatlandırması yoluyla örtülü kazanç "
                             "sayılabilir; adat hesaplanmasını değerlendirin.", "131/231 borç bakiyesi"))

    # ---- Kârlılık ----
    if fin and fin.has_income and k.get("net_satis"):
        if k.get("donem_kari", 0) < 0:
            out.append(Oneri("yuksek", "Kârlılık", "Dönem zararla sonuçlanıyor",
                             f"Vergi öncesi sonuç {kurus_str(k['donem_kari'])} TL. Gider kalemlerini ve satış fiyatlarını "
                             "maliyet artışlarıyla birlikte gözden geçirin.", "Dönem kârı < 0"))
        bm, pbm = r.get("brut_marj"), pr.get("brut_marj")
        if bm is not None and pbm is not None and pbm - bm > 0.05:
            out.append(Oneri("orta", "Kârlılık", "Brüt kâr marjı önceki döneme göre düştü",
                             f"Brüt kâr marjı {_pct(pbm)} iken {_pct(bm)} oldu. Satış fiyatlarının maliyet artışına "
                             "göre güncellenip güncellenmediğini ve maliyet kayıtlarının tam olup olmadığını kontrol edin.",
                             "Brüt marj düşüşü > 5 puan"))
        if fin.flows_window and k.get("ticari_alacaklar", 0) > 0:
            gun = (fin.flows_window[1] - fin.flows_window[0]).days + 1
            sure = k["ticari_alacaklar"] / k["net_satis"] * gun if k["net_satis"] > 0 else None
            if sure is not None and sure > 90:
                out.append(Oneri("orta", "Finansal yapı", "Alacak tahsil süresi uzun",
                                 f"Ticari alacaklar yaklaşık {round(sure)} günlük satışa eşit. Vadesi geçmiş alacakları "
                                 "listeleyip takip edin; tahsili şüpheli olanlar için karşılık ayrılmasını (VUK 323) "
                                 "değerlendirin.", "Alacak / günlük satış > 90 gün"))

    # ---- Vergi ----
    if gv is not None:
        if gv.maliyet and gv.maliyet.ek:
            out.append(Oneri("yuksek", "Muhasebe kayıtları", "Satışların maliyeti kaydı yapılmalı",
                             f"Geçici vergi kârı {kurus_str(gv.maliyet.ek)} TL tahmini maliyetle hesaplandı. Beyandan önce "
                             "dönem sonu stok sayımına göre maliyet kaydının yapılması gerekir.", "Tahmini maliyet"))
        if gv.odenecek and gv.beyan:
            out.append(Oneri("bilgi", "Vergi", "Geçici vergi ödemesi için nakit ayırın",
                             f"Tahmini ödenecek geçici vergi {kurus_str(gv.odenecek)} TL; son gün {gv.beyan:%d.%m.%Y} "
                             "(parametre tablosuna göre).", "Geçici vergi ön hesaplaması"))
    if fin and fin.has_balance and k.get("net_satis") and k.get("devreden_kdv", 0) > k["net_satis"] * 0.1:
        out.append(Oneri("bilgi", "Vergi", "Devreden KDV birikmiş",
                         f"190 Devreden KDV {kurus_str(k['devreden_kdv'])} TL. İhracat, indirimli oranlı satış veya "
                         "tevkifatlı işlem varsa KDV iadesi talep edilip edilemeyeceğini değerlendirin; yoksa alış ve "
                         "satış KDV oranlarını kontrol edin.", "190 bakiyesi > dönem net satışının %10'u"))

    # ---- Bulgulardan ----
    sev = {"Kritik": "yuksek", "Yüksek": "yuksek", "Orta": "orta"}
    for f in list(findings)[:5]:
        out.append(Oneri(sev.get(f.severity, "bilgi"), "Muhasebe kayıtları", f.title, f.next_step, f"Bulgu {f.rule_id}"))

    out.sort(key=lambda o: ONCELIK_SIRA[o.oncelik])
    return out
