# app/agent.py

from __future__ import annotations

import os
import re
import time
from typing import Optional, Dict, Tuple

from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole

from app.storage import load_user_data, save_user_data
from app.weights import (
    User as WUser,
    History as WHistory,
    recommend_weight_for_exercise,  # точечный расчёт под упражнение
    recommend_start_weight,         # совместимость, если ключ неизвестен
    base_key,
)

# ---------------- Конфиг модели ----------------

GIGACHAT_MODEL: str = os.getenv("GIGACHAT_MODEL", "GigaChat-2-Max").strip()
GIGACHAT_TEMPERATURE: float = float(os.getenv("GIGACHAT_TEMPERATURE", "0.2"))
GIGACHAT_MAX_TOKENS: int = int(os.getenv("GIGACHAT_MAX_TOKENS", "2000"))
GIGACHAT_TIMEOUT: int = int(os.getenv("GIGACHAT_TIMEOUT", "60"))
GIGACHAT_RETRIES: int = int(os.getenv("GIGACHAT_RETRIES", "3"))


# ---------------- Утилиты форматирования ----------------

_RPE_PATTERNS = [
    r"\(?\s*RPE\s*=?\s*\d+(?:\s*-\s*\d+)?\s*\)?",
    r"\(?\s*RIR\s*=?\s*\d+(?:\s*-\s*\d+)?\s*\)?",
    r"\bдо\s+отказа\b",
    r"\bпочти\s+до\s+отказа\b",
]

