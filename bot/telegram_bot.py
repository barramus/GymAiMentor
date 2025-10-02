import os
import re
import time
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    Chat,
)
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.agent import FitnessAgent
from app.storage import (
    load_user_data,
    save_user_data,
)

__version__ = "tg-bot-1.4.0"
logger = logging.getLogger("bot.telegram_bot")

# -------------------- Клавиатуры --------------------

GOAL_MAPPING = {
    "🏃‍♂️ Похудеть": "похудение",
    "🏋️‍♂️ Набрать массу": "набор массы",
    "🧘 Поддерживать форму": "поддержание формы",
}
GOAL_KEYBOARD = ReplyKeyboardMarkup(
    [["🏋️‍♂️ Набрать массу", "🏃‍♂️ Похудеть", "🧘 Поддерживать форму"]],
    resize_keyboard=True,
    one_time_keyboard=True,
)

GENDER_CHOICES = ["👩 Женский", "👨 Мужской"]
GENDER_KEYBOARD = ReplyKeyboardMarkup(
    [GENDER_CHOICES],
    resize_keyboard=True,
    one_time_keyboard=True,
)

LEVEL_CHOICES = ["🚀 Начинающий", "🔥 Опытный"]

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["❓ Задать вопрос AI-тренеру"],
        ["📄 Другая программа"],
        ["💾 Сохранить в файл", "🔁 Начать заново"],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

# -------------------- Состояния --------------------

user_states: Dict[str, dict] = {}  # { user_id: {mode, step, data} }

# -------------------- Вспомогательные --------------------

def _normalize_name(raw: str) -> str:
    name = (raw or "").strip()
    return name[:80] if len(name) > 80 else name

def _strip_html(text: str) -> str:
    # <br>, <p> → переносы, убрать теги. Потом подчистить
    text = re.sub(r"\s*<br\s*/?>\s*", "\n", text, flags=re.I)
    text = re.sub(r"</?p\s*/?>", "\n", text, flags=re.I)

    # Убрать заголовочные #/##/... которые иногда присылает модель
    def _drop_hashes(line: str) -> str:
        if re.match(r"^\s*#{1,6}\s+", line):
            return re.sub(r"^\s*#{1,6}\s+", "", line)
        return line

    text = "\n".join(_drop_hashes(l) for l in text.splitlines())

    # Нормализуем пустые строки
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def _sanitize_for_markdown(text: str) -> str:
    """
    Безопасная обработка до Markdown: телега чувствительна к незакрытым символам.
    Простейшая «профилактика»: заменить «`» внутри обычного текста на апостроф, убрать нестабильные HTML-остатки.
    """
    text = _strip_html(text)
    # удалим «голые» < и >
    text = text.replace("<", "‹").replace(">", "›")
    # бэктики внутри фраз
    text = text.replace("`", "´")
    return text

async def _safe_send(chat: Chat, text: str, use_markdown: bool = True) -> None:
    """
    Отправка длинных сообщений с разбиением по лимиту телеги (~4096).
    Режем по абзацам, затем по строкам, затем «жёстко» если надо.
    """
    if not text:
        return

    max_len = 3800  # оставим запас под служебные символы
    chunks: List[str] = []

    def _split_long(s: str) -> List[str]:
        if len(s) <= max_len:
            return [s]
        parts = []
        buf = []
        cur = 0
        for para in s.split("\n\n"):
            if cur + len(para) + 2 <= max_len:
                buf.append(para)
                cur += len(para) + 2
            else:
                # если абзац сам по себе огромный — режем по строкам
                if buf:
                    parts.append("\n\n".join(buf))
                    buf = []
                    cur = 0
                if len(para) <= max_len:
                    parts.append(para)
                else:
                    # жёсткая нарезка
                    for i in range(0, len(para), max_len):
                        parts.append(para[i : i + max_len])
        if buf:
            parts.append("\n\n".join(buf))
        return parts

    for piece in _split_long(text):
        if not piece.strip():
            continue
        chunks.append(piece)

    for idx, chunk in enumerate(chunks, 1):
        try:
            await chat.send_message(
                chunk,
                parse_mode=ParseMode.MARKDOWN if use_markdown else None,
                disable_web_page_preview=True,
            )
        except Exception:
            # повтор — без Markdown
            await chat.send_message(chunk, disable_web_page_preview=True)
        # лёгкая пауза, чтобы телега не «схлопнула» пачку
        if idx < len(chunks):
            time.sleep(0.2)

