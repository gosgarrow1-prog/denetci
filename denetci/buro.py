"""Büro bilgileri: rapor başlığında ve altında görünen unvan, iletişim bilgileri ve logolar.

Uygulama klasöründeki buro/ klasöründen okunur (DENETCI_BURO ile değiştirilebilir):
    buro.json       {"unvan": ..., "alt_baslik": ..., "adres": ..., "eposta": ..., "web": ..., "telefonlar": [...]}
    logo-acik.png   açık zemin için (rapor)
    logo-koyu.png   koyu zemin için (sol menü)
Klasör yoksa uygulama Denetçi adıyla çalışır.

Birden fazla büro: her büronun bilgileri ve logoları <veri>/burolar/<no>/ klasöründe durur (uygulamadaki
"Büro bilgileri" formu buraya yazar). 1 numaralı büroda bu klasörde olmayan dosya buro/ klasöründen okunur.
"""

import json
import os
from pathlib import Path

LOGOLAR = ("acik", "koyu")


ALANLAR = ("unvan", "alt_baslik", "adres", "eposta", "web")


def klasor() -> Path:
    return Path(os.environ.get("DENETCI_BURO", Path.cwd() / "buro"))


def buro_klasoru(buro_id: int | None) -> Path:
    from .config import data_dir
    return data_dir() / "burolar" / str(int(buro_id or 1))


def _oku(path: Path) -> dict:
    try:
        b = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return b if isinstance(b, dict) else {}


def dosya_bilgileri() -> dict:
    return _oku(klasor() / "buro.json")


def _dosya(buro_id: int | None, ad: str) -> Path | None:
    p = buro_klasoru(buro_id) / ad
    if p.is_file():
        return p
    if int(buro_id or 1) == 1 and (klasor() / ad).is_file():
        return klasor() / ad
    return None


def bilgiler(buro_id: int | None = 1) -> dict:
    """Büro bilgileri ve hangi logoların bulunduğu. Dosya okunamazsa boş sözlük."""
    p = _dosya(buro_id, "buro.json")
    b = _oku(p) if p else {}
    b["logolar"] = {t: _dosya(buro_id, f"logo-{t}.png") is not None for t in LOGOLAR}
    return b


def kaydet(buro_id: int, alanlar: dict, telefonlar: list[str]) -> None:
    """Formdan gelen büro bilgilerini yazar; dosyadaki diğer anahtarlar (ör. kullanicilar) korunur."""
    d = buro_klasoru(buro_id)
    d.mkdir(parents=True, exist_ok=True)
    b = bilgiler(buro_id)
    b.pop("logolar", None)
    for k in ALANLAR:
        v = str(alanlar.get(k) or "").strip()[:300]
        if v:
            b[k] = v
        else:
            b.pop(k, None)
    b["telefonlar"] = [x.strip()[:40] for x in telefonlar if x.strip()][:4]
    (d / "buro.json").write_text(json.dumps(b, ensure_ascii=False, indent=2), encoding="utf-8")


def logo_kaydet(buro_id: int, tema: str, icerik: bytes) -> None:
    if tema not in LOGOLAR:
        raise ValueError("tema")
    if not icerik.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Logo PNG biçiminde olmalı.")
    if len(icerik) > 2 * 1024 * 1024:
        raise ValueError("Logo 2 MB'tan küçük olmalı.")
    d = buro_klasoru(buro_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"logo-{tema}.png").write_bytes(icerik)


def logo_yolu(tema: str, buro_id: int | None = 1) -> Path | None:
    if tema not in LOGOLAR:
        return None
    return _dosya(buro_id, f"logo-{tema}.png")


def hazirlayan(kullanici_adi: str, ad: str | None = None, unvan: str | None = None, buro_id: int | None = 1) -> dict:
    """Raporu hazırlayan: buro.json'daki "kullanicilar" tablosundan ad ve unvan; tanımlı değilse kullanıcı adı.

        "kullanicilar": {"burak": {"ad": "Burak ALTINOK", "unvan": "S.M.Mali Müşavir"}}
    """
    if ad:
        return {"ad": ad, "unvan": unvan or ""}
    tablo = bilgiler(buro_id).get("kullanicilar") or {}
    k = None
    if isinstance(tablo, dict):
        # Kullanıcı adı büyük/küçük harf farkıyla yazılmış olabilir (ör. "Baltinok" / "baltinok").
        k = next((v for ad, v in tablo.items() if str(ad).casefold() == kullanici_adi.casefold()), None)
    if isinstance(k, str):
        k = {"ad": k}
    if not isinstance(k, dict) or not k.get("ad"):
        return {"ad": kullanici_adi, "unvan": ""}
    return {"ad": k["ad"], "unvan": k.get("unvan", "")}
