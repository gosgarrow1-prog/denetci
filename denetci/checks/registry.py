"""Çalıştırılan tüm kuralların listesi."""

from .gecici_rules import GECICI_RULES
from .kdv import KDV_RULES
from .kdv_oran import ORAN_RULES
from .efatura_rule import EFATURA_RULES
from .risk import RISK_RULES
from .rules import ALL_RULES as TEMEL_KURALLAR

ALL_RULES = TEMEL_KURALLAR + RISK_RULES + KDV_RULES + ORAN_RULES + EFATURA_RULES + GECICI_RULES

# Büro içi ek incelemeler: kayıt tutulur ve risk sayfasında görünür, ama açık bulgu sayısına, uyarı rozetine ve
# mükellefe verilen raporlara girmez (küçük işletmelerde çoğunlukla yanlış alarm verir).
ARKA_PLAN = frozenset({"K-12", "K-33"})
