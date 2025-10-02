# app/agent.py
import os
import re
import time
from typing import Optional, List

from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole

from app.storage import load_user_data, save_user_data

# ------------------------- Конфиг -------------------------

GIGACHAT_MODEL: str = os.getenv("GIGACHAT_MODEL", "GigaChat-2-Max").strip()
GIGACHAT_TEMPERATURE: float = float(os.getenv("GIGACHAT_TEMPERATURE", "0.2"))
GIGACHAT_MAX_TOKENS: int = int(os.getenv("GIGACHAT_MAX_TOKENS", "2200"))
GIGACHAT_TIMEOUT: int = int(os.getenv("GIGACHAT_TIMEOUT", "60"))
GIGACHAT_RETRIES: int = int(os.getenv("GIGACHAT_RETRIES", "3"))

# ------------------------- Утилиты пост-обработки -------------------------

_RPE_PATTERNS = [
    r"\(?\s*RPE\s*=?\s*\d+(?:\s*-\s*\d+)?\s*\)?",
    r"\(?\s*RIR\s*=?\s*\d+(?:\s*-\s*\d+)?\s*\)?",
    r"\bдо\s+отказа\b",
    r"\bпочти\s+до\s+отказа\b",
]

def _strip_rpe(text: str) -> str:
    """Убираем RPE/RIR/«до отказа», нормализуем маркеры и переносы."""
    out = text
    for p in _RPE_PATTERNS:
        out = re.sub(p, "", out, flags=re.IGNORECASE)
    # 3x12 -> 3×12
    out = re.sub(r"(\d)\s*[xX\*]\s*(\d)", r"\1×\2", out)
    # Пули в единый стиль
    out = re.sub(r"^\s*[•\-]\s*", "- ", out, flags=re.MULTILINE)
    # Подчистим пустые скобки и двойные запятые
    out = re.sub(r"\(\s*\)", "", out)
    out = re.sub(r",\s*,", ", ", out)
    # Лишние пробелы у переносов
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n[ \t]+", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()

def _strip_html_like(text: str) -> str:
    """Скобочные HTML-теги -> переносы / ничего."""
    out = re.sub(r"\s*<br\s*/?>\s*", "\n", text, flags=re.IGNORECASE)
    out = re.sub(r"</?p\s*/?>", "\n", out, flags=re.IGNORECASE)
    return out

def _drop_hash_headings(text: str) -> str:
    """
    Телеграм иногда путается с # и ## в Markdown.
    Переведём заголовки вида '# …' / '## …' в **полужирный**.
    """
    lines = text.splitlines()
    fixed: List[str] = []
    for ln in lines:
        if re.match(r"^\s*#{1,6}\s+", ln):
            title = re.sub(r"^\s*#{1,6}\s+", "", ln).strip()
            if title:
                fixed.append(f"**{title}**")
            else:
                fixed.append("")
        else:
            fixed.append(ln)
    return "\n".join(fixed)

def _sanitize(text: str) -> str:
    out = _strip_html_like(text)
    out = _strip_rpe(out)
    out = _drop_hash_headings(out)
    return out.strip()

def _physical_context(d: dict) -> str:
    """Компактный текст анкеты для подмешивания в запросы."""
    phys = (d.get("physical_data") or {})
    def g(k, default="не указано"):
        v = phys.get(k)
        return str(v).strip() if (v is not None and str(v).strip()) else default

    return (
        "Данные пользователя:\n"
        f"- Цель: {g('target')}\n"
        f"- Пол: {g('gender')}\n"
        f"- Возраст: {g('age')}\n"
        f"- Рост (см): {g('height')}\n"
        f"- Текущий вес (кг): {g('weight')}\n"
        f"- Желаемый вес (кг): {g('goal')}\n"
        f"- Ограничения/предпочтения: {g('restrictions','нет')}\n"
        f"- Частота тренировок (раз/нед): {g('schedule')}\n"
        f"- Уровень: {g('level')}"
    )

# ------------------------- Промты -------------------------

