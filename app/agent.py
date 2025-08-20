from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole
from app.storage import load_user_data, save_user_data

import os
import time
import re
from typing import Optional

GIGACHAT_MODEL: str = os.getenv("GIGACHAT_MODEL", "GigaChat-2-Max").strip()
GIGACHAT_TEMPERATURE: float = float(os.getenv("GIGACHAT_TEMPERATURE", "0.2"))
GIGACHAT_MAX_TOKENS: int = int(os.getenv("GIGACHAT_MAX_TOKENS", "2000"))
GIGACHAT_TIMEOUT: int = int(os.getenv("GIGACHAT_TIMEOUT", "60"))
GIGACHAT_RETRIES: int = int(os.getenv("GIGACHAT_RETRIES", "3"))

MAX_HISTORY_MESSAGES = 12

SYSTEM_PROMPT = (
    "Ты — персональный фитнес-тренер с 8+ годами активных тренировок по бодибилдингу и практики с клиентами. Общайся на «ты». "
    "На основе пользовательских данных составь индивидуальную программу БЕЗ уточняющих вопросов. "
    "Строго соблюдай формат ответа ниже и пиши кратко.\n\n"
    "ФОРМАТ ОТВЕТА:\n"
    "1) Заголовок: «Программа на N дней для <цель>».\n"
    "2) Краткая вводная (1–2 предложения): уровень, ключевые акценты.\n"
    "3) По дням:\n"
    "   — Заголовок дня: «День X — <группа мышц/тип>»\n"
    "   — Список упражнений (каждое на новой строке):\n"
    "     • <упражнение>: <подходы>x<повторы> (RPE=<число>, отдых=<сек>), техника: <кратко>\n"
    "   — Завершение дня: «Заминка: <5–10 мин>»\n"
    "4) Прогрессия на неделю: как увеличивать нагрузку (вес/повторы/подходы).\n"
    "5) Если есть ограничения — отдельный блок «Альтернативы».\n"
    "6) Питание (очень кратко): белок/КБЖУ/вода, 3–4 пункта.\n"
    "7) Без ссылок, без таблиц, только текст. Без префиксов «Вот», «Ниже» и т.п.\n"
    "8) Язык ответа: русский.\n"
)


class FitnessAgent:
    def __init__(self, token: str, user_id: str):
        if not token:
            raise RuntimeError("GIGACHAT_TOKEN пуст — проверь .env / unit-файл.")
        self.token = token
        self.user_id = user_id
        self.user_data = load_user_data(user_id)

        physical_data = self.user_data.get("physical_data", {}) or {}
        self._user_name: Optional[str] = (physical_data.get("name") or "").strip() or None

        physical_prompt = self._format_physical_data(physical_data)

        self.payload = Chat(
            messages=[
                Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT),
                Messages(role=MessagesRole.USER, content=physical_prompt),
            ],
            temperature=GIGACHAT_TEMPERATURE,
            max_tokens=GIGACHAT_MAX_TOKENS,
            model=GIGACHAT_MODEL,
        )

    def _format_physical_data(self, data: dict) -> str:
        days_hint = ""
        sched = str(data.get("schedule", "")).strip().lower()
        if sched.isdigit():
            d = int(sched)
            if 2 <= d <= 6:
                days_hint = f"\nПредпочтительное количество тренировочных дней в неделе: {d}."
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
            f"{days_hint}"
        )

    def _with_name_prefix(self, text: str) -> str:
        """Обычное персональное обращение для обычных ответов (не плана)."""
        name = self._user_name
        if not name:
            return text
        if text and text[0].isupper():
            return f"{name}, {text[0].lower() + text[1:]}"
        return f"{name}, {text}"

    def _strip_greeting(self, text: str) -> str:
        """Срезает приветствия в начале ответа модели (Привет, Здравствуйте, Hello...)."""
        if not text:
            return text
        pattern = r'^\s*((привет(ствую)?|здравствуй(те)?|добрый\s+(день|вечер|утро)|хай|йо|hello|hi)[!,.\-\s]*){1,2}'
        return re.sub(pattern, '', text, flags=re.IGNORECASE)

    def _program_header(self, body: str) -> str:
        """Шапка для программы тренировок по требованию."""
        name = self._user_name
        prefix = (
            f"{name}, обработал твой запрос, и вот что получилось ⬇️"
            if name else
            "Обработал твой запрос, и вот что получилось ⬇️"
        )
        return f"{prefix}\n\n{body}"

    def _trim_history(self):
        """Оставляем SYSTEM + первую USER (анкета) + последние MAX_HISTORY_MESSAGES."""
        msgs = self.payload.messages
        if len(msgs) <= 2:
            return
        head = msgs[:2]
        tail = msgs[2:]
        if len(tail) > MAX_HISTORY_MESSAGES:
            tail = tail[-MAX_HISTORY_MESSAGES:]
        self.payload.messages = head + tail

    async def get_response(self, user_input: str) -> str:
        from asyncio import to_thread

        if user_input and user_input.strip():
            self.payload.messages.append(Messages(role=MessagesRole.USER, content=user_input))

        def _chat_sync():
            """
            Синхронный вызов GigaChat с ретраями.
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
                    time.sleep(2 * attempt)
            raise last_err or RuntimeError("GigaChat call failed")

        message = await to_thread(_chat_sync)

        self.payload.messages.append(message)
        self._trim_history()

        if user_input and user_input.strip():
            personalized = self._with_name_prefix(message.content)
        else:
            clean = self._strip_greeting(message.content)
            personalized = self._program_header(clean)

        history = self.user_data.get("history", [])
        if user_input and user_input.strip():
            history.append(("🧍 " + user_input, "🤖 " + personalized))
        else:
            history.append(("🧍 Запрос программы", "🤖 " + personalized))
        self.user_data["history"] = history
        save_user_data(self.user_id, self.user_data)

        return personalized
