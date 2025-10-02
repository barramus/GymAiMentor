# app/weights.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

"""
Лёгкий совместимый модуль.

Задача:
- Сохранить имена, которые могут импортироваться из старого кода
  (User, History, base_key, recommend_start_weight),
- но НЕ выполнять никаких расчётов рабочих весов — рекомендации теперь
  полностью приходят от ИИ в тексте программы.

Если где-то в проекте встретится вызов recommend_start_weight, он вернёт None.
"""

# Небольшой алиас-словарик оставим на будущее (если пригодится для парсинга)
ALIAS = {
    "присед": "squat", "приседания": "squat",
    "станов": "deadlift",
    "жим штанги лёжа": "bench", "жим лёжа": "bench",
    "жим стоя": "ohp", "гантелей стоя": "ohp",
    "тяга штанги": "row", "в наклон": "row",
    "верхн": "lat_pulldown", "широким хватом": "lat_pulldown",
    "сгибани": "leg_curl",
    "жим ногами": "leg_press",
}

@dataclass
class User:
    gender: Optional[str] = None
    age: Optional[int] = None
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    level: Optional[str] = None
    target: Optional[str] = None

@dataclass
class History:
    last_weight: Optional[float] = None
    reps: Optional[int] = None
    rir: Optional[int] = None

def base_key(ex_name: str) -> Optional[str]:
    """Возвращает простой ключ упражнения по подстроке (если нужно)."""
    n = (ex_name or "").lower()
    for k, v in ALIAS.items():
        if k in n:
            return v
    return None

def recommend_start_weight(*args, **kwargs):
    """
    Совместимый заглушечный метод: теперь НЕ считаем веса кодом.
    Возвращаем None, чтобы вызывающая сторона могла понять,
    что вычислений здесь больше нет.
    """
    return None
