"""Çalıştırılan tüm kuralların listesi."""

from .gecici_rules import GECICI_RULES
from .kdv import KDV_RULES
from .rules import ALL_RULES as TEMEL_KURALLAR

ALL_RULES = TEMEL_KURALLAR + KDV_RULES + GECICI_RULES
