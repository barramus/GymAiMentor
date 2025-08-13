import json
import os

DEFAULT_USER_DATA = {
    "history": [],
    "physical_data": {},
    "physical_data_completed": False
}

def save_user_data(user_id: str, data: dict, folder="data/users"):
    os.makedirs(folder, exist_ok=True)

    # Гарантируем наличие ключей
    for key, default_value in DEFAULT_USER_DATA.items():
        data.setdefault(key, default_value)

    with open(f"{folder}/{user_id}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def load_user_data(user_id: str, folder="data/users") -> dict:
    path = f"{folder}/{user_id}.json"
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {}

    # Устанавливаем значения по умолчанию, если отсутствуют
    for key, default_value in DEFAULT_USER_DATA.items():
        data.setdefault(key, default_value)

    return data
