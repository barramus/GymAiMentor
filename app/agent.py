from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole
from app.storage import load_user_data, save_user_data

import os
import time
from typing import Optional

GIGACHAT_MODEL: str = os.getenv("GIGACHAT_MODEL", "GigaChat-2-Max").strip()

GIGACHAT_TEMPERATURE: float = float(os.getenv("GIGACHAT_TEMPERATURE", "0.2"))

GIGACHAT_MAX_TOKENS: int = int(os.getenv("GIGACHAT_MAX_TOKENS", "2000"))

GIGACHAT_TIMEOUT: int = int(os.getenv("GIGACHAT_TIMEOUT", "60"))

GIGACHAT_RETRIES: int = int(os.getenv("GIGACHAT_RETRIES", "3"))

class FitnessAgent:
    def __init__(self, token: str, user_id: str):
        self.token = token
        self.user_id = user_id
        self.user_data = load_user_data(user_id)

        physical_data = self.user_data.get("physical_data", {}) or {}
        self._user_name: Optional[str] = (physical_data.get("name") or "").strip() or None

        physical_prompt = self._format_physical_data(physical_data)

        self.payload = Chat(
            messages=[
                Messages(
                    role=MessagesRole.SYSTEM,
                    content=(
                        "Представь, что ты — персональный фитнес-тренер с опытом активных тренировок 8+ лет. "
                        "Ты опытный атлет, используешь современные, эффективные методики. "
                        "Общайся с человеком на 'ты'. "
                        "На основе предоставленных данных составь индивидуальную программу тренировок "
                        "на то количество дней, которое указано в данных. Без уточняющих вопросов. "
                        "Обязательно учитывай цель, физические параметры, ограничения и то, сколько раз в неделю "
                        "человек может посещать тренажёрный зал."
                    ),
                ),
                Messages(role=MessagesRole.USER, content=physical_prompt),
            ],
            temperature=GIGACHAT_TEMPERATURE,
            max_tokens=GIGACHAT_MAX_TOKENS,
            model=GIGACHAT_MODEL,
        )

    def _format_physical_data(self, data: dict) -> str:
        return (
            f"Цель: {data.get('target', 'не указана')}\n"
            f"Пол: {data.get('gender', 'не указано')}\n"
            f"Возраст: {data.get('age', 'не указано')} лет\n"
            f"Рост: {data.get('height', 'не указано')} см\n"
            f"Текущий вес: {data.get('weight', 'не указано')} кг\n"
            f"Желаемый вес: {data.get('goal', 'не указано')} кг\n"
            f"Ограничения: {data.get('restrictions', 'нет')}\n"
            f"Частота тренировок: {data.get('schedule', 'не указано')}\n"
            f"Уровень подготовки: {data.get('level', 'не указано')}"
        )

    def _with_name_prefix(self, text: str) -> str:
        """Добавляет обращение по имени только в пользовательский ответ агента."""
        name = self._user_name
        if not name:
            return text
        if text and text[0].isupper():
            return f"{name}, {text[0].lower() + text[1:]}"
        return f"{name}, {text}"

    async def get_response(self, user_input: str) -> str:
        from asyncio import to_thread

        if user_input and user_input.strip():
            self.payload.messages.append(Messages(role=MessagesRole.USER, content=user_input))

        def _chat_sync():
            """
            Синхронный вызов GigaChat c ретраями.
            Совместим с разными версиями SDK: пробуем задать модель в конструкторе клиента;
            если сигнатура не поддерживает — передаём model в giga.chat(...).
            """
            last_err = None
            for attempt in range(1, GIGACHAT_RETRIES + 1):
                try:
                    try:
                        with GigaChat(
                            credentials=self.token,
                            verify_ssl_certs=False,
                            timeout=GIGACHAT_TIMEOUT,
                            model=GIGACHAT_MODEL,
                        ) as giga:
                            response = giga.chat(self.payload)
                            return response.choices[0].message
                    except TypeError:
                        with GigaChat(
                            credentials=self.token,
                            verify_ssl_certs=False,
                            timeout=GIGACHAT_TIMEOUT,
                        ) as giga:
                            response = getattr(giga, "chat")(self.payload, model=GIGACHAT_MODEL)
                            return response.choices[0].message
                except Exception as e:
                    last_err = e
                    if attempt == GIGACHAT_RETRIES:
                        raise
                    time.sleep(2 * attempt)
            raise last_err or RuntimeError("GigaChat call failed")

        message = await to_thread(_chat_sync)

        self.payload.messages.append(message)
        personalized = self._with_name_prefix(message.content)

        history = self.user_data.get("history", [])
        if user_input and user_input.strip():
            history.append(("🧍 " + user_input, "🤖 " + personalized))
        else:
            history.append(("🧍 Запрос программы", "🤖 " + personalized))
        self.user_data["history"] = history
        save_user_data(self.user_id, self.user_data)

        return personalized
