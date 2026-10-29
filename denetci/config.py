"""Uygulama ayarları. Veri klasörü DENETCI_VERI ortam değişkeniyle değiştirilebilir."""

import os
from pathlib import Path

# Aritmetik farklarda tolerans: 1 TL (kuruş cinsinden). 29.10.2026 kararı.
TOLERANS_KURUS = 100

# Kurala dayalı bulgularda "Yüksek" önem eşiği: 5.000 TL veya aktif toplamının %0,5'i.
YUKSEK_ESIK_TUTAR_KURUS = 5_000_00
YUKSEK_ESIK_AKTIF_ORAN = 0.005

# Dönemler arası değişim eşiği: hem %25 hem 10.000 TL (plandaki başlangıç önerisi).
DEGISIM_ORAN = 0.25
DEGISIM_TUTAR_KURUS = 10_000_00

MAX_YUKLEME_BAYT = 20 * 1024 * 1024
IZINLI_UZANTILAR = {".xlsx", ".csv"}

RULESET_VERSION = "0.2.0"
ENGINE_VERSION = "0.1.0"


def data_dir() -> Path:
    path = Path(os.environ.get("DENETCI_VERI", Path.home() / "DenetciVeri"))
    path.mkdir(parents=True, exist_ok=True)
    return path
