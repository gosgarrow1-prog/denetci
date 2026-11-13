"""Mevzuat asistanı: vergi/muhasebe sorularını Claude ile yanıtlar, resmî kaynaklardan web araması yaparak.

- Anahtar: ANTHROPIC_API_KEY (sunucuda /etc/denetci.env). Tanımlı değilse asistan kapalıdır.
- Model: DENETCI_YZ_MODEL (varsayılan claude-haiku-5-5), düşünme düzeyi DENETCI_YZ_EFOR (varsayılan medium).
- Arama yalnızca resmî ve mesleki kaynaklarla sınırlıdır; yanıt kaynak bağlantılarıyla döner.
- Soruya mükellef verisi eklenmez; yalnızca kullanıcının yazdığı metin gönderilir.
"""

import os
from dataclasses import dataclass, field

MODEL = os.environ.get("DENETCI_YZ_MODEL", "claude-haiku-5-5")  # Burak: minimum maliyet; daha isabetli yanıt için claude-opus-5-5
EFOR = os.environ.get("DENETCI_YZ_EFOR", "medium")
ALANLAR = ["gib.gov.tr", "mevzuat.gov.tr", "resmigazete.gov.tr", "kms.kaysis.gov.tr", "turmob.org.tr", "sgk.gov.tr",
           "ticaret.gov.tr", "kgk.gov.tr"]
EN_FAZLA_ARAMA = 4
SISTEM = (
    "Türkiye'de çalışan bir serbest muhasebeci mali müşavirlik bürosuna vergi ve muhasebe mevzuatı konusunda yardım "
    "ediyorsun. Kullanıcılar meslek mensubudur.\n"
    "- Türkçe, sade ve kısa yanıt ver: önce doğrudan cevap (oran, süre, tutar, evet/hayır), sonra dayanak ve "
    "dikkat edilecek istisnalar.\n"
    "- Oran, tutar, süre ve tarihleri mutlaka güncel resmî kaynaktan (kanun, tebliğ, Cumhurbaşkanı kararı, GİB) "
    "web aramasıyla doğrula; dayanağı madde numarasıyla yaz (ör. GVK 94/5-a, KDVK 29).\n"
    "- Kaynakta bulamadığın ya da emin olmadığın bilgiyi kesin gibi yazma; 'doğrulanamadı' de ve neye bakılması "
    "gerektiğini söyle.\n"
    "- Mükellefe özel durumlarda hangi bilgiye göre sonucun değişeceğini belirt. Yanıtın görüş ya da özelge "
    "yerine geçmediğini gerektiğinde hatırlat.\n"
    "- Markdown başlığı kullanma; kısa paragraflar ve gerekirse madde işaretli liste yaz."
)


class YzHatasi(RuntimeError):
    pass


@dataclass
class Cevap:
    metin: str
    kaynaklar: list[tuple[str, str]] = field(default_factory=list)  # (başlık, adres)
    giris: int = 0
    cikis: int = 0
    arama: int = 0
    model: str = MODEL

    @property
    def maliyet_usd(self) -> float:
        return maliyet(self.model, self.giris, self.cikis, self.arama)


FIYAT = {"opus": (4, 20), "sonnet": (2, 10), "haiku": (0.10, 0.50)}  # $/1M giriş, çıkış (liste fiyatı, 2026-11)


def maliyet(model: str | None, giris: int, cikis: int, arama: int) -> float:
    """Yaklaşık maliyet ($). Web araması 1000'i 10 $."""
    g, c = next((v for k, v in FIYAT.items() if k in (model or "")), FIYAT["opus"])
    return giris * g / 1e6 + cikis * c / 1e6 + arama * 0.01


def hazir() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def sor(soru: str, gecmis: list[dict] | None = None) -> Cevap:
    """Soruyu yanıtlar. gecmis: önceki [{'soru':..., 'cevap':...}] (aynı konuşmanın devamı için, en fazla 3)."""
    if not hazir():
        raise YzHatasi("Mevzuat asistanı için API anahtarı tanımlı değil.")
    import anthropic

    soru = (soru or "").strip()[:4000]
    if not soru:
        raise YzHatasi("Soru boş.")
    mesajlar = []
    for g in (gecmis or [])[-3:]:
        mesajlar += [{"role": "user", "content": g["soru"]}, {"role": "assistant", "content": g["cevap"]}]
    mesajlar.append({"role": "user", "content": soru})
    client = anthropic.Anthropic(timeout=180.0, max_retries=2)
    haiku = "haiku" in MODEL
    # Haiku'da gelişmiş (dinamik süzmeli) arama ve sunucu tarafı yedek model yok.
    araclar = [{"type": "web_search_20250305" if haiku else "web_search_20260209", "name": "web_search",
                "max_uses": EN_FAZLA_ARAMA, "allowed_domains": ALANLAR}]
    ek = {} if haiku else {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    cevap = Cevap("", model=MODEL)
    try:
        for _ in range(4):  # sunucu aracı uzun sürerse yanıt pause_turn ile bölünebilir
            r = client.beta.messages.create(
                model=MODEL, max_tokens=8000, system=SISTEM, messages=mesajlar, tools=araclar,
                output_config={"effort": EFOR}, cache_control={"type": "ephemeral"}, **ek)
            u = r.usage
            cevap.giris += (u.input_tokens or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0)
            cevap.cikis += u.output_tokens or 0
            stu = getattr(u, "server_tool_use", None)
            if stu is not None:
                cevap.arama += getattr(stu, "web_search_requests", 0) or 0
            _topla(r.content, cevap)
            if r.stop_reason == "pause_turn":
                mesajlar = mesajlar + [{"role": "assistant", "content": r.content}]
                continue
            if r.stop_reason == "refusal":
                raise YzHatasi("Asistan bu soruyu yanıtlamadı. Soruyu farklı biçimde yazmayı deneyin.")
            break
    except anthropic.AuthenticationError:
        raise YzHatasi("API anahtarı geçersiz. Anahtarı kontrol edin.")
    except anthropic.RateLimitError:
        raise YzHatasi("Kısa sürede çok soru soruldu ya da kullanım sınırı doldu. Biraz sonra tekrar deneyin.")
    except anthropic.APIConnectionError:
        raise YzHatasi("Yapay zekâ hizmetine bağlanılamadı. İnternet bağlantısını kontrol edin.")
    except anthropic.APIStatusError as e:
        raise YzHatasi(f"Yapay zekâ hizmeti hata verdi ({e.status_code}). Biraz sonra tekrar deneyin.")
    cevap.metin = cevap.metin.strip()
    if not cevap.metin:
        raise YzHatasi("Yanıt alınamadı. Tekrar deneyin.")
    return cevap


def _topla(bloklar, cevap: Cevap):
    gorulen = {u for _, u in cevap.kaynaklar}
    for b in bloklar:
        if b.type != "text":
            continue
        cevap.metin += b.text
        for c in getattr(b, "citations", None) or []:
            url = getattr(c, "url", None)
            if url and url not in gorulen:
                gorulen.add(url)
                cevap.kaynaklar.append(((getattr(c, "title", None) or url)[:200], url))
