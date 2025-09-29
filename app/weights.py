
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, Tuple
import math

# Коэффициенты стартового веса как доля BW
COEFFS_BW = {
    "squat":       (0.55, 0.75),
    "deadlift":    (0.65, 0.90),
    "bench":       (0.40, 0.65),
    "ohp":         (0.25, 0.40),
    "row":         (0.35, 0.55),
    "lat_pulldown":(0.25, 0.40),
    "leg_curl":    (0.20, 0.30),
    "leg_press":   (0.90, 1.30),
}

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

PLATE_STEP = 2.5
DB_STEP = 1.0
MACHINE_STEP = 2.5

def round_to_step(x: float, step: float) -> float:
    if step <= 0: return x
    return max(step, round(x / step) * step)

def _level_idx(level: Optional[str]) -> int:
    if not level: return 0
    return 1 if str(level).lower().startswith(("опыт", "interm", "adv")) else 0

def _gender_corr(gender: Optional[str]) -> float:
    if not gender: return 1.0
    g = str(gender).lower()
    if g.startswith(("ж", "f", "w")): return 0.8
    return 1.0

def _age_corr(age: Optional[int]) -> float:
    if not age: return 1.0
    try:
        a = int(age)
    except Exception:
        return 1.0
    if a >= 60: return 0.90
    if a >= 40: return 0.95
    return 1.0

def _goal_corr(goal: Optional[str]) -> float:
    if not goal: return 1.0
    g = str(goal).lower()
    if "похуд" in g: return 0.95
    if "мас" in g: return 1.05
    return 1.0

def estimate_1rm(weight: float, reps: int) -> float:
    return float(weight) * (1 + int(reps)/30.0)

def weight_for_reps_from_1rm(one_rm: float, target_reps: int) -> float:
    r = int(target_reps)
    if r >= 10: pct = 0.675
    elif r >= 7: pct = 0.75
    else: pct = 0.85
    return float(one_rm) * pct

@dataclass
class User:
    gender: Optional[str]
    age: Optional[int]
    height: Optional[float]
    weight: Optional[float]
    goal: Optional[str]
    level: Optional[str]

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
        if k in n: return v
    return None

def recommend_start_weight(exercise_name: str, user: User, target_reps: int, history: Optional[History] = None):
    step = equipment_step(exercise_name)

    # 1) по истории
    if history and history.last_weight and history.reps:
        try:
            one_rm = estimate_1rm(history.last_weight, history.reps)
            w = weight_for_reps_from_1rm(one_rm, target_reps)
            return round_to_step(w, step), "по истории (1RM)"
        except Exception:
            pass

    # 2) от BW
    key = base_key(exercise_name)
    bw = float(user.weight or 0)
    if key and bw:
        novice, experienced = COEFFS_BW.get(key, (0.3, 0.5))
        base_coef = experienced if _level_idx(user.level) else novice
        w = bw * base_coef
    else:
        w = (bw or 60.0) * (0.25 if "гантел" in exercise_name.lower() else 0.4)

    w *= _gender_corr(user.gender)
    w *= _age_corr(user.age)
    w *= _goal_corr(user.goal)

    return round_to_step(w, step), "старт по антропометрии"
