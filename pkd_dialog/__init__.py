"""Диалоговый подбор PKD: типы контракта, признаки, вопросы.

Пакет назван `pkd_dialog`, а не `pkd`, по прозаической причине: рядом лежит
модуль `pkd.py` с работающим поиском, и одноимённый пакет Python просто
не даст импортировать оба. Когда старый поиск переедет внутрь пакета,
эти модули станут его частью — до тех пор границы разные, и это честнее,
чем ломать работающую ручку ради красоты дерева каталогов.
"""
from .models import (Answer, DialogState, Feature, FeatureSet, FeatureSource,
                     PrimaryStatus, Question, QuestionOption, Status, fingerprint)

__all__ = ["Answer", "DialogState", "Feature", "FeatureSet", "FeatureSource",
           "PrimaryStatus", "Question", "QuestionOption", "Status", "fingerprint"]
