"""Büro bilgileri: rapor başlığında ve altında görünen unvan, iletişim bilgileri ve logolar.

Uygulama klasöründeki buro/ klasöründen okunur (DENETCI_BURO ile değiştirilebilir):
    buro.json       {"unvan": ..., "alt_baslik": ..., "adres": ..., "eposta": ..., "web": ..., "telefonlar": [...]}
    logo-acik.png   açık zemin için (rapor)
    logo-koyu.png   koyu zemin için (sol menü)
Klasör yoksa uygulama Denetçi adıyla çalışır.
"""

import json
import os
from pathlib import Path

LOGOLAR = ("acik", "koyu")


def klasor() -> Path:
    return Path(os.environ.get("DENETCI_BURO", Path.cwd() / "buro"))


def bilgiler() -> dict:
    """Büro bilgileri ve hangi logoların bulunduğu. Dosya okunamazsa boş sözlük."""
    d = klasor()
    try:
        b = json.loads((d / "buro.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        b = {}
    if not isinstance(b, dict):
        b = {}
    b["logolar"] = {t: (d / f"logo-{t}.png").is_file() for t in LOGOLAR}
    return b


def logo_yolu(tema: str) -> Path | None:
    if tema not in LOGOLAR:
        return None
    p = klasor() / f"logo-{tema}.png"
    return p if p.is_file() else None
