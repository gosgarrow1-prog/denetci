"""Çalıştırılan tüm kuralların listesi."""

from .kdv import KDV_RULES
from .rules import ALL_RULES as TEMEL_KURALLAR

ALL_RULES = TEMEL_KURALLAR + KDV_RULES