def _strip_rpe(text: str) -> str:
    """Убираем RPE/RIR/«до отказа», нормализуем маркеры и переносы, не ломая Markdown."""
    out = text or ""
    for p in _RPE_PATTERNS:
        out = re.sub(p, "", out, flags=re.IGNORECASE)

    # HTML → переносы
    out = re.sub(r"\s*<br\s*/?>\s*", "\n", out, flags=re.IGNORECASE)
    out = re.sub(r"</?p\s*/?>", "\n", out, flags=re.IGNORECASE)

    # bullets → дефисы
    out = re.sub(r"^\s*•\s+", "- ", out, flags=re.MULTILINE)

    # 3x12 -> 3×12
    out = re.sub(r"(\d)\s*[xX\*]\s*(\d)", r"\1×\2", out)

    # чистим пустые скобки и лишние пробелы
    out = re.sub(r"\(\s*\)", "", out)
    out = re.sub(r",\s*,", ", ", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n[ \t]+", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)

    # висячие одинарные бэктики
    out = re.sub(r"`(?=[^`]*$)", "", out)
    return out.strip()

def _to_int(s: str | int | None) -> Optional[int]:
    if s is None:
        return None
    if isinstance(s, int):
        return s
    m = re.search(r"\d+", str(s))
    return int(m.group(0)) if m else None


# ---------------- Агент ----------------

class FitnessAgent:
    def __init__(self, token: str, user_id: str):
        self.token = token
        self.user_id = user_id
        self.user_data = load_user_data(user_id)

        physical_data = self.user_data.get("physical_data", {}) or {}
        self._user_name: Optional[str] = (physical_data.get("name") or "").strip() or None

        # Жёсткие требования от анкеты к структуре плана
        level = (physical_data.get("level") or "").strip().lower()
        days = _to_int(physical_data.get("schedule"))
        per_day = "5–7" if level == "опытный" else "4–5"

        strict_bits = []
        if days:
            strict_bits.append(f"• Сделай РОВНО {days} тренировочных дней в неделю.")
        strict_bits.append(f"• В каждом дне перечисли {per_day} силовых упражнений (не считая разминку и заминку).")
        strict_bits.append("• Если упражнений меньше — добавь, если больше — сократи.")
        strict_bits.append("• Не используй HTML-теги (<br>, <p>) — только Markdown и обычные переносы строк.")
        strict_text = "\n".join(strict_bits)

        physical_prompt = self._format_physical_data(physical_data)

        # Базовый чат для генерации ПЛАНОВ
        self.payload = Chat(
            messages=[
                Messages(
                    role=MessagesRole.SYSTEM,
                    content=(
                        "Ты — опытный персональный тренер по силовым тренировкам и бодибилдингу (опыт более 8 лет). "
                        "Твоя задача — создавать детальные, безопасные и эффективные тренировочные программы для зала "
                        "с учётом целей, пола, возраста, веса, уровня подготовки, частоты тренировок и ограничений пользователя.\n\n"

                        "### Общие правила:\n"
                        "• Всегда отвечай в формате **Markdown**.\n"
                        "• Не используй приветствий, лишних объяснений или заключений — только готовый план.\n"
                        "• Пиши грамотно и понятно, как для клиента в фитнес-клубе.\n"
                        "• Строй программу **без уточняющих вопросов** — используй данные анкеты пользователя полностью.\n"
                        "• Не используй RPE/RIR и слова «до отказа».\n"
                        "• Давай рекомендации по весам отягощений, основываясь на данных пользователя "
                        "(пол, вес, возраст, уровень подготовки, цель, возможные ограничения).\n"
                        "• Упражнения подбирай так, чтобы программа была **интересной, разнообразной и безопасной**, "
                        "но при этом соответствовала уровню пользователя.\n"
                        "• Избегай сложных, травмоопасных или неподходящих движений, если пользователь новичок "
                        "(например, не включай олимпийские подъёмы без опыта).\n"
                        "• Подбирай разумный объём работы: не слишком мало и не чрезмерно, чтобы хватало ресурса на прогресс.\n\n"

                        "### Формат плана:\n"
                        "• Каждый тренировочный день пиши с новой строки и отделяй пустой строкой.\n"
                        "• Заголовок дня: **День N — часть тела/тип тренировки** (например: **День 1 — Грудь и трицепс**).\n"
                        "• В начале каждого дня укажи разминку (5–7 минут) с простыми примерами (кардио, суставная разминка).\n"
                        "• В конце каждого дня добавь заминку/растяжку (3–5 минут).\n"
                        "• Каждое упражнение оформляй так: «- Название — 3×12, отдых 90 сек., усилие: умеренно, рекомендуемый вес: ~40 кг».\n"
                        "• Обязательно добавляй **рекомендуемый стартовый вес** для каждого упражнения "
                        "(ориентируйся на пол, вес, уровень, цель и безопасную нагрузку для первых подходов). "
                        "Если упражнение выполняется с собственным весом — так и указывай («работа с собственным весом»).\n"
                        "• Если вес зависит от количества повторений, уточни это (например: «~40 кг при 8–10 повторениях»).\n"
                        "• Для каждого дня указывай **оптимальное количество подходов и повторений**, а также время отдыха "
                        "для всех упражнений (различай тяжёлые базовые и изолирующие движения).\n"
                        "• В конце программы всегда добавляй раздел **Заметки по прогрессии**, где объясняешь:\n"
                        "  - как увеличивать вес (например: «+2–2.5 кг, если все подходы выполнены легко»);\n"
                        "  - как адаптировать нагрузку при утомлении или дискомфорте;\n"
                        "  - общие советы по безопасности (например: техника важнее веса).\n\n"

                        "### Подбор упражнений:\n"
                        "• При 2–3 тренировках в неделю — используй **full body** или upper/lower.\n"
                        "• При 4 тренировках — классический **upper/lower split** или push/pull/legs.\n"
                        "• При 5+ тренировках — можешь использовать сплит по мышечным группам.\n"
                        "• Обязательно добавляй базовые движения (приседания, жим, тяга, подтягивания/тяги) и дополняй изолирующими.\n"
                        "• Если есть ограничения или слабые места — адаптируй упражнения под пользователя.\n\n"

                        "### Ключевая задача:\n"
                        "Создать **структурированную, безопасную и понятную** программу, которую пользователь сможет "
                        "выполнять в зале, включая ориентир по весам и советы по прогрессии.\n"
                        f"{strict_text}\n"
                    ),
                ),
                Messages(role=MessagesRole.USER, content=physical_prompt),
            ],
            temperature=GIGACHAT_TEMPERATURE,
            max_tokens=GIGACHAT_MAX_TOKENS,
            model=GIGACHAT_MODEL,
        )

    # ---------- Публичные методы ----------

    async def get_answer(self, question: str) -> str:
        """
        Краткий ответ (живой диалог), НО с учётом анкеты пользователя.
        Если пользователь просит «составь план», ассистент может это сделать.
        """
        from asyncio import to_thread

        # вклеим физконтекст в вопрос
        context = self._format_physical_data(self.user_data.get("physical_data") or {})
        qa_payload = Chat(
            messages=[
                Messages(
                    role=MessagesRole.SYSTEM,
                    content=(
                        "Ты — персональный фитнес-тренер высокого уровня (опыт работы более 8 лет). "
                        "Ты специализируешься на силовых тренировках, бодибилдинге, функциональном тренинге, "
                        "кардионагрузках, похудении, наборе мышечной массы, а также нутрициологии и спортивном питании. "
                        "Ты умеешь адаптировать рекомендации под уровень подготовки (новичок, средний, продвинутый), "
                        "учитывая пол, возраст, вес, цели и ограничения пользователя.\n\n"

                        "### Как отвечать:\n"
                        "• Всегда отвечай **по делу, без лишней воды**, но информативно и понятно.\n"
                        "• Используй **Markdown** для оформления списков и акцентов.\n"
                        "• Отвечай дружелюбно, но без приветствий и прощаний.\n"
                        "• Если вопрос требует пояснений по технике, безопасности или питанию — дай практические советы.\n"
                        "• Если пользователь просит **составить план/программу тренировок**, можешь сделать это сразу "
                        "(используя все известные о нём физические данные: пол, возраст, вес, цель, уровень, частота тренировок, ограничения).\n"
                        "• Если вопрос общий (про упражнения, нагрузку, питание, прогрессию, восстановление) — отвечай кратко, "
                        "структурированно, с конкретными рекомендациями.\n"
                        "• Давай советы по выбору веса отягощений и прогрессии нагрузки, если это актуально.\n"
                        "• Не используй RPE/RIR и слова «до отказа» — описывай усилия словами («лёгко», «умеренно», «тяжело»).\n\n"

                        "### Важные принципы:\n"
                        "• Безопасность прежде всего — подсказывай корректную технику и как избегать травм.\n"
                        "• Давай разнообразные, интересные и эффективные рекомендации, а не однотипные советы.\n"
                        "• Если спрашивают про питание — учитывай цель (похудение, набор массы, поддержание формы), "
                        "упоминай БЖУ, калории, полезные источники белка/углеводов/жиров.\n"
                        "• Если пользователь неопытен, избегай слишком сложных или травмоопасных упражнений.\n"
                        "• Если нужно рассчитать примерные веса — ориентируйся на пол, вес, уровень и цель пользователя.\n\n"

                        "Ты можешь отвечать на любые вопросы, связанные с:\n"
                        "• силовыми тренировками (базовые и изолирующие упражнения);\n"
                        "• функциональными тренировками и кардио;\n"
                        "• питанием для похудения, набора массы и здоровья;\n"
                        "• прогрессией нагрузки и выбором весов;\n"
                        "• восстановлением, сном и планированием тренировок.\n"
                    ),
                ),
                Messages(
                    role=MessagesRole.USER,
                    content=(
                        "Контекст пользователя:\n"
                        f"{context}\n\n"
                        "Вопрос:\n"
                        f"{question}"
                    ),
                ),
            ],
            temperature=min(0.45, GIGACHAT_TEMPERATURE),
            max_tokens=900,
            model=GIGACHAT_MODEL,
        )

        def _chat_sync():
            last_err = None
            for attempt in range(1, GIGACHAT_RETRIES + 1):
                try:
                    # Новый SDK: модель в Chat(); старый — иногда ругался на аргумент model
                    try:
                        with GigaChat(credentials=self.token, verify_ssl_certs=False, timeout=GIGACHAT_TIMEOUT) as giga:
                            resp = giga.chat(qa_payload)
                            return resp.choices[0].message.content
                    except TypeError:
                        with GigaChat(credentials=self.token, verify_ssl_certs=False, timeout=GIGACHAT_TIMEOUT, model=GIGACHAT_MODEL) as giga:
                            resp = giga.chat(qa_payload)
                            return resp.choices[0].message.content
                except Exception as e:
                    last_err = e
                    if attempt == GIGACHAT_RETRIES:
                        raise
                    time.sleep(2 * attempt)
            raise last_err or RuntimeError("GigaChat call failed (Q&A)")

        txt = await to_thread(_chat_sync)
        txt = _strip_rpe(txt)
        return txt.strip()

    async def get_response(self, user_input: str) -> str:
        """
        Генерация программы по анкете, с добавлением рекомендаций по весам отягощения.
        """
        from asyncio import to_thread

        if user_input and user_input.strip():
            self.payload.messages.append(Messages(role=MessagesRole.USER, content=user_input))

        def _chat_sync():
            last_err = None
            for attempt in range(1, GIGACHAT_RETRIES + 1):
                try:
                    try:
                        with GigaChat(credentials=self.token, verify_ssl_certs=False, timeout=GIGACHAT_TIMEOUT) as giga:
                            response = giga.chat(self.payload)
                            return response.choices[0].message
                    except TypeError:
                        with GigaChat(credentials=self.token, verify_ssl_certs=False, timeout=GIGACHAT_TIMEOUT, model=GIGACHAT_MODEL) as giga:
                            response = giga.chat(self.payload)
                            return response.choices[0].message
                except Exception as e:
                    if attempt == GIGACHAT_RETRIES:
                        raise
                    time.sleep(2 * attempt)
            raise RuntimeError("GigaChat call failed (plan)")

        message = await to_thread(_chat_sync)
        self.payload.messages.append(message)

        cleaned = _strip_rpe(message.content)
        personalized = self._with_name_prefix(cleaned)

        # добавим рекомендации по весам
        try:
            personalized = self._annotate_plan_with_weights(personalized)
        except Exception:
            # не блокируем выдачу, если что-то пошло не так
            pass

        # сохраним в историю (для страницы «история диалога», если надо)
        history = self.user_data.get("history", [])
        if user_input and user_input.strip():
            history.append(("🧍 " + user_input, "🤖 " + personalized))
        else:
            history.append(("🧍 Запрос программы", "🤖 " + personalized))
        self.user_data["history"] = history
        # запомним последнюю программу для «Сохранить в файл»
        self.user_data["last_program"] = personalized
        save_user_data(self.user_id, self.user_data)

        return personalized

    # ---------- Вспомогательные ----------

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
        prefix = f"{name}, обработал твой запрос — вот что получилось ⬇️\n\n" if name else ""
        return prefix + (text or "")

    def _weight_context(self) -> Tuple[WUser, Dict[str, WHistory]]:
        """
        Собираем контекст для расчёта весов: анкета -> WUser, история -> WHistory.
        """
        d = self.user_data or {}
        phys = (d.get("physical_data") or {})
        # аккуратно приводим вес из строки
        weight_val = phys.get("weight")
        try:
            weight_kg = float(str(weight_val).replace(",", ".")) if weight_val is not None else None
        except Exception:
            weight_kg = None

        user = WUser(
            gender=(phys.get("gender") or "").lower() or None,
            age=_to_int(phys.get("age")),
            height_cm=_to_int(phys.get("height")),
            weight_kg=weight_kg,
            level=(phys.get("level") or "").lower() or None,
            target=(phys.get("target") or "").lower() or None,
        )

        # История по ключам упражнений
        hist_raw = d.get("lifts") or {}
        history: Dict[str, WHistory] = {}
        for k, rec in hist_raw.items():
            # Сохраняем только последний замер (и/или массив точек, если нужен)
            last_weight = rec.get("last_weight")
            reps = rec.get("reps")
            history[k] = WHistory(last_weight=last_weight, reps=reps, rir=None)
        return user, history

    def _annotate_plan_with_weights(self, text: str) -> str:
        """
        Находит строки упражнений формата:
          - Название — 3×12, отдых 90 сек.
        И добавляет хвост: ', рекомендация: ~Х кг'
        """
        user, history = self._weight_context()

        def _norm(s: str) -> str:
            s = (s or "").lower().replace("ё", "е")
            return re.sub(r"\s+", " ", s).strip()

        lines = (text or "").splitlines()
        out = []
        for ln in lines:
            m = re.search(r"^\s*-\s*(.+?)\s+—\s+(\d+\s*×\s*\d+(?:–\d+)?)", ln)
            if not m:
                out.append(ln)
                continue

            raw_name = m.group(1)
            key = base_key(_norm(raw_name)) or ""
            try:
                # если знаем упражнение — точная оценка с учётом типа снаряда
                if key:
                    rec = history.get(key)
                    w, _src = recommend_weight_for_exercise(raw_name, user, target_reps=10, history=rec)
                    val = int(w) if float(w).is_integer() else round(float(w), 1)
                else:
                    # общий fallback
                    w = recommend_start_weight(user, None, key=None, target_reps=10, exercise_name=raw_name)
                    val = int(w) if float(w).is_integer() else round(float(w), 1)
            except Exception:
                out.append(ln)
                continue

            if "рекомендац" not in ln:
                ln = f"{ln}, рекомендация: ~{val} кг"

            out.append(ln)

        return "\n".join(out)
