import os
import re
import time
from typing import Optional

from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole

from app.storage import load_user_data, save_user_data

GIGACHAT_MODEL: str = os.getenv("GIGACHAT_MODEL", "GigaChat-2-Max").strip()
GIGACHAT_TEMPERATURE: float = float(os.getenv("GIGACHAT_TEMPERATURE", "0.25"))
GIGACHAT_MAX_TOKENS: int = int(os.getenv("GIGACHAT_MAX_TOKENS", "2200"))
GIGACHAT_TIMEOUT: int = int(os.getenv("GIGACHAT_TIMEOUT", "60"))
GIGACHAT_RETRIES: int = int(os.getenv("GIGACHAT_RETRIES", "3"))

# ---------- промты ----------

SYSTEM_PROMPT = (
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



# ---------- чистка и нормализация ----------

_RPE_PATTERNS = [
    r"\(?\s*RPE\s*=?\s*\d+(?:\s*-\s*\d+)?\s*\)?",
    r"\(?\s*RIR\s*=?\s*\d+(?:\s*-\s*\d+)?\s*\)?",
    r"\bдо\s+отказа\b",
    r"\bпочти\s+до\s+отказа\b",
]

def _strip_noise(text: str) -> str:
    """Убираем RPE/RIR/«до отказа», лишние пробелы и #/## заголовки."""
    out = text or ""
    # RPE/RIR
    for p in _RPE_PATTERNS:
        out = re.sub(p, "", out, flags=re.IGNORECASE)

    # заменить маркеры • на дефисы, x/* на ×
    out = re.sub(r"^\s*•\s+", "- ", out, flags=re.MULTILINE)
    out = re.sub(r"(\d)\s*[xX\*]\s*(\d)", r"\1×\2", out)

    # убрать HTML теги <br>, <p>
    out = re.sub(r"\s*<br\s*/?>\s*", "\n", out, flags=re.IGNORECASE)
    out = re.sub(r"</?p\s*/?>", "\n", out, flags=re.IGNORECASE)

    # убрать markdown заголовки # и ##
    out = re.sub(r"^\s*#{1,6}\s*", "", out, flags=re.MULTILINE)

    # косметика
    out = re.sub(r"\(\s*\)", "", out)
    out = re.sub(r",\s*,", ", ", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n[ \t]+", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)

    return out.strip()

def _to_int(s) -> Optional[int]:
    try:
        return int(re.search(r"\d+", str(s)).group(0))
    except Exception:
        return None

# ---------- агент ----------

class FitnessAgent:
    def __init__(self, token: str, user_id: str):
        self.token = token
        self.user_id = user_id
        self.user_data = load_user_data(user_id)

        phys = self.user_data.get("physical_data") or {}
        self._user_name: Optional[str] = (phys.get("name") or "").strip() or None

        self._phys_prompt = self._format_physical_data(phys)

    # — публичные методы —

    async def get_program(self, user_instruction: str = "") -> str:
        """
        Вернёт сгенерированную программу (Markdown), с учётом анкеты.
        user_instruction — дополнительные пожелания (например: «сделай 5 дней»).
        """
        from asyncio import to_thread
        payload = Chat(
            messages=[
                Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT),
                Messages(role=MessagesRole.USER, content=self._phys_prompt + (f"\n\nПожелания: {user_instruction}" if user_instruction else "")),
            ],
            temperature=GIGACHAT_TEMPERATURE,
            max_tokens=GIGACHAT_MAX_TOKENS,
            model=GIGACHAT_MODEL,
        )

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
                            resp = giga.chat(payload)
                            return resp.choices[0].message.content
                    except TypeError:
                        with GigaChat(
                            credentials=self.token,
                            verify_ssl_certs=False,
                            timeout=GIGACHAT_TIMEOUT,
                        ) as giga:
                            resp = getattr(giga, "chat")(payload, model=GIGACHAT_MODEL)
                            return resp.choices[0].message.content
                except Exception as e:
                    last_err = e
                    if attempt == GIGACHAT_RETRIES:
                        raise
                    time.sleep(1.5 * attempt)
            raise last_err or RuntimeError("GigaChat call failed")

        txt = await to_thread(_chat_sync)
        cleaned = _strip_noise(txt)
        final = self._with_name_prefix(cleaned)

        # сохраняем в историю и как последнюю программу
        hist = self.user_data.get("history", [])
        hist.append(("🧍 Запрос программы", "🤖 " + final))
        self.user_data["history"] = hist
        self.user_data["last_program"] = final
        self.user_data["last_reply"] = final
        save_user_data(self.user_id, self.user_data)
        return final

    async def get_answer(self, question: str) -> str:
        """
        Краткий структурированный ответ/совет. Если явно просят план — можно выдать план (учитывая анкету).
        """
        from asyncio import to_thread
        payload = Chat(
            messages=[
                Messages(role=MessagesRole.SYSTEM, content=QA_SYSTEM_PROMPT),
                Messages(role=MessagesRole.USER, content=f"Анкета:\n{self._phys_prompt}\n\nВопрос:\n{question}"),
            ],
            temperature=min(0.35, GIGACHAT_TEMPERATURE),
            max_tokens=min(1000, GIGACHAT_MAX_TOKENS),
            model=GIGACHAT_MODEL,
        )

        def _chat_sync():
            try:
                with GigaChat(credentials=self.token, verify_ssl_certs=False, timeout=GIGACHAT_TIMEOUT) as giga:
                    try:
                        resp = giga.chat(payload)  # новые SDK
                    except TypeError:
                        resp = getattr(giga, "chat")(payload, model=GIGACHAT_MODEL)  # старые SDK
                    return resp.choices[0].message.content
            except Exception as e:
                raise e

        txt = await to_thread(_chat_sync)
        cleaned = _strip_noise(txt).strip()

        # история
        hist = self.user_data.get("history", [])
        hist.append(("🧍 " + question, "🤖 " + cleaned))
        self.user_data["history"] = hist
        self.user_data["last_reply"] = cleaned
        save_user_data(self.user_id, self.user_data)
        return cleaned

    # — утилиты —

    def _format_physical_data(self, d: dict) -> str:
        return (
            f"Цель: {d.get('target') or 'не указана'}\n"
            f"Пол: {d.get('gender') or 'не указано'}\n"
            f"Возраст: {d.get('age') or 'не указано'} лет\n"
            f"Рост: {d.get('height') or 'не указано'} см\n"
            f"Текущий вес: {d.get('weight') or 'не указано'} кг\n"
            f"Желаемый вес: {d.get('goal') or 'не указано'} кг\n"
            f"Ограничения: {d.get('restrictions') or 'нет'}\n"
            f"Частота тренировок: {d.get('schedule') or 'не указано'}\n"
            f"Уровень: {d.get('level') or 'не указано'}"
        )

    def _with_name_prefix(self, text: str) -> str:
        name = (self._user_name or "").strip()
        return (f"{name}, вот твой план ⬇️\n\n" if name else "") + text
