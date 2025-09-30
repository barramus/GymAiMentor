# app/weights.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, Tuple

# Коэффициенты стартового веса как доля BW (новичок, опытный)
COEFFS_BW = {
    "squat":        (0.55, 0.75),
    "deadlift":     (0.65, 0.90),
    "bench":        (0.40, 0.65),
    "ohp":          (0.25, 0.40),
    "row":          (0.35, 0.55),
    "lat_pulldown": (0.25, 0.40),
    "leg_curl":     (0.20, 0.30),
    "leg_press":    (0.90, 1.30),
}

# Примитивный словарь алиасов «фраза в названии → ключ»
ALIAS: Dict[str, str] = {
    "присед": "squat", "приседания": "squat", "фронтальные приседания": "squat",
    "станов": "deadlift",
    "жим штанги лёжа": "bench", "жим лёжа": "bench",
    "жим стоя": "ohp", "жим гантелей стоя": "ohp",
    "тяга штанги": "row", "в наклон": "row",
    "верхн": "lat_pulldown", "широким хватом": "lat_pulldown",
    "сгибани": "leg_curl",
    "жим ногами": "leg_press",
}

# Шаги округления
PLATE_STEP = 2.5    # штанга/блины
DB_STEP    = 1.0    # гантели
MACHINE_STEP = 2.5  # тренажёры/блоки

def round_to_step(x: float, step: float) -> float:
    if step <= 0:
        return float(x)
    return max(step, round(float(x) / step) * step)

def _level_idx(level: Optional[str]) -> int:
    if not level:
        return 0
    return 1 if str(level).lower().startswith(("опыт", "interm", "adv")) else 0

def _gender_corr(gender: Optional[str]) -> float:
    if not gender:
        return 1.0
    g = str(gender).lower()
    if g.startswith(("ж", "f", "w")):
        return 0.8
    return 1.0

def _age_corr(age: Optional[int]) -> float:
    if not age:
        return 1.0
    try:
        a = int(age)
    except Exception:
        return 1.0
    if a >= 60: return 0.90
    if a >= 40: return 0.95
    return 1.0

def _goal_corr(goal: Optional[str]) -> float:
    if not goal:
        return 1.0
    g = str(goal).lower()
    if "похуд" in g: return 0.95
    if "мас" in g:   return 1.05
    return 1.0

def estimate_1rm(weight: float, reps: int) -> float:
    return float(weight) * (1 + int(reps)/30.0)

def weight_for_reps_from_1rm(one_rm: float, target_reps: int) -> float:
    r = int(target_reps)
    if r >= 10:   pct = 0.675
    elif r >= 7:  pct = 0.75
    else:         pct = 0.85
    return float(one_rm) * pct

@dataclass
class User:
    gender: Optional[str]
    age: Optional[int]
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    level: Optional[str] = None
    target: Optional[str] = None

    # совместимость со старой сигнатурой
    def __init__(
        self,
        gender: Optional[str],
        age: Optional[int],
        height_cm: Optional[float] = None,
        weight_kg: Optional[float] = None,
        level: Optional[str] = None,
        target: Optional[str] = None,
        **kwargs
    ):
        self.gender = gender
        self.age = age
        self.height_cm = height_cm if height_cm is not None else kwargs.get("height")
        self.weight_kg = weight_kg if weight_kg is not None else kwargs.get("weight")
        self.level = level
        self.target = target if target is not None else kwargs.get("goal")

@dataclass
class History:
    last_weight: Optional[float] = None
    reps: Optional[int] = None
    rir: Optional[int] = None

def equipment_step(ex_name: str) -> float:
    name = ex_name.lower()
    if "гантел" in name: return DB_STEP
    if any(k in name for k in ["блок", "тренаж", "кросс"]): return MACHINE_STEP
    return PLATE_STEP

def base_key(ex_name: str) -> Optional[str]:
    n = ex_name.lower()
    for k, v in ALIAS.items():
        if k in n:
            return v
    return None

# ---------- Точный расчёт для конкретного упражнения ----------

def recommend_weight_for_exercise(
    exercise_name: str,
    user: User,
    target_reps: int = 10,
    history: Optional[History] = None,
) -> Tuple[float, str]:
    """
    Возвращает (рекомендованный_вес_кг, источник_оценки).
    Учитывает историю, BW, уровень, пол/возраст/цель и шаг снаряда.
    """
    step = equipment_step(exercise_name)

    # 1) История → 1RM → вес на заданные повторы
    if history and history.last_weight and history.reps:
        try:
            one_rm = estimate_1rm(history.last_weight, history.reps)
            w = weight_for_reps_from_1rm(one_rm, target_reps)
            return round_to_step(w, step), "по истории (1RM)"
        except Exception:
            pass

    # 2) Оценка от массы тела и уровня
    key = base_key(exercise_name)
    bw = float(user.weight_kg or 0)
    if key and bw:
        novice, experienced = COEFFS_BW.get(key, (0.3, 0.5))
        base_coef = experienced if _level_idx(user.level) else novice
        w = bw * base_coef
    else:
        # запасной вариант
        w = (bw or 60.0) * (0.25 if "гантел" in exercise_name.lower() else 0.4)

    w *= _gender_corr(user.gender)
    w *= _age_corr(user.age)
    w *= _goal_corr(user.target)

    return round_to_step(w, step), "старт по антропометрии"

# ---------- Обёртка совместимости (возвращает только число) ----------

def recommend_start_weight(
    user: User,
    history: Optional[History] = None,
    key: Optional[str] = None,
    target_reps: int = 10,
    exercise_name: Optional[str] = None,
) -> float:
    """
    Совместимо со старым вызовом: recommend_start_weight(user, history_for_key).
    Если известен exercise_name — используем точный расчёт, иначе делаем общую оценку.
    """
    if exercise_name:
        w, _ = recommend_weight_for_exercise(exercise_name, user, target_reps, history)
        return float(w)

    # по истории без знания шага снаряда
    if history and history.last_weight and history.reps:
        try:
            one_rm = estimate_1rm(history.last_weight, history.reps)
            w = weight_for_reps_from_1rm(one_rm, target_reps)
            return float(round_to_step(w, PLATE_STEP))
        except Exception:
            pass

    # от BW/ключа
    bw = float(user.weight_kg or 0.0)
    if key and key in COEFFS_BW and bw:
        novice, experienced = COEFFS_BW[key]
        base_coef = experienced if _level_idx(user.level) else novice
        w = bw * base_coef
    else:
        w = (bw or 60.0) * 0.4  # общий случай «под штангу»

    w *= _gender_corr(user.gender)
    w *= _age_corr(user.age)
    w *= _goal_corr(user.target)

    return float(round_to_step(w, PLATE_STEP))

__all__ = [
    "User",
    "History",
    "base_key",
    "recommend_weight_for_exercise",
    "recommend_start_weight",
]
