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
IZINLI_UZANTILAR = {".xlsx", ".csv", ".xml", ".zip"}  # Luca tabloları, e-Defter; beyanname PDF'leri ayrı yoldan alınır

RULESET_VERSION = "0.12.0"
ENGINE_VERSION = "0.1.0"


def telefon_kodu() -> bool:
    """Girişte telefon doğrulama kodu (TOTP) istensin mi? 29.10.2026 kararı: uygulama yalnızca bu bilgisayarda
    (127.0.0.1) çalıştığı sürece kapalı. Sunucuya taşınırsa DENETCI_TELEFON_KODU=1 ile açılmalı."""
    return os.environ.get("DENETCI_TELEFON_KODU", "1" if sunucu() else "0") == "1"


def sunucu() -> bool:
    """Uygulama internetteki bir sunucuda mı çalışıyor? (DENETCI_SUNUCU=1) O zaman telefon kodu varsayılan olarak
    açıktır, oturum çerezi yalnızca https üzerinden gönderilir ve arşiv klasörü tarayıcıdan indirilir."""
    return os.environ.get("DENETCI_SUNUCU", "0") == "1"


def yedek_dir() -> Path:
    path = Path(os.environ.get("DENETCI_YEDEK", data_dir().parent / "DenetciYedek"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_dir() -> Path:
    path = Path(os.environ.get("DENETCI_VERI", Path.home() / "DenetciVeri"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def arsiv_dir() -> Path:
    """Yüklenen belgelerin kopyalandığı, mükellef/yıl/ay düzenindeki arşiv klasörü."""
    if os.environ.get("DENETCI_ARSIV"):
        path = Path(os.environ["DENETCI_ARSIV"])
    elif os.environ.get("DENETCI_VERI"):
        path = data_dir() / "Belgeler"
    else:
        path = Path.home() / "Documents" / "Denetçi Belgeleri"
    path.mkdir(parents=True, exist_ok=True)
    return path