def _gender_norm(t: str) -> Optional[str]:
    t = (t or "").strip().lower()
    if "жен" in t or "👩" in t:
        return "женский"
    if "муж" in t or "👨" in t:
        return "мужской"
    return None

async def _send_menu(chat: Chat):
    await chat.send_message("Что дальше? Выбери действие в меню ниже 👇", reply_markup=MAIN_KEYBOARD)

# -------------------- Основные действия --------------------

async def _save_last_program(update: Update, user_id: str):
    data = load_user_data(user_id)
    text = (data.get("last_program") or "").strip()
    if not text:
        await update.effective_chat.send_message(
            "Сначала сгенерируй программу (через /start или «📄 Другая программа»)."
        )
        await _send_menu(update.effective_chat)
        return

    ts = int(time.time())
    fname = f"program_{user_id}_{ts}.txt"
    out_path = Path("data") / "users" / fname
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")

    with open(out_path, "rb") as fh:
        await update.effective_chat.send_document(
            fh, filename=fname, caption="Файл с твоим последним ответом"
        )
    await _send_menu(update.effective_chat)

async def _generate_and_send_program(update: Update, user_id: str, agent: FitnessAgent, user_input: str = ""):
    chat = update.effective_chat
    thinking = await chat.send_message("Готовлю программу… это займёт пару секунд 🧠")

    try:
        plan = await agent.get_program(user_input)
        plan = _sanitize_for_markdown(plan)
    except Exception:
        logger.exception("Ошибка генерации программы")
        await thinking.edit_text("Не удалось сгенерировать программу. Попробуй ещё раз.")
        return

    # Сохраняем только как «последняя программа», чтобы «Сохранить в файл» работало всегда
    data = load_user_data(user_id)
    data["last_program"] = plan
    save_user_data(user_id, data)

    await thinking.delete()
    await _safe_send(chat, plan, use_markdown=True)
    await _send_menu(chat)

