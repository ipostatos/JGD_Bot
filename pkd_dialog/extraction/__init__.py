"""Извлечение признаков из текста. Правила лежат отдельно от правил выбора
кодов намеренно: «слово -> признак» и «признак -> код» меняются в разном
темпе и разными людьми."""
from .rule_based import (Confidence, EvidenceSpan, ExtractionResult, FeatureConflict,
                         FeatureValue, RuleBasedFeatureExtractor, load_rules, tokenize)

__all__ = ["Confidence", "EvidenceSpan", "ExtractionResult", "FeatureConflict",
           "FeatureValue", "RuleBasedFeatureExtractor", "load_rules", "tokenize"]
