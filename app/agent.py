import os
import re
import time
from typing import Optional

from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole
from app.storage import load_user_data, save_user_data

GIGACHAT_MODEL: str = os.getenv("GIGACHAT_MODEL", "GigaChat-2-Max").strip()
GIGACHAT_TEMPERATURE: float = float(os.getenv("GIGACHAT_TEMPERATURE", "0.2"))
GIGACHAT_MAX_TOKENS: int = int(os.getenv("GIGACHAT_MAX_TOKENS", "2000"))
GIGACHAT_TIMEOUT: int = int(os.getenv("GIGACHAT_TIMEOUT", "60"))
GIGACHAT_RETRIES: int = int(os.getenv("GIGACHAT_RETRIES", "3"))

_RPE_CHUNK = re.compile(r'[,;\s]*RPE\s*=?\s*[\d.,\-–—]+', re.IGNORECASE)
_RIR_CHUNK = re.compile(r'[,;\s]*RIR\s*=?\s*[\d.,\-–—]+', re.IGNORECASE)
_EMPTY_PARENS = re.compile(r'\(\s*\)')
_FIX_COMMAs = re.compile(r'\s*,\s*,')  # двойные запятые

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
                        "Представь, что ты — персональный фитнес-тренер с опытом 8+ лет. "
                        "Общайся на 'ты'. На основе данных пользователя составь подробный план по дням: "
                        "заголовок дня, список упражнений в формате 'Название — подходы×повторы, отдых', "
                        "итоговая краткая заметка по прогрессии. "
                        "Не используй термины RPE и RIR, не указывай субъективные шкалы нагрузки. "
                        "Пиши только подходы×повторы, отдых, вес/вариацию/темп при необходимости. "
                        "Не приветствуй пользователя и не используй вводные вроде 'привет'."
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

    def _sanitize_rpe(self, text: str) -> str:
        """Убирает RPE/RIR и чистит артефакты пунктуации."""
        t = _RPE_CHUNK.sub("", text)
        t = _RIR_CHUNK.sub("", t)
        t = _EMPTY_PARENS.sub("", t)
        t = _FIX_COMMAs.sub(",", t)
        t = re.sub(r'\s*,\s*\)', ')', t)
        t = re.sub(r'[ \t]{2,}', ' ', t)
        return t.strip()

    def _apply_header(self, text: str) -> str:
        """Жёстко задаём шапку и убираем приветствия в начале."""
        name = self._user_name
        body = (text or "").lstrip()

        low = body.lower()
        for kw in ("привет", "здравствуй", "здравствуйте", "добрый день", "добрый вечер", "хай"):
            if low.startswith(kw):
                if "\n" in body:
                    body = body.split("\n", 1)[1].lstrip()
                else:
                    body = re.sub(rf'^{kw}\W*', '', body, flags=re.IGNORECASE).lstrip()
                break

        header = (f"{name}, обработал твой запрос, и вот что получилось ⬇️" if name
                  else "Обработал твой запрос, и вот что получилось ⬇️")
        return header + "\n\n" + body

    async def get_response(self, user_input: str) -> str:
        from asyncio import to_thread

        if user_input and user_input.strip():
            self.payload.messages.append(Messages(role=MessagesRole.USER, content=user_input))

        def _chat_sync():
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
        clean = self._sanitize_rpe(message.content)
        personalized = self._apply_header(clean)

        self.payload.messages.append(Messages(role=MessagesRole.ASSISTANT, content=clean))
        history = self.user_data.get("history", [])
        if user_input and user_input.strip():
            history.append(("🧍 " + user_input, "🤖 " + personalized))
        else:
            history.append(("🧍 Запрос программы", "🤖 " + personalized))
        self.user_data["history"] = history
        save_user_data(self.user_id, self.user_data)

        return personalized
