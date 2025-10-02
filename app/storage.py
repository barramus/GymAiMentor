# app/storage.py

from __future__ import annotations

import json
import os
import copy
import time
from pathlib import Path
from typing import Dict, Any, Optional

# Базовая структура пользователя
DEFAULT_USER_DATA: Dict[str, Any] = {
    "history": [],
    "physical_data": {
        "name": None,
        "gender": None,
        "age": None,
        "height": None,
        "weight": None,
        "goal": None,          # желаемый вес
        "restrictions": None,
        "level": None,
        "schedule": None,      # частота тренировок в нед.
        "target": None,        # цель: похудение/набор/поддержание
    },
    "lifts": {},               # резерв под будущие логи (сейчас не используем)
    "last_reply": None,        # последний текст, показанный пользователю
    "last_program": "",        # последняя сгенерированная программа (для сохранения в файл)
    "programs": [],            # история программ (по желанию)
    "physical_data_completed": False,
    "menu_enabled": False,     # показывать ли основную панель кнопок
}

# --------- Внутренние утилиты ---------

def _user_path(user_id: str, folder: str) -> Path:
    return Path(folder) / f"{user_id}.json"

def _ensure_structure(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Нормализуем структуру и мягко мигрируем старые поля:
    - ранее schedule/level/target могли лежать в корне — переносим в physical_data.
    - добавляем новые поля, если их не было.
    """
    result = copy.deepcopy(DEFAULT_USER_DATA)
    if not isinstance(data, dict):
        return result

    # history
    if isinstance(data.get("history"), list):
        result["history"] = data["history"]

    # physical_data
    if isinstance(data.get("physical_data"), dict):
        for k in result["physical_data"].keys():
            if k in data["physical_data"]:
                result["physical_data"][k] = data["physical_data"][k]

    # миграция старых ключей в корне
    for legacy_key in ("schedule", "level", "target"):
        if legacy_key in data and result["physical_data"].get(legacy_key) is None:
            result["physical_data"][legacy_key] = data.get(legacy_key)

    # флаги/служебные
    if isinstance(data.get("physical_data_completed"), bool):
        result["physical_data_completed"] = data["physical_data_completed"]

    if isinstance(data.get("menu_enabled"), bool):
        result["menu_enabled"] = data["menu_enabled"]

    # программы
    if isinstance(data.get("last_program"), str):
        result["last_program"] = data["last_program"]

    if isinstance(data.get("programs"), list):
        result["programs"] = data["programs"]

    # логи (на будущее)
    if isinstance(data.get("lifts"), dict):
        result["lifts"] = data["lifts"]

    # последний ответ
    if "last_reply" in data:
        result["last_reply"] = data.get("last_reply")

    return result

# --------- Публичный API ---------

def load_user_data(user_id: str, folder: str = "data/users") -> Dict[str, Any]:
    path = _user_path(user_id, folder)
    if not path.exists():
        return copy.deepcopy(DEFAULT_USER_DATA)
    try:
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return copy.deepcopy(DEFAULT_USER_DATA)
    return _ensure_structure(raw)

def save_user_data(user_id: str, data: Dict[str, Any], folder: str = "data/users") -> None:
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

# Последний текст пользователю (для совместимости)
def set_last_reply(user_id: str, text: str, folder: str = "data/users") -> str:
    data = load_user_data(user_id, folder)
    data["last_reply"] = text
    save_user_data(user_id, data, folder)
    return text

def get_last_reply(user_id: str, folder: str = "data/users") -> Optional[str]:
    data = load_user_data(user_id, folder)
    return data.get("last_reply")

# Последняя ПРОГРАММА (для сохранения в файл)
def set_last_program(user_id: str, text: str, folder: str = "data/users") -> str:
    data = load_user_data(user_id, folder)
    data["last_program"] = text or ""
    # по желанию — копим историю
    progs = data.get("programs") or []
    if text and (not progs or progs[-1] != text):
        progs.append(text)
        data["programs"] = progs[-10:]  # ограничим историю 10 последними
    save_user_data(user_id, data, folder)
    return text

def get_last_program(user_id: str, folder: str = "data/users") -> str:
    data = load_user_data(user_id, folder)
    return data.get("last_program") or ""
