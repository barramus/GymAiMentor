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

_RPE_PATTERNS = [
    r"\(?\s*RPE\s*=?\s*\d+(?:\s*-\s*\d+)?\s*\)?",
    r"\(?\s*RIR\s*=?\s*\d+(?:\s*-\s*\d+)?\s*\)?",
    r"\bдо\s+отказа\b",
    r"\bпочти\s+до\s+отказа\b",
]

def _strip_rpe(text: str) -> str:
    """Убираем RPE/RIR/«до отказа» и приводим формат, не ломая переносы строк."""
    out = text
    for p in _RPE_PATTERNS:
        out = re.sub(p, "", out, flags=re.IGNORECASE)

    out = re.sub(r"^\s*•\s+", "- ", out, flags=re.MULTILINE)

    out = re.sub(r"(\d)\s*[xX\*]\s*(\d)", r"\1×\2", out)

    out = re.sub(r"\(\s*\)", "", out)
    out = re.sub(r",\s*,", ", ", out)

    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n[ \t]+", "\n", out)

    out = re.sub(r"\n{3,}", "\n\n", out)

    return out.strip()


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
                        "Ты — персональный фитнес-тренер по бодибилдингу в тренажёрном зале с опытом 8+ лет. "
                        "Пиши строго в формате Markdown, без приветствий и прощаний. "
                        "Выход — только структурированный план без лишнего текста.\n\n"
                        "Требования к оформлению:\n"
                        "• Каждый день с новой строки и пустой строкой между днями.\n"
                        "• Заголовок дня: **День N** (или **День N — часть тела**).\n"
                        "• В начале каждого дня добавь короткую разминку (5–7 минут) и в конце заминку/растяжку (3–5 минут).\n"
                        "• Упражнения — маркированный список, на строку одно упражнение:\n"
                        "  - «Название — 3×12, отдых 90 сек.» (знак ×; тире «—» между названием и сеткой).\n"
                        "• В конце плана добавь короткий блок **Заметки по прогрессии** (как увеличивать вес/повторы).\n\n"
                        "Содержательная часть:\n"
                        "• Обязательно указывай подходы × повторы и отдых в секундах.\n"
                        "• НЕ используй термины RPE/RIR и фразы «до отказа». "
                        "  Если требуется ориентир усилий — пиши словами: «лёгко», «умеренно», «тяжело».\n"
                        "• Строй программу без уточняющих вопросов, используя данные анкеты пользователя "
                        "(цель, пол, возраст, рост, вес, желаемый вес, ограничения, частота тренировок, уровень).\n"
                        "• Учитывай ограничения/особенности и доступное число тренировок в неделю.\n"
                        "• Если тренировочных дней меньше 3, объединяй группы мышц разумно; если 4+ — распределяй сплитом.\n\n"
                        "Пример формата (образец разметки, не содержимое):\n"
                        "**День 1 — Верх тела**\n"
                        "- Жим штанги лёжа — 4×8–10, отдых 90 сек., усилие: умеренно-тяжело\n"
                        "- Тяга горизонтального блока — 3×10–12, отдых 75 сек., усилие: умеренно\n"
                        "- Подъёмы на бицепс — 3×12–15, отдых 60 сек., усилие: умеренно\n"
                        "\n"
                        "**День 2 — Ноги/ягодицы**\n"
                        "- Приседания — 4×6–8, отдых 120 сек., усилие: тяжело\n"
                        "- Румынская тяга — 3×8–10, отдых 90 сек., усилие: умеренно-тяжело\n"
                        "\n"
                        "**Заметки по прогрессии**\n"
                        "- Если все подходы даются легко — добавляй +2–2.5 кг или +1–2 повтора в следующий раз.\n"
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
        """Всегда одинаковое вступление без 'привет'."""
        name = (self._user_name or "").strip()
        prefix = f"{name}, обработал твой запрос — вот что получилось ⬇️\n\n" if name else ""
        return prefix + text

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
        self.payload.messages.append(message)

        cleaned = _strip_rpe(message.content)
        personalized = self._with_name_prefix(cleaned)

        history = self.user_data.get("history", [])
        if user_input and user_input.strip():
            history.append(("🧍 " + user_input, "🤖 " + personalized))
        else:
            history.append(("🧍 Запрос программы", "🤖 " + personalized))
        self.user_data["history"] = history
        save_user_data(self.user_id, self.user_data)

        return personalized
