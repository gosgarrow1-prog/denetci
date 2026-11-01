"""Çalıştırılan tüm kuralların listesi."""

from .gecici_rules import GECICI_RULES
from .kdv import KDV_RULES
from .kdv_oran import ORAN_RULES
from .efatura_rule import EFATURA_RULES
from .rules import ALL_RULES as TEMEL_KURALLAR

ALL_RULES = TEMEL_KURALLAR + KDV_RULES + ORAN_RULES + EFATURA_RULES + GECICI_RULES