PLAN_SYSTEM_PROMPT = (
    "Ты — опытный персональный тренер по силовым тренировкам и бодибилдингу (опыт более 8 лет). "
    "Твоя задача — создавать детальные, безопасные и эффективные программы силовых тренировок для зала, "
    "учитывая цели, пол, возраст, вес, уровень подготовки, частоту тренировок и возможные ограничения пользователя.\n\n"

    "Общие правила:\n"
    "• Всегда используй формат **Markdown**.\n"
    "• Не добавляй приветствий, лишних объяснений или заключений — только готовый план.\n"
    "• Пиши грамотно, понятно и профессионально, как персональный тренер фитнес-клуба.\n"
    "• Строй программу **без уточняющих вопросов** — используй все данные анкеты пользователя.\n"
    "• Не используй RPE/RIR и слова «до отказа».\n"
    "• Обязательно давай рекомендации по стартовому весу отягощений, исходя из пола, веса, возраста, уровня подготовки, цели и ограничений пользователя.\n"
    "• Делай программу разнообразной и интересной, избегая монотонных повторяющихся движений.\n"
    "• Не включай травмоопасные или слишком сложные упражнения для новичков (например, олимпийские подъёмы без опыта).\n"
    "• Подбирай адекватный объём работы: не слишком мало, но и не чрезмерно, чтобы хватало ресурса на прогресс.\n\n"

    "Формат плана:\n"
    "• Каждый тренировочный день начинай с новой строки и отделяй пустой строкой.\n"
    "• Заголовок дня: **День N — часть тела/тип тренировки** (например: **День 1 — Грудь и трицепс**).\n"
    "• В начале каждого дня укажи разминку (5–7 минут) с примерами (кардио, суставная разминка).\n"
    "• В конце каждого дня добавь заминку/растяжку (3–5 минут).\n"
    "• Каждое упражнение оформляй в виде: «- Название — 3×12, отдых 90 сек., усилие: умеренно, рекомендуемый вес: ~40 кг».\n"
    "  - Если упражнение выполняется с собственным весом — укажи это явно.\n"
    "  - Если вес зависит от количества повторений — уточни (например: «~40 кг при 8–10 повторениях»).\n"
    "• Для каждого дня указывай оптимальное количество подходов и повторений, а также время отдыха "
    "для тяжёлых базовых и более лёгких изолирующих движений.\n"
    "• После всех дней добавь раздел **Заметки по прогрессии**, где объясни:\n"
    "  - как увеличивать вес (например: «+2–2.5 кг, если все подходы выполнены легко»);\n"
    "  - как уменьшить нагрузку при усталости или дискомфорте;\n"
    "  - общие советы по безопасности (техника важнее веса).\n\n"

    "Подбор упражнений:\n"
    "• При 2–3 тренировках в неделю — используй full body или upper/lower.\n"
    "• При 4 тренировках — применяй upper/lower split или push/pull/legs.\n"
    "• При 5+ тренировках — делай сплит по мышечным группам (грудь/спина/ноги/плечи/руки и т.п.).\n"
    "• Включай базовые многосуставные движения (приседания, жимы, тяги, подтягивания/тяги блока) и дополняй изолирующими.\n"
    "• Адаптируй упражнения под уровень и ограничения пользователя, исключай опасные варианты.\n\n"

    "Главная цель:\n"
    "Создать структурированную, безопасную, разнообразную и понятную программу силовых тренировок, "
    "которую пользователь сможет выполнять в тренажёрном зале, включая рекомендации по весам и чёткие советы по прогрессии."
)


QA_SYSTEM_PROMPT = (
    "Ты — персональный фитнес-тренер высокого уровня (опыт работы более 8 лет). "
    "Ты специализируешься на силовых тренировках, бодибилдинге, функциональном тренинге, кардионагрузках, "
    "похудении, наборе мышечной массы, а также нутрициологии и спортивном питании. "
    "Ты умеешь адаптировать советы под уровень подготовки (новичок, средний, продвинутый), "
    "учитывая пол, возраст, вес, цель, частоту тренировок и ограничения пользователя.\n\n"

    "Как отвечать:\n"
    "• Отвечай **по делу и информативно**, без лишней воды.\n"
    "• Используй **Markdown** для списков и акцентов.\n"
    "• Держи дружелюбный и профессиональный тон, **без приветствий и прощаний**.\n"
    "• Если вопрос требует пояснений по технике, безопасности или питанию — дай практические советы.\n"
    "• Если пользователь просит **составить план или программу тренировок** — делай это сразу, "
    "используя все известные физические данные (пол, возраст, вес, уровень, цель, частоту тренировок, ограничения).\n"
    "• Если вопрос общий (про упражнения, нагрузку, питание, прогрессию, восстановление) — отвечай кратко и структурировано.\n"
    "• Давай рекомендации по выбору весов и прогрессии нагрузки, если это уместно.\n"
    "• Не используй RPE/RIR и слова «до отказа» — описывай усилия словами («лёгко», «умеренно», «тяжело»).\n\n"

    "Важные принципы:\n"
    "• **Безопасность прежде всего** — объясняй правильную технику и как избегать травм.\n"
    "• Давай разнообразные и интересные рекомендации, а не однотипные советы.\n"
    "• Если речь о питании — учитывай цель (похудение, набор массы, поддержание формы), "
    "упоминай БЖУ, калории и полезные источники белков, углеводов и жиров.\n"
    "• Избегай слишком сложных или травмоопасных упражнений для новичков.\n"
    "• Если нужно предложить примерный вес — ориентируйся на пол, вес, уровень подготовки, цель и безопасность.\n\n"

    "Ты можешь помогать по темам:\n"
    "• силовые тренировки (базовые и изолирующие упражнения);\n"
    "• функциональные тренировки и кардио;\n"
    "• питание для похудения, набора массы и здоровья;\n"
    "• прогрессия нагрузки, выбор весов и адаптация под уровень;\n"
    "• восстановление, сон и планирование тренировок.\n"
    "Если вопрос сложный — отвечай развёрнуто, но без лишней воды; если простой — лаконично.\n"
)