# -------------------- Public handlers --------------------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    chat = update.effective_chat
    user_id = str(update.effective_user.id)
    text = (update.message.text or "").strip()

    data = load_user_data(user_id)
    phys = data.get("physical_data") or {}
    name = phys.get("name")
    completed = bool(data.get("physical_data_completed"))
    state = user_states.get(user_id, {"mode": None, "step": 0, "data": {}})

    # --- Кнопки из главного меню (НЕ трактуем как вопросы!) ---
    if text == "💾 Сохранить в файл":
        await _save_last_program(update, user_id)
        return

    if text == "📄 Другая программа":
        if not completed:
            await chat.send_message("Сначала пройдём мини-опрос, чтобы программа была персональной.")
            await _send_menu(chat)
            return
        agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
        await _generate_and_send_program(update, user_id, agent, user_input="")
        return

    if text == "🔁 Начать заново":
        data["physical_data"] = {"name": name}
        data["physical_data_completed"] = False
        save_user_data(user_id, data)
        user_states[user_id] = {"mode": "awaiting_goal", "step": 0, "data": {}}
        await chat.send_message("Начнём заново! Выбери цель ⬇️", reply_markup=GOAL_KEYBOARD)
        return

    if text == "❓ Задать вопрос AI-тренеру":
        user_states[user_id] = {"mode": "qa", "step": 0, "data": {}}
        await chat.send_message("Готов ответить на твой вопрос о тренировках или питании 👇")
        return

    # --- Режим QA: краткие ответы, с учётом анкеты ---
    if state.get("mode") == "qa":
        agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
        thinking = await chat.send_message("Думаю над ответом на твой запрос…")
        try:
            ans = await agent.get_answer(text)  # внутри агент подтянет анкету пользователя
            ans = _sanitize_for_markdown(ans)
        except Exception:
            logger.exception("Ошибка ответа QA")
            await thinking.edit_text("Не получилось ответить. Попробуй переформулировать вопрос.")
            return

        await thinking.delete()
        await _safe_send(chat, ans, use_markdown=True)
        return

    # --- Ветка опроса ---
    # Если пользователь только что написал что-то после /start — выставим состояние
    if not completed and state.get("mode") is None:
        user_states[user_id] = {"mode": "awaiting_goal", "step": 0, "data": {}}
        if name:
            await chat.send_message(f"{name}, выбери свою цель тренировок ⬇️", reply_markup=GOAL_KEYBOARD)
        else:
            await chat.send_message("Как тебя зовут?")
            user_states[user_id] = {"mode": "awaiting_name", "step": 0, "data": {}}
        return

    if state.get("mode") == "awaiting_name":
        if not text:
            await chat.send_message("Пожалуйста, напиши своё имя одним сообщением.")
            return
        name = _normalize_name(text)
        phys["name"] = name
        data["physical_data"] = phys
        save_user_data(user_id, data)
        user_states[user_id] = {"mode": "awaiting_goal", "step": 0, "data": {}}
        await chat.send_message(f"{name}, выбери свою цель тренировок ⬇️", reply_markup=GOAL_KEYBOARD)
        return

    if text in GOAL_MAPPING and state.get("mode") in (None, "awaiting_goal"):
        user_states[user_id] = {"mode": "awaiting_gender", "step": 0, "data": {"target": GOAL_MAPPING[text]}}
        await chat.send_message("Укажи свой пол:", reply_markup=GENDER_KEYBOARD)
        return

    if state.get("mode") == "awaiting_gender":
        g = _gender_norm(text)
        if not g:
            await chat.send_message("Пожалуйста, выбери пол кнопкой ниже:", reply_markup=GENDER_KEYBOARD)
            return
        st = state["data"]; st["gender"] = g
        user_states[user_id] = {"mode": "survey", "step": 1, "data": st}
        await chat.send_message("Сколько тебе лет?")
        return

    questions = [
        ("age", "Сколько тебе лет?"),
        ("height", "Твой рост в сантиметрах?"),
        ("weight", "Твой текущий вес в килограммах?"),
        ("goal", "Желаемый вес в килограммах?"),
        ("restrictions", "Есть ли ограничения по здоровью или предпочтения в тренировках?"),
        ("schedule", "Сколько раз в неделю можешь посещать тренажёрный зал?"),
    ]

    if state.get("mode") == "survey":
        # Записываем ответ на предыдущий вопрос
        idx = state["step"]
        if idx > 0:
            prev_key = questions[idx - 1][0]
            state["data"][prev_key] = text

        if idx + 1 <= len(questions):
            next_q = questions[idx][1]
            user_states[user_id] = {"mode": "survey", "step": idx + 1, "data": state["data"]}
            await chat.send_message(next_q)
            return

        # Переходим к выбору уровня
        user_states[user_id] = {"mode": "awaiting_level", "step": 0, "data": state["data"]}
        await chat.send_message(
            "Выбери свой уровень подготовки:",
            reply_markup=ReplyKeyboardMarkup([LEVEL_CHOICES], resize_keyboard=True, one_time_keyboard=True),
        )
        return

    if state.get("mode") == "awaiting_level":
        if text not in LEVEL_CHOICES:
            await chat.send_message(
                "Пожалуйста, выбери уровень кнопкой ниже:",
                reply_markup=ReplyKeyboardMarkup([LEVEL_CHOICES], resize_keyboard=True, one_time_keyboard=True),
            )
            return

        level = "начинающий" if "Начинающий" in text else "опытный"
        st = state["data"]; st["level"] = level

        # Сохраняем анкету
        base_phys = data.get("physical_data", {}) or {}
        base_phys.update(st)
        data["physical_data"] = base_phys
        data["physical_data_completed"] = True
        save_user_data(user_id, data)

        await chat.send_message("Спасибо! Формирую твою персональную программу…")

        agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
        await _generate_and_send_program(update, user_id, agent, user_input="")

        # сбрасываем временное состояние
        user_states.pop(user_id, None)
        return

    # --- Фоллбек: если анкета заполнена, трактуем как «свободный вопрос» в QA ---
    if completed:
        user_states[user_id] = {"mode": "qa", "step": 0, "data": {}}
        agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
        thinking = await chat.send_message("Думаю над ответом на твой запрос…")
        try:
            ans = await agent.get_answer(text)
            ans = _sanitize_for_markdown(ans)
        except Exception:
            logger.exception("Ошибка ответа QA (fallback)")
            await thinking.edit_text("Не получилось ответить. Попробуй ещё раз.")
            return
        await thinking.delete()
        await _safe_send(chat, ans, use_markdown=True)
        return

    # Если ничего не подошло — повторно попросим имя/цель
    user_states[user_id] = {"mode": "awaiting_name", "step": 0, "data": {}}
    await chat.send_message("Как тебя зовут?")

__all__ = ["handle_message", "GOAL_KEYBOARD", "MAIN_KEYBOARD", "user_states"]
