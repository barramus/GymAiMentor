# app/agent.py
import os
import re
import time
from typing import Optional

from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole

from app.storage import load_user_data, save_user_data
from app.weights import (
    User as WUser,
    recommend_weight_for_exercise,
    base_key,
)

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

def _to_int(s: str | int | None) -> Optional[int]:
    if s is None:
        return None
    if isinstance(s, int):
        return s
    m = re.search(r"\d+", str(s))
    return int(m.group(0)) if m else None

class FitnessAgent:
    def __init__(self, token: str, user_id: str):
        self.token = token
        self.user_id = user_id
        self.user_data = load_user_data(user_id)

        physical = self.user_data.get("physical_data") or {}
        self._user_name: Optional[str] = (physical.get("name") or "").strip() or None

        level = (physical.get("level") or "").strip().lower()
        days = _to_int(physical.get("schedule"))
        per_day = "5–7" if level == "опытный" else "4–5"
        strict = []
        if days:
            strict.append(f"• Сделай РОВНО {days} тренировочных дней в неделю.")
        strict.append(f"• В каждом дне перечисли {per_day} силовых упражнений.")
        strict.append("• Не используй HTML-теги (<br>, <p>) — только Markdown и \\n.")
        strict_text = "\n".join(strict)

        physical_prompt = self._format_physical_data(physical)

        self.payload = Chat(
            messages=[
                Messages(
                    role=MessagesRole.SYSTEM,
                    content=(
                        "Ты — персональный фитнес-тренер по бодибилдингу (8+ лет). "
                        "Пиши СТРОГО Markdown, без приветствий и заключений. "
                        "Выход — только структурированный план.\n\n"
                        "Формат:\n"
                        "• Каждый день с новой строки, между днями пустая строка.\n"
                        "• Заголовок: **День N — часть тела**.\n"
                        "• В начале дня разминка 5–7 мин, в конце заминка/растяжка 3–5 мин.\n"
                        "• Упражнения — маркеры «- ». Одна строка: «Название — 3×12, отдых 90 сек., усилие: умеренно».\n"
                        "• В конце блок **Заметки по прогрессии**.\n\n"
                        "Ограничения:\n"
                        "• Обязательно указывай подходы×повторы и отдых (сек).\n"
                        "• Не используй RPE/RIR/«до отказа». Если нужно — пиши «лёгко/умеренно/тяжело».\n"
                        "• Строй программу без уточняющих вопросов по анкете.\n"
                        f"{strict_text}\n"
                    ),
                ),
                Messages(role=MessagesRole.USER, content=physical_prompt),
            ],
            temperature=GIGACHAT_TEMPERATURE,
            max_tokens=GIGACHAT_MAX_TOKENS,
            model=GIGACHAT_MODEL,
        )

    async def get_answer(self, question: str) -> str:
        """Краткий ответ на вопрос; НЕ генерирует программы."""
        from asyncio import to_thread
        qa_payload = Chat(
            messages=[
                Messages(
                    role=MessagesRole.SYSTEM,
                    content=(
                        "Ты — персональный тренер. Отвечай по сути на вопросы "
                        "о тренировках и питании. НЕ формируй тренировочные планы, "
                        "не используй приветствия/завершения. Пиши кратко."
                    ),
                ),
                Messages(role=MessagesRole.USER, content=question),
            ],
            temperature=min(0.4, GIGACHAT_TEMPERATURE),
            max_tokens=700,
            model=GIGACHAT_MODEL,
        )

        def _chat_sync():
            # В SDK есть версии с сигнатурой chat(payload) — используем её.
            with GigaChat(credentials=self.token, verify_ssl_certs=False, timeout=GIGACHAT_TIMEOUT) as giga:
                resp = getattr(giga, "chat")(qa_payload)
                return resp.choices[0].message.content

        txt = await to_thread(_chat_sync)
        return _strip_rpe(txt).strip()

    def _format_physical_data(self, d: dict) -> str:
        return (
            f"Цель: {d.get('target', 'не указана')}\n"
            f"Пол: {d.get('gender', 'не указано')}\n"
            f"Возраст: {d.get('age', 'не указано')} лет\n"
            f"Рост: {d.get('height', 'не указано')} см\n"
            f"Текущий вес: {d.get('weight', 'не указано')} кг\n"
            f"Желаемый вес: {d.get('goal', 'не указано')} кг\n"
            f"Ограничения: {d.get('restrictions', 'нет')}\n"
            f"Частота тренировок: {d.get('schedule', 'не указано')}\n"
            f"Уровень подготовки: {d.get('level', 'не указано')}"
        )

    def _with_name_prefix(self, text: str) -> str:
        name = (self._user_name or "").strip()
        return (f"{name}, обработал твой запрос — вот что получилось ⬇️\n\n" if name else "") + text

    def _weight_context_user(self) -> WUser:
        phys = (self.user_data.get("physical_data") or {})
        def _flt(v):
            try:
                return float(str(v).replace(",", ".")) if v is not None else None
            except Exception:
                return None
        return WUser(
            gender=(phys.get("gender") or "").lower() or None,
            age=_to_int(phys.get("age")),
            height_cm=_to_int(phys.get("height")),
            weight_kg=_flt(phys.get("weight")),
            level=(phys.get("level") or "").lower() or None,
            target=(phys.get("target") or "").lower() or None,
        )

    def _annotate_plan_with_weights(self, text: str) -> str:
        """Добавляем в строки упражнений «, рекомендация: ~X кг» по анкете."""
        user = self._weight_context_user()

        def _norm(s: str) -> str:
            s = s.lower().replace("ё", "е")
            return re.sub(r"\s+", " ", s).strip()

        out = []
        for ln in text.splitlines():
            m = re.search(r"^\s*[-•]\s*(.+?)\s+—\s+(\d+\s*×\s*\d+(?:–\d+)?)", ln)
            if not m:
                out.append(ln)
                continue
            raw_name = m.group(1)
            if not base_key(_norm(raw_name)):
                out.append(ln)
                continue
            try:
                w, _src = recommend_weight_for_exercise(raw_name, user, target_reps=10, history=None)
            except Exception:
                out.append(ln)
                continue
            val = int(w) if float(w).is_integer() else round(float(w), 1)
            if "рекомендац" not in ln:
                ln = f"{ln}, рекомендация: ~{val} кг"
            out.append(ln)
        return "\n".join(out)

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
                            resp = giga.chat(self.payload)
                            return resp.choices[0].message
                    except TypeError:
                        # старые версии SDK без параметра model в chat()
                        with GigaChat(
                            credentials=self.token,
                            verify_ssl_certs=False,
                            timeout=GIGACHAT_TIMEOUT,
                        ) as giga:
                            resp = getattr(giga, "chat")(self.payload)
                            return resp.choices[0].message
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
        try:
            personalized = self._annotate_plan_with_weights(personalized)
        except Exception:
            pass

        hist = self.user_data.get("history", [])
        hist.append(("🧍 Запрос" if not user_input else "🧍 " + user_input, "🤖 " + personalized))
        self.user_data["history"] = hist
        save_user_data(self.user_id, self.user_data)
        return personalized
