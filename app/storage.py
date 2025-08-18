import json
import os
import copy
from pathlib import Path
from typing import Dict, Any, Optional

DEFAULT_USER_DATA: Dict[str, Any] = {
    "history": [],
    "physical_data": {
        "name": None,
        "gender": None,
        "age": None,
        "height": None,
        "weight": None,
        "goal": None,
        "restrictions": None,
        "schedule": None,
        "level": None,
        "target": None,
    },
    "physical_data_completed": False,
}

def _user_path(user_id: str, folder: str) -> Path:
    return Path(folder) / f"{user_id}.json"

def _ensure_structure(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Приводит произвольный словарь к ожидаемой структуре DEFAULT_USER_DATA.
    Безопасно для старых файлов (добавляет отсутствующие ключи).
    """
    result = copy.deepcopy(DEFAULT_USER_DATA)

    if not isinstance(data, dict):
        return result

    if isinstance(data.get("history"), list):
        result["history"] = data["history"]

    if isinstance(data.get("physical_data"), dict):
        for k in result["physical_data"].keys():
            if k in data["physical_data"]:
                result["physical_data"][k] = data["physical_data"][k]

    if isinstance(data.get("physical_data_completed"), bool):
        result["physical_data_completed"] = data["physical_data_completed"]

    return result

def load_user_data(user_id: str, folder: str = "data/users") -> Dict[str, Any]:
    """
    Загружает профиль пользователя (с миграцией структуры).
    Если файла нет или он повреждён — вернёт дефолтную структуру.
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
    Атомарная запись профиля:
    - гарантирует структуру,
    - пишет во временный файл и делает os.replace.
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

def get_user_name(user_id: str, folder: str = "data/users") -> Optional[str]:
    data = load_user_data(user_id, folder)
    return data["physical_data"].get("name")

def set_user_name(user_id: str, name: Optional[str], folder: str = "data/users") -> Dict[str, Any]:
    data = load_user_data(user_id, folder)
    if isinstance(name, str):
        name = name.strip()[:80] or None
    data["physical_data"]["name"] = name
    save_user_data(user_id, data, folder)
    return data
