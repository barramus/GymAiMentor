# app/storage.py
from __future__ import annotations

import json
import os
import copy
import time
from pathlib import Path
from typing import Dict, Any, Optional

# ======= Схема по умолчанию =======
DEFAULT_USER_DATA: Dict[str, Any] = {
    "history": [],                         # [(user_msg, bot_msg), ...]
    "physical_data": {
        "name": None,
        "gender": None,
        "age": None,
        "height": None,
        "weight": None,
        "goal": None,                      # желаемый вес
        "restrictions": None,
        "level": None,
        "schedule": None,                  # кол-во тренировок/нед
        "target": None,                    # цель: похудение/масса/поддержание
    },
    "lifts": {},                           # история по упражнениям (для рекомендаций веса)
    "last_reply": None,                    # последний сгенерированный ответ
    "physical_data_completed": False,
    "last_program": "",
    "programs": [],
}

# ======= Вспомогательные =======
def _user_path(user_id: str, folder: str) -> Path:
    return Path(folder) / f"{user_id}.json"


def _coerce_scalar(v):
    """Обрезаем слишком длинные строки и приводим простые типы."""
    if isinstance(v, str):
        v = v.strip()
        if len(v) > 200:
            v = v[:200]
    return v


def _ensure_structure(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Нормализуем структуру и мягко мигрируем старые поля:
    раньше schedule/level/target/last_reply могли лежать в корне — переносим в physical_data.
    Также гарантируем наличие всех ключей и корректных типов.
    """
    result = copy.deepcopy(DEFAULT_USER_DATA)

    if not isinstance(data, dict):
        return result

    # history
    if isinstance(data.get("history"), list):
        result["history"] = list(data["history"])

    # physical_data
    if isinstance(data.get("physical_data"), dict):
        for k in result["physical_data"].keys():
            if k in data["physical_data"]:
                result["physical_data"][k] = _coerce_scalar(data["physical_data"][k])

    # миграция legacy-полей из корня в physical_data
    for legacy_key in ("schedule", "level", "target"):
        if legacy_key in data and result["physical_data"].get(legacy_key) is None:
            result["physical_data"][legacy_key] = _coerce_scalar(data.get(legacy_key))

    # флаги/служебные поля
    if isinstance(data.get("physical_data_completed"), bool):
        result["physical_data_completed"] = data["physical_data_completed"]

    if isinstance(data.get("last_program"), str):
        result["last_program"] = data["last_program"]

    if isinstance(data.get("programs"), list):
        result["programs"] = list(data["programs"])

    # lifts (оставляем для возможных рекомендаций веса)
    if isinstance(data.get("lifts"), dict):
        result["lifts"] = data["lifts"]

    # last_reply может быть и None
    if "last_reply" in data and (
        isinstance(data.get("last_reply"), str) or data.get("last_reply") is None
    ):
        result["last_reply"] = data.get("last_reply")

    return result


# ======= Базовые операции =======
def load_user_data(user_id: str, folder: str = "data/users") -> Dict[str, Any]:
    path = _user_path(user_id, folder)
    if not path.exists():
        return copy.deepcopy(DEFAULT_USER_DATA)
    try:
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        # если файл битый — возвращаем дефолтную схему
        return copy.deepcopy(DEFAULT_USER_DATA)
    return _ensure_structure(raw)


def save_user_data(user_id: str, data: Dict[str, Any], folder: str = "data/users") -> None:
    """
    Атомарная запись с временным файлом.
    """
    Path(folder).mkdir(parents=True, exist_ok=True)
    normalized = _ensure_structure(data)

    path = _user_path(user_id, folder)
    tmp_path = path.with_suffix(".json.tmp")

    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(normalized, f, ensure_ascii=False, indent=4)
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


# ======= Утилиты для имени пользователя =======
def get_user_name(user_id: str, folder: str = "data/users") -> Optional[str]:
    data = load_user_data(user_id, folder)
    return (data.get("physical_data") or {}).get("name")


def set_user_name(user_id: str, name: Optional[str], folder: str = "data/users") -> Dict[str, Any]:
    data = load_user_data(user_id, folder)
    if isinstance(name, str):
        name = name.strip()[:80] or None
    data["physical_data"]["name"] = name
    save_user_data(user_id, data, folder)
    return data


# ======= История подъёмов (оставляем для будущих рекомендаций веса) =======
def get_lift_history(user_id: str, lift_key: str, folder: str = "data/users"):
    data = load_user_data(user_id, folder)
    return (data.get("lifts") or {}).get(lift_key)


def save_lift_history(
    user_id: str,
    lift_key: str,
    last_weight: float,
    reps: int,
    rir: int | None = None,
    folder: str = "data/users",
):
    data = load_user_data(user_id, folder)
    entry = {
        "ts": int(time.time()),
        "last_weight": float(last_weight),
        "reps": int(reps),
        "rir": None if rir is None else int(rir),
    }
    lifts = data.setdefault("lifts", {})
    rec = dict(lifts.get(lift_key) or {})
    rec["last_weight"] = entry["last_weight"]
    rec["reps"] = entry["reps"]
    rec["rir"] = entry["rir"]
    hist = list(rec.get("history") or [])
    hist.append(entry)
    rec["history"] = hist[-50:]  # храним только последние 50 записей
    lifts[lift_key] = rec
    data["lifts"] = lifts
    save_user_data(user_id, data, folder)
    return data["lifts"][lift_key]


# ======= Последний ответ (для кнопки «Сохранить в файл») =======
def set_last_reply(user_id: str, text: str, folder: str = "data/users"):
    data = load_user_data(user_id, folder)
    data["last_reply"] = text
    save_user_data(user_id, data, folder)
    return text


def get_last_reply(user_id: str, folder: str = "data/users"):
    data = load_user_data(user_id, folder)
    return data.get("last_reply")
