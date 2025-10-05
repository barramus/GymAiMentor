import json
import os
import copy
import time
from pathlib import Path
from typing import Dict, Any, Optional, Tuple


def validate_age(text: str) -> Tuple[bool, Optional[int], str]:
    """Проверяет корректность возраста. Возвращает (успех, значение, сообщение об ошибке)."""
    try:
        age = int(text.strip())
        if 10 <= age <= 100:
            return True, age, ""
        return False, None, "Возраст должен быть от 10 до 100 лет."
    except (ValueError, AttributeError):
        return False, None, "Пожалуйста, введи число (например: 25)."


def validate_height(text: str) -> Tuple[bool, Optional[int], str]:
    """Проверяет корректность роста в см."""
    try:
        height = int(text.strip())
        if 100 <= height <= 250:
            return True, height, ""
        return False, None, "Рост должен быть от 100 до 250 см."
    except (ValueError, AttributeError):
        return False, None, "Пожалуйста, введи число в сантиметрах (например: 175)."


def validate_weight(text: str) -> Tuple[bool, Optional[float], str]:
    """Проверяет корректность веса в кг."""
    try:
        weight = float(text.strip().replace(',', '.'))
        if 30 <= weight <= 300:
            return True, round(weight, 1), ""
        return False, None, "Вес должен быть от 30 до 300 кг."
    except (ValueError, AttributeError):
        return False, None, "Пожалуйста, введи число в килограммах (например: 70 или 70.5)."


def validate_schedule(text: str) -> Tuple[bool, Optional[int], str]:
    """Проверяет корректность частоты тренировок в неделю."""
    try:
        schedule = int(text.strip())
        if 1 <= schedule <= 7:
            return True, schedule, ""
        return False, None, "Частота тренировок должна быть от 1 до 7 раз в неделю."
    except (ValueError, AttributeError):
        return False, None, "Пожалуйста, введи число (например: 3)."



DEFAULT_USER_DATA: Dict[str, Any] = {
    "history": [],
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
        "preferred_muscle_group": None,  # предпочитаемая группа мышц для акцента
    },
    "lifts": {},               # на будущее (история упражнений)
    "last_reply": None,        # последний текст (любого ответа)
    "last_program": None,      # последняя СГЕНЕРИРОВАННАЯ ПРОГРАММА
    "physical_data_completed": False,
    "programs": [],            # опционально, если копите версии
}


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


def set_user_goal(user_id: str, goal: str, folder: str = "data/users") -> Dict[str, Any]:
    """
    Устанавливает новую цель тренировок для пользователя.
    Добавляет запись в историю об изменении цели.
    """
    d = load_user_data(user_id, folder)
    old_goal = (d.get("physical_data") or {}).get("target")
    
    # Обновляем цель
    d.setdefault("physical_data", {}).update({"target": goal})
    
    # Добавляем в историю
    if old_goal and old_goal != goal:
        hist = d.get("history", [])
        hist.append((
            f"🎯 Изменение цели с '{old_goal}' на '{goal}'",
            f"✅ Цель успешно изменена. Новая цель: {goal}"
        ))
        d["history"] = hist
    
    save_user_data(user_id, d, folder)
    return d


def update_user_param(user_id: str, param_name: str, value: Any, folder: str = "data/users") -> Dict[str, Any]:
    """
    Обновляет отдельный параметр в анкете пользователя.
    param_name: 'weight', 'schedule', 'restrictions', 'level', 'age', 'height', 'goal'
    """
    d = load_user_data(user_id, folder)
    old_value = (d.get("physical_data") or {}).get(param_name)
    
    # Обновляем параметр
    d.setdefault("physical_data", {}).update({param_name: value})
    
    # Добавляем в историю
    if old_value != value:
        param_labels = {
            'name': '👤 имя',
            'age': '🔢 возраст',
            'weight': '⚖️ текущий вес',
            'goal': '🎯 желаемый вес',
            'schedule': '📈 частоту тренировок',
            'restrictions': '⚠️ ограничения',
            'level': '🏋️ уровень подготовки',
            'height': '📏 рост',
            'preferred_muscle_group': '💪 акцент на мышцы'
        }
        label = param_labels.get(param_name, param_name)
        hist = d.get("history", [])
        hist.append((
            f"✏️ Изменение: {label}",
            f"Новое значение: {value}" + (f" (было: {old_value})" if old_value else "")
        ))
        d["history"] = hist
    
    save_user_data(user_id, d, folder)
    return d


def get_user_profile_text(user_id: str, folder: str = "data/users") -> str:
    """
    Возвращает форматированный текст анкеты пользователя.
    """
    d = load_user_data(user_id, folder)
    phys = d.get("physical_data") or {}
    
    # Иконки для целей
    goal_icons = {
        "похудение": "🏃‍♂️",
        "набор массы": "🏋️‍♂️",
        "поддержание формы": "🧘"
    }
    
    target = phys.get('target') or 'не указана'
    target_icon = goal_icons.get(target, "🎯")
    
    # Форматируем акцент на мышечную группу
    muscle_group_display = {
        "ноги": "🦵 Ноги",
        "ягодицы": "🍑 Ягодицы",
        "спина": "🔙 Спина",
        "плечи и руки": "💪 Плечи и руки",
        "сбалансированно": "🎲 Сбалансированно"
    }
    
    preferred = phys.get('preferred_muscle_group')
    muscle_group_text = muscle_group_display.get(preferred, preferred or 'не указано')
    
    text = f"""📋 **Твоя анкета:**

👤 Имя: {phys.get('name') or 'не указано'}
{target_icon} Цель: {target}
⚧ Пол: {phys.get('gender') or 'не указано'}
🔢 Возраст: {phys.get('age') or 'не указано'} лет
📏 Рост: {phys.get('height') or 'не указано'} см
⚖️ Текущий вес: {phys.get('weight') or 'не указано'} кг
🎯 Желаемый вес: {phys.get('goal') or 'не указано'} кг
🏋️ Уровень: {phys.get('level') or 'не указано'}
📈 Частота: {phys.get('schedule') or 'не указано'} раз/неделю
💪 Акцент на мышцы: {muscle_group_text}
⚠️ Ограничения: {phys.get('restrictions') or 'нет'}"""
    
    return text



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
