import json
import os
import copy
import fcntl
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

DATA_DIR = os.getenv("DATA_DIR", "data/users").strip() or "data/users"
HISTORY_LIMIT = int(os.getenv("HISTORY_LIMIT", "16"))

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

def _user_path(user_id: str, folder: str = DATA_DIR) -> Path:
    return Path(folder) / f"{user_id}.json"

def _lock_path(user_id: str, folder: str = DATA_DIR) -> Path:
    return Path(folder) / f"{user_id}.lock"

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

def _trim_history_inplace(user_data: Dict[str, Any]) -> None:
    """Обрезает историю до HISTORY_LIMIT (если >0)."""
    if not HISTORY_LIMIT or HISTORY_LIMIT <= 0:
        return
    hist = user_data.get("history")
    if isinstance(hist, list) and len(hist) > HISTORY_LIMIT:
        user_data["history"] = hist[-HISTORY_LIMIT:]

class _FileLock:
    """Простой файловый lock через flock."""
    def __init__(self, lockfile: Path):
        self.lockfile = lockfile
        self._fd = None

    def __enter__(self):
        self.lockfile.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(self.lockfile, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(self._fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self._fd is not None:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
        finally:
            self._fd = None

def load_user_data(user_id: str, folder: str = DATA_DIR) -> Dict[str, Any]:
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

def save_user_data(user_id: str, data: Dict[str, Any], folder: str = DATA_DIR) -> None:
    """
    Атомарная запись профиля:
    - гарантирует структуру,
    - подрезает историю до HISTORY_LIMIT,
    - пишет во временный файл и делает os.replace,
    - защищает запись flock-ом.
    """
    Path(folder).mkdir(parents=True, exist_ok=True)
    normalized = _ensure_structure(data)
    _trim_history_inplace(normalized)

    path = _user_path(user_id, folder)
    tmp_path = path.with_suffix(".json.tmp")
    lock_path = _lock_path(user_id, folder)

    with _FileLock(lock_path):
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

def get_user_name(user_id: str, folder: str = DATA_DIR) -> Optional[str]:
    return load_user_data(user_id, folder)["physical_data"].get("name")

def set_user_name(user_id: str, name: Optional[str], folder: str = DATA_DIR) -> Dict[str, Any]:
    data = load_user_data(user_id, folder)
    if isinstance(name, str):
        name = name.strip()[:80] or None
    data["physical_data"]["name"] = name
    save_user_data(user_id, data, folder)
    return data

def append_history(user_id: str, pair: Tuple[str, str], folder: str = DATA_DIR) -> Dict[str, Any]:
    """
    Добавляет запись (user_text, assistant_text) в историю с обрезкой по лимиту.
    pair: ("🧍 ...", "🤖 ...")
    """
    data = load_user_data(user_id, folder)
    hist = data.get("history")
    if not isinstance(hist, list):
        hist = []
    hist.append(pair)
    data["history"] = hist
    save_user_data(user_id, data, folder)
    return data

def reset_user(user_id: str, preserve_name: bool = True, folder: str = DATA_DIR) -> Dict[str, Any]:
    """
    Сброс профиля: по умолчанию оставляем имя, остальное — заново.
    """
    old = load_user_data(user_id, folder)
    name = old.get("physical_data", {}).get("name") if preserve_name else None
    fresh = copy.deepcopy(DEFAULT_USER_DATA)
    fresh["physical_data"]["name"] = (name.strip()[:80] if isinstance(name, str) and name.strip() else None)
    save_user_data(user_id, fresh, folder)
    return fresh

def update_physical_field(user_id: str, field: str, value: Any, folder: str = DATA_DIR) -> Dict[str, Any]:
    """
    Точечное обновление одного поля анкеты (age/weight/schedule/…)
    """
    data = load_user_data(user_id, folder)
    if field in DEFAULT_USER_DATA["physical_data"]:
        data["physical_data"][field] = value
        save_user_data(user_id, data, folder)
    return data

def set_physical_data(user_id: str, updates: Dict[str, Any], folder: str = DATA_DIR) -> Dict[str, Any]:
    """
    Массовое обновление анкеты (merge).
    """
    data = load_user_data(user_id, folder)
    base = data.get("physical_data") or {}
    for k, v in (updates or {}).items():
        if k in DEFAULT_USER_DATA["physical_data"]:
            base[k] = v
    data["physical_data"] = base
    save_user_data(user_id, data, folder)
    return data

def mark_completed(user_id: str, completed: bool = True, folder: str = DATA_DIR) -> Dict[str, Any]:
    data = load_user_data(user_id, folder)
    data["physical_data_completed"] = bool(completed)
    save_user_data(user_id, data, folder)
    return data

def get_physical_data(user_id: str, folder: str = DATA_DIR) -> Dict[str, Any]:
    return load_user_data(user_id, folder)["physical_data"]

def is_completed(user_id: str, folder: str = DATA_DIR) -> bool:
    return bool(load_user_data(user_id, folder).get("physical_data_completed"))
