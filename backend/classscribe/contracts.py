"""Frozen product-contract enumerations shared by core business code."""

from __future__ import annotations

from enum import StrEnum


class ProductSurface(StrEnum):
    CLASSROOM_WORKBENCH = "classroom_workbench"
    IBUS_DICTATION = "ibus_dictation"


class LanguageMode(StrEnum):
    CHINESE = "zh"
    JAPANESE = "ja"
    ENGLISH = "en"
    AUTO_MIXED = "auto_mixed"


class ModelSelectionMode(StrEnum):
    AUTO_BEST = "auto_best"
    MANUAL_PRIMARY = "manual_primary"
    STRICT_SINGLE_MODEL = "strict_single_model"


class TextLayer(StrEnum):
    FAITHFUL = "faithful"
    CORRECTED = "corrected"
    USER = "user"
