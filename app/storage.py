# app/storage.py
import json
import os
import copy
import time
from pathlib import Path
from typing import Dict, Any, Optional

# -------------------- Структура по умолчанию --------------------

DEFAULT_USER_DATA: Dict[str, Any] = {
    "history": [],  # список пар (user_msg, bot_msg) для справки
    "physical_data": {
        "name": None,          # Имя пользователя
        "gender": None,        # "мужской"/"женский"
        "age": None,           # число или строка с числом
        "height": None,        # см
        "weight": None,        # кг
        "goal": None,          # желаемый вес (если указывают)
        "restrictions": None,  # ограничения/предпочтения
        "level": None,         # "начинающий"/"опытный"
        "schedule": None,      # сколько раз/неделю
        "target": None,        # "похудение"/"набор массы"/"поддержание формы"
    },
    "lifts": {},               # на будущее (история упражнений)
    "last_reply": None,        # последний текст (любого ответа)
    "last_program": None,      # последняя СГЕНЕРИРОВАННАЯ ПРОГРАММА
    "physical_data_completed": False,
    "programs": [],            # опционально, если копите версии
}

# -------------------- Вспомогательное --------------------

def _user_path(user_id: str, folder: str) -> Path:
    return Path(folder) / f"{user_id}.json"


def _ensure_structure(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Нормализуем входной объект до актуальной схемы.
    Мягко переносим устаревшие поля (schedule/level/target) из корня в physical_data.
    Неизвестные поля игнорируем.
    """
    result = copy.deepcopy(DEFAULT_USER_DATA)

    if not isinstance(data, dict):
        return result

    # history
    if isinstance(data.get("history"), list):
        result["history"] = data["history"]

    # physical_data
    if isinstance(data.get("physical_data"), dict):
        pd_in = data["physical_data"]
        for k in result["physical_data"].keys():
            if k in pd_in:
                result["physical_data"][k] = pd_in[k]

    # миграция legacy-полей (если вдруг лежали в корне)
    for legacy_key in ("schedule", "level", "target"):
        if legacy_key in data and result["physical_data"].get(legacy_key) is None:
            result["physical_data"][legacy_key] = data.get(legacy_key)

    # флаги/строки
    if isinstance(data.get("physical_data_completed"), bool):
        result["physical_data_completed"] = data["physical_data_completed"]

    # last_reply, last_program
    if data.get("last_reply") is None or isinstance(data.get("last_reply"), str):
        result["last_reply"] = data.get("last_reply")
    if data.get("last_program") is None or isinstance(data.get("last_program"), str):
        result["last_program"] = data.get("last_program")

    # lifts (если был)
    if isinstance(data.get("lifts"), dict):
        result["lifts"] = data["lifts"]

    # сохранённые программы (если есть)
    if isinstance(data.get("programs"), list):
        result["programs"] = data["programs"]

    return result


# -------------------- IO --------------------

def load_user_data(user_id: str, folder: str = "data/users") -> Dict[str, Any]:
    """
    Безопасно читаем JSON. При ошибке парсинга/отсутствии файла — возвращаем дефолт.
    """
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
    """
    Атомарная запись через временный файл: *.tmp → os.replace.
    Параллельно нормализуем структуру.
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
        # на всякий случай почистим tmp, если что-то пошло не так
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


# -------------------- Удобные геттеры/сеттеры --------------------

def get_user_name(user_id: str, folder: str = "data/users") -> Optional[str]:
    d = load_user_data(user_id, folder)
    return (d.get("physical_data") or {}).get("name")


def set_user_name(user_id: str, name: Optional[str], folder: str = "data/users") -> Dict[str, Any]:
    d = load_user_data(user_id, folder)
    if isinstance(name, str):
        name = (name or "").strip()[:80] or None
    d.setdefault("physical_data", {}).update({"name": name})
    save_user_data(user_id, d, folder)
    return d


def set_last_reply(user_id: str, text: Optional[str], folder: str = "data/users") -> Optional[str]:
    d = load_user_data(user_id, folder)
    d["last_reply"] = text
    save_user_data(user_id, d, folder)
    return text


def get_last_reply(user_id: str, folder: str = "data/users") -> Optional[str]:
    d = load_user_data(user_id, folder)
    return d.get("last_reply")


def set_last_program(user_id: str, text: Optional[str], folder: str = "data/users") -> Optional[str]:
    """
    Храним последнюю сгенерированную ПРОГРАММУ отдельно от last_reply,
    чтобы кнопка «Сохранить в файл» работала предсказуемо.
    """
    d = load_user_data(user_id, folder)
    d["last_program"] = text
    save_user_data(user_id, d, folder)
    return text


def get_last_program(user_id: str, folder: str = "data/users") -> Optional[str]:
    d = load_user_data(user_id, folder)
    return d.get("last_program")


# --------- История упражнений (оставляем на будущее, используется частично) ---------

def get_lift_history(user_id: str, lift_key: str, folder: str = "data/users"):
    d = load_user_data(user_id, folder)
    return (d.get("lifts") or {}).get(lift_key)


def save_lift_history(
    user_id: str,
    lift_key: str,
    last_weight: float,
    reps: int,
    rir: Optional[int] = None,
    folder: str = "data/users",
):
    """
    Универсальный накопитель истории по упражнению.
    Сейчас в проекте почти не используется, но оставляем для совместимости/расширений.
    """
    d = load_user_data(user_id, folder)

    entry = {
        "ts": int(time.time()),
        "last_weight": float(last_weight),
        "reps": int(reps),
        "rir": None if rir is None else int(rir),
    }

    lifts = d.setdefault("lifts", {})
    rec = lifts.get(lift_key) or {}

    rec["last_weight"] = entry["last_weight"]
    rec["reps"] = entry["reps"]
    rec["rir"] = entry["rir"]

    hist = rec.get("history") or []
    hist.append(entry)
    rec["history"] = hist[-50:]  # ограничим хвост избыточной истории

    lifts[lift_key] = rec
    d["lifts"] = lifts

    save_user_data(user_id, d, folder)
    return d["lifts"][lift_key]