# ------------------------- Класс агента -------------------------

class FitnessAgent:
    def __init__(self, token: str, user_id: str):
        self.token = token
        self.user_id = user_id
        self.user_data = load_user_data(user_id)
        phys = (self.user_data.get("physical_data") or {})
        self._user_name: Optional[str] = (phys.get("name") or "").strip() or None

    # ---------- Внутренний универсальный вызов GigaChat ----------

    def _chat_call(self, payload: Chat):
        """
        Унификация вызова SDK (в некоторых версиях chat(model=...) не поддерживается).
        Делаем безопасные ретраи.
        """
        last_err = None
        for attempt in range(1, GIGACHAT_RETRIES + 1):
            try:
                # Попытка №1: без передачи model в chat(...)
                with GigaChat(
                    credentials=self.token,
                    verify_ssl_certs=False,
                    timeout=GIGACHAT_TIMEOUT,
                    model=GIGACHAT_MODEL,  # для некоторых версий достаточно задать при создании
                ) as giga:
                    try:
                        resp = giga.chat(payload)
                    except TypeError:
                        # Альтернативный путь: передать model в метод
                        resp = getattr(giga, "chat")(payload, model=GIGACHAT_MODEL)
                return resp.choices[0].message.content
            except Exception as e:
                last_err = e
                if attempt == GIGACHAT_RETRIES:
                    raise
                time.sleep(1.5 * attempt)
        raise last_err or RuntimeError("GigaChat call failed")

    # ---------- Пользовательские данные в начале ответа ----------

    def _with_name_prefix(self, text: str) -> str:
        name = (self._user_name or "").strip()
        if not name:
            return text
        return f"{name}, вот что я подготовил ⬇️\n\n{text}"

    # ---------- Ответ на произвольный вопрос / короткие планы ----------

    async def get_answer(self, question: str) -> str:
        """
        «Живой» ответ. ВСЕГДА учитывает анкету.
        Если просят план/программу — агент имеет право составить сжатый план (с весами и структурой).
        """
        from asyncio import to_thread

        physical = _physical_context(self.user_data)

        payload = Chat(
            messages=[
                Messages(role=MessagesRole.SYSTEM, content=QA_SYSTEM_PROMPT),
                Messages(role=MessagesRole.USER, content=f"{physical}\n\nВопрос/запрос:\n{question}"),
            ],
            temperature=min(0.45, GIGACHAT_TEMPERATURE),
            max_tokens=min(1200, GIGACHAT_MAX_TOKENS),
            model=GIGACHAT_MODEL,
        )

        def _call():
            return self._chat_call(payload)

        raw = await to_thread(_call)
        txt = _sanitize(raw)
        # сохраняем историю переписки
        hist = self.user_data.get("history", [])
        hist.append(("🧍 " + question, "🤖 " + txt))
        self.user_data["history"] = hist
        save_user_data(self.user_id, self.user_data)
        return txt

    # ---------- Генерация полной программы ----------

    async def get_response(self, user_input: str = "") -> str:
        """
        Полноценная программа силовых тренировок на основе анкеты.
        `user_input` можно передать для уточнений (например, «нужен 5-дневный сплит»).
        """
        from asyncio import to_thread

        physical = _physical_context(self.user_data)

        messages = [
            Messages(role=MessagesRole.SYSTEM, content=PLAN_SYSTEM_PROMPT),
            Messages(role=MessagesRole.USER, content=physical),
        ]
        if user_input and user_input.strip():
            messages.append(Messages(role=MessagesRole.USER, content=f"Дополнительные пожелания: {user_input}"))

        payload = Chat(
            messages=messages,
            temperature=GIGACHAT_TEMPERATURE,
            max_tokens=GIGACHAT_MAX_TOKENS,
            model=GIGACHAT_MODEL,
        )

        def _call():
            return self._chat_call(payload)

        raw = await to_thread(_call)
        cleaned = _sanitize(raw)
        personalized = self._with_name_prefix(cleaned)

        # История
        hist = self.user_data.get("history", [])
        hist.append(("🧍 Запрос программы" if not user_input else "🧍 " + user_input, "🤖 " + personalized))
        self.user_data["history"] = hist
        # Последний ответ — для «Сохранить в файл»
        self.user_data["last_reply"] = personalized
        self.user_data["last_program"] = personalized
        save_user_data(self.user_id, self.user_data)

        return personalized
