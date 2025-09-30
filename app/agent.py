import os
import re
import time
from typing import Optional

from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole

from app.storage import load_user_data, save_user_data
from app.weights import (
    User as WUser,
    History as WHistory,
    recommend_start_weight,
    base_key,
)

# --- Параметры GigaChat из .env ---
GIGACHAT_MODEL: str = os.getenv("GIGACHAT_MODEL", "GigaChat-2-Max").strip()
GIGACHAT_TEMPERATURE: float = float(os.getenv("GIGACHAT_TEMPERATURE", "0.2"))
GIGACHAT_MAX_TOKENS: int = int(os.getenv("GIGACHAT_MAX_TOKENS", "2000"))
GIGACHAT_TIMEOUT: int = int(os.getenv("GIGACHAT_TIMEOUT", "60"))
GIGACHAT_RETRIES: int = int(os.getenv("GIGACHAT_RETRIES", "3"))

# --- Нормализация вывода (убираем RPE/RIR/«до отказа», правим маркеры) ---
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
    out = re.sub(r"^\s*•\s+", "- ", out, flags=re.MULTILINE)       # bullets → "-"
    out = re.sub(r"(\d)\s*[xX\*]\s*(\d)", r"\1×\2", out)           # 3x12 → 3×12
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

        physical_data = self.user_data.get("physical_data", {}) or {}
        self._user_name: Optional[str] = (physical_data.get("name") or "").strip() or None

        # — жёсткие требования с учётом анкеты (дни/кол-во упражнений) —
        level = (physical_data.get("level") or "").strip().lower()
        days = _to_int(physical_data.get("schedule"))
        per_day = "5–7" if level == "опытный" else "4–5"
        strict_block = []
        if days:
            strict_block.append(f"• Сделай РОВНО {days} тренировочных дней в неделю.")
        strict_block.append(f"• В каждом дне перечисли {per_day} силовых упражнений (не считая разминку и заминку).")
        strict_block.append("• Не используй HTML-теги (<br>, <p>) — только Markdown и обычные переносы строк.")
        strict_text = "\n".join(strict_block)

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
                        "• Упражнения — маркированный список, формат строки ровно такой:\n"
                        "  - «Название — 3×12, отдых 90 сек.» (знак ×; тире «—» между названием и сеткой).\n"
                        "• В конце плана добавь блок **Заметки по прогрессии** (как увеличивать вес/повторы).\n\n"
                        "Содержательная часть:\n"
                        "• Обязательно указывай подходы × повторы и отдых в секундах.\n"
                        "• НЕ используй RPE/RIR и фразы «до отказа». Если нужен ориентир усилий — «лёгко/умеренно/тяжело».\n"
                        "• Строй программу без уточняющих вопросов, используя данные анкеты.\n"
                        "• Если тренировочных дней < 3 — объединяй группы мышц разумно; если 4+ — используй сплиты.\n"
                        f"\n{strict_text}\n"
                    ),
                ),
                Messages(role=MessagesRole.USER, content=physical_prompt),
            ],
            temperature=GIGACHAT_TEMPERATURE,
            max_tokens=GIGACHAT_MAX_TOKENS,
            model=GIGACHAT_MODEL,
        )

    # ---------- Q&A без генерации программ ----------
    async def get_answer(self, question: str) -> str:
        from asyncio import to_thread
        qa_payload = Chat(
            messages=[
                Messages(
                    role=MessagesRole.SYSTEM,
                    content=(
                        "Ты — персональный тренер. Отвечай по сути на вопросы о тренировках и питании. "
                        "НЕ генерируй тренировочные программы. Без приветствий, по делу, кратко."
                    ),
                ),
                Messages(role=MessagesRole.USER, content=question),
            ],
            temperature=min(0.4, GIGACHAT_TEMPERATURE),
            max_tokens=700,
            model=GIGACHAT_MODEL,
        )

        def _chat_sync():
            with GigaChat(credentials=self.token, verify_ssl_certs=False, timeout=GIGACHAT_TIMEOUT) as giga:
                resp = getattr(giga, "chat")(qa_payload, model=GIGACHAT_MODEL)
                return resp.choices[0].message.content

        txt = await to_thread(_chat_sync)
        return _strip_rpe(txt).strip()

    # ---------- Служебные форматтеры ----------
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
        name = (self._user_name or "").strip()
        return (f"{name}, обработал твой запрос — вот что получилось ⬇️\n\n" if name else "") + text

    # ---------- Контекст для расчёта отягощений ----------
    def _weight_context(self) -> tuple[WUser, dict[str, WHistory]]:
        d = self.user_data or {}
        phys = (d.get("physical_data") or {})

        # осторожно приводим к float
        weight_val = phys.get("weight")
        try:
            weight_kg = float(str(weight_val).replace(",", ".")) if weight_val is not None else None
        except Exception:
            weight_kg = None

        user = WUser(
            gender=(phys.get("gender") or "").lower() or None,
            age=_to_int(phys.get("age")),
            height=_to_int(phys.get("height")),
            weight=weight_kg,
            goal=(phys.get("target") or "").lower() or None,
            level=(phys.get("level") or "").lower() or None,
        )

        # История может отсутствовать — это ок
        hist_raw = d.get("lifts") or {}
        history: dict[str, WHistory] = {}
        for k, rec in hist_raw.items():
            try:
                history[k] = WHistory(
                    last_weight=float(rec.get("last_weight")) if rec.get("last_weight") is not None else None,
                    reps=int(rec.get("reps")) if rec.get("reps") is not None else None,
                    rir=int(rec.get("rir")) if rec.get("rir") is not None else None,
                )
            except Exception:
                # пропускаем битые записи
                continue
        return user, history

    # ---------- Подстановка «рекомендация: ~Х кг» в план ----------
    def _annotate_plan_with_weights(self, text: str) -> str:
        """
        Ищем строки формата:
          - <Название> — 3×12, отдых ...
        и добавляем в хвост: ", рекомендация: ~Х кг".
        """
        user, history = self._weight_context()

        def _norm(s: str) -> str:
            s = s.lower().replace("ё", "е")
            return re.sub(r"\s+", " ", s).strip()

        lines = text.splitlines()
        out = []
        for ln in lines:
            m = re.search(r"^\s*[-•]\s*(.+?)\s+—\s+(\d+)\s*×\s*(\d+)(?:[–-]\d+)?", ln)
            if not m:
                out.append(ln)
                continue

            ex_name = m.group(1).strip()
            target_reps = int(m.group(3))
            key = base_key(_norm(ex_name)) or ""

            try:
                # берём историю по ключу, если есть
                hist = history.get(key)
                rec_w, _source = recommend_start_weight(ex_name, user, target_reps, hist)
            except Exception:
                rec_w = None

            if rec_w:
                val = int(rec_w) if float(rec_w).is_integer() else round(float(rec_w), 1)
                if "рекомендац" not in ln:
                    ln = f"{ln}, рекомендация: ~{val} кг"

            out.append(ln)

        return "\n".join(out)

    # ---------- Главный метод генерации программы / ответа ----------
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

        # Тихо пытаемся дополнить рекомендациями по весам
        try:
            personalized = self._annotate_plan_with_weights(personalized)
        except Exception:
            pass

        # Сохраняем историю диалога
        history = self.user_data.get("history", [])
        if user_input and user_input.strip():
            history.append(("🧍 " + user_input, "🤖 " + personalized))
        else:
            history.append(("🧍 Запрос программы", "🤖 " + personalized))
        self.user_data["history"] = history
        save_user_data(self.user_id, self.user_data)

        return personalized
