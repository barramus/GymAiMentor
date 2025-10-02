from __future__ import annotations

import os
import re
import time
import logging
from pathlib import Path
from typing import Optional, Dict, List

from telegram import Update, ReplyKeyboardMarkup, Chat
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.agent import FitnessAgent
from app.storage import load_user_data, save_user_data, set_last_reply, get_last_reply

logger = logging.getLogger("bot.telegram_bot")

# Последний отправленный пользователю длинный ответ (чтобы сохранить в файл)
LAST_REPLIES: dict[str, str] = {}
# Память состояний опроса
user_states: Dict[str, dict] = {}

# ========================
#         КЛАВИАТУРЫ
# ========================

# Цели: используем мужские эмодзи, но логика ниже не зависит от эмодзи/регистра
GOAL_KEYBOARD = ReplyKeyboardMarkup(
    [["🏋️‍♂️ Набрать массу", "🏃‍♂️ Похудеть", "🧘 Поддерживать форму"]],
    resize_keyboard=True,
    one_time_keyboard=True,
)

GENDER_CHOICES = ["👩 Женский", "👨 Мужской"]
GENDER_KEYBOARD = ReplyKeyboardMarkup([GENDER_CHOICES], resize_keyboard=True, one_time_keyboard=True)

LEVEL_CHOICES = ["🚀 Начинающий", "🔥 Опытный"]
LEVEL_KEYBOARD = ReplyKeyboardMarkup([LEVEL_CHOICES], resize_keyboard=True, one_time_keyboard=True)

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["❓ Задать вопрос AI-тренеру"],
        ["📄 Другая программа"],
        ["💾 Сохранить в файл", "🔁 Начать заново"],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

# ========================
#        ВСПОМОГАЛКИ
# ========================

def _sanitize_for_tg(text: str) -> str:
    """Немного чистим и убираем заголовочные #, чтобы не ломать Markdown в Telegram."""
    out = text or ""
    # Уберём префиксы заголовков (#, ##, ...) — Марина просила без них
    out = re.sub(r"^\s*#{1,6}\s*", "", out, flags=re.MULTILINE)
    # HTML-разрывы строк в \n
    out = re.sub(r"\s*<br\s*/?>\s*", "\n", out, flags=re.IGNORECASE)
    out = re.sub(r"</?p\s*/?>", "\n", out, flags=re.IGNORECASE)
    # Уберём тройные переносы
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()

def _split_for_telegram(text: str, max_len: int = 3500) -> List[str]:
    """Делим длинный текст на части: сначала по Дням, затем по пустым строкам, иначе по длине."""
    if len(text) <= max_len:
        return [text]
    parts: List[str] = []
    remaining = text
    while len(remaining) > max_len:
        cut = remaining.rfind("\n\nДень", 0, max_len)
        if cut < 0:
            cut = remaining.rfind("\n\n", 0, max_len)
        if cut < 0:
            cut = max_len
        parts.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        parts.append(remaining)
    return parts

async def _safe_send(chat: Chat, text: str, use_markdown: bool = True):
    """
    Безопасно шлём текст: если слишком длинный — бьём на части,
    если Markdown ломается — отправляем plain.
    """
    text = text.strip()
    for chunk in _split_for_telegram(text, max_len=3500):
        try:
            if use_markdown:
                await chat.send_message(chunk, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
            else:
                await chat.send_message(chunk, disable_web_page_preview=True)
        except Exception as e:
            logger.error("Markdown send failed (%s). Fallback to plain text.", e)
            await chat.send_message(chunk, disable_web_page_preview=True)

async def _send_main_menu(update: Update):
    await update.effective_chat.send_message(
        "Что дальше? Выбери действие в меню ниже 👇",
        reply_markup=MAIN_KEYBOARD,
    )

async def _save_last_to_file(update: Update, user_id: str):
    """
    Сохраняем ПОСЛЕДНЮЮ программу/ответ, показанный пользователю, в файл .txt.
    Кнопка работает только для сохранения.
    """
    text = LAST_REPLIES.get(user_id) or get_last_reply(user_id) or ""
    if not text.strip():
        await update.effective_chat.send_message(
            "Сначала сгенерируй программу (кнопкой «📄 Другая программа»)."
        )
        return
    ts = int(time.time())
    fname = f"program_{user_id}_{ts}.txt"
    out_path = Path("data/users") / fname
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    with open(out_path, "rb") as fh:
        await update.effective_chat.send_document(
            fh, filename=fname, caption="Файл с твоей последней программой"
        )

def _normalize_name(raw: str) -> str:
    name = (raw or "").strip()
    return name[:80] if len(name) > 80 else name

def _normalize_gender(text: str) -> Optional[str]:
    t = (text or "").lower()
    if "жен" in t or "👩" in t:
        return "женский"
    if "муж" in t or "👨" in t:
        return "мужской"
    return None

def _parse_goal(text: str) -> Optional[str]:
    """
    Разбираем цель из кнопки/сообщения, НЕ завися от эмодзи/регистра.
    Возвращаем одно из: 'похудение' | 'набор массы' | 'поддержание формы'
    """
    t = (text or "").lower()
    # Удалим всё не буквенно-цифровое, чтобы не мешали эмодзи
    t = re.sub(r"[^\w\sёЁа-яА-Я]", " ", t)
    if "похуд" in t:
        return "похудение"
    if "набр" in t or "мас" in t:
        return "набор массы"
    if "поддерж" in t or "форм" in t:
        return "поддержание формы"
    return None

# ========================
#      ОСНОВНОЙ HANDLER
# ========================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user_id = str(update.effective_user.id)
    text = (update.message.text or "").strip()

    # Текущие данные пользователя
    data = load_user_data(user_id)
    phys = data.get("physical_data") or {}
    name = phys.get("name")
    completed = bool(data.get("physical_data_completed"))
    state = user_states.get(user_id) or {"mode": None, "step": 0, "data": {}}

    # ---------- ЖЁСТКАЯ обработка кнопок (не попадают в Q&A) ----------
    if text == "💾 Сохранить в файл":
        await _save_last_to_file(update, user_id)
        return

    if text == "📄 Другая программа":
        await update.message.reply_text("Думаю над ответом на твой запрос…")
        try:
            agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
            plan = await agent.get_program("")  # генерируем только программу по анкете
            plan = _sanitize_for_tg(plan)
            LAST_REPLIES[user_id] = plan
            set_last_reply(user_id, plan)
            await _safe_send(update.effective_chat, plan, use_markdown=True)
            await _send_main_menu(update)
        except Exception:
            logger.exception("Ошибка генерации альтернативной программы")
            await update.message.reply_text("Не получилось сгенерировать программу. Попробуй ещё раз.")
        return

    if text == "🔁 Начать заново":
        # Полный сброс анкеты (имя оставляем, если было) — старт с выбора цели
        data["physical_data"] = {"name": name}
        data["physical_data_completed"] = False
        save_user_data(user_id, data)
        user_states[user_id] = {"mode": None, "step": 0, "data": {}}
        await update.message.reply_text(
            "Начинаем заново! Выбери свою цель тренировок ⬇️",
            reply_markup=GOAL_KEYBOARD,
        )
        return

    # ---------- Если анкета не завершена и нет активного режима — начинаем/продолжаем опрос ----------
    if not completed and state.get("mode") is None:
        if not name:
            user_states[user_id] = {"mode": "awaiting_name", "step": 0, "data": {}}
            await update.message.reply_text("Как тебя зовут?")
            return
        await update.message.reply_text(
            f"{name}, выбери свою цель тренировок ⬇️",
            reply_markup=GOAL_KEYBOARD,
        )
        return

    # ---------- Вход в Q&A ----------
    if text == "❓ Задать вопрос AI-тренеру":
        user_states[user_id] = {"mode": "qa", "step": 0, "data": {}}
        await update.message.reply_text("Задай вопрос по тренировкам/питанию 👇")
        return

    # ---------- Режим Q&A ----------
    if state.get("mode") == "qa":
        agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
        answer = await agent.get_answer(text)
        answer = _sanitize_for_tg(answer)
        LAST_REPLIES[user_id] = answer
        set_last_reply(user_id, answer)
        await _safe_send(update.effective_chat, answer, use_markdown=True)
        return

    # ---------- Анкета ----------
    if state.get("mode") == "awaiting_name":
        if not text:
            await update.message.reply_text("Напиши, пожалуйста, имя.")
            return
        phys["name"] = _normalize_name(text)
        data["physical_data"] = phys
        save_user_data(user_id, data)
        user_states[user_id] = {"mode": None, "step": 0, "data": {}}
        await update.message.reply_text(
            f"{phys['name']}, выбери свою цель тренировок ⬇️",
            reply_markup=GOAL_KEYBOARD,
        )
        return

    # Цель (разбор текста без зависимости от эмодзи/регистра)
    goal = _parse_goal(text)
    if goal:
        user_states[user_id] = {"mode": "awaiting_gender", "step": 0, "data": {"target": goal}}
        await update.message.reply_text("Укажи свой пол:", reply_markup=GENDER_KEYBOARD)
        return

    # Пол
    if state.get("mode") == "awaiting_gender":
        g = _normalize_gender(text)
        if not g:
            await update.message.reply_text("Пожалуйста, выбери пол кнопкой ниже:", reply_markup=GENDER_KEYBOARD)
            return
        st = {"mode": "survey", "step": 2, "data": {**state.get("data", {}), "gender": g}}
        user_states[user_id] = st
        await update.message.reply_text("Сколько тебе лет?")
        return

    # Последовательность вопросов анкеты
    questions = [
        ("age", "Сколько тебе лет?"),
        ("height", "Твой рост в сантиметрах?"),
        ("weight", "Твой текущий вес в килограммах?"),
        ("goal", "Желаемый вес в килограммах?"),
        ("restrictions", "Есть ли ограничения по здоровью или предпочтения в тренировках?"),
        ("schedule", "Сколько раз в неделю можешь посещать тренажёрный зал?"),
    ]

    if state.get("mode") == "survey":
        # Сохраняем предыдущий ответ
        if state["step"] > 1:
            prev_key = questions[state["step"] - 2][0]
            state["data"][prev_key] = text
        # Задаём следующий вопрос
        if state["step"] <= len(questions):
            idx = state["step"] - 1
            _, q_text = questions[idx]
            user_states[user_id] = {"mode": "survey", "step": state["step"] + 1, "data": state["data"]}
            await update.message.reply_text(q_text)
            return
        # Переходим к выбору уровня
        user_states[user_id] = {"mode": "awaiting_level", "step": 0, "data": state["data"]}
        await update.message.reply_text("Выбери свой уровень подготовки:", reply_markup=LEVEL_KEYBOARD)
        return

    if state.get("mode") == "awaiting_level":
        if text not in LEVEL_CHOICES:
            await update.message.reply_text("Пожалуйста, выбери уровень кнопкой ниже:", reply_markup=LEVEL_KEYBOARD)
            return
        level = "опытный" if "Опыт" in text or "🔥" in text else "новичок"
        finished = {**state.get("data", {}), "level": level}
        user_states.pop(user_id, None)

        base = data.get("physical_data") or {}
        base.update(finished)
        data["physical_data"] = base
        data["physical_data_completed"] = True
        save_user_data(user_id, data)

        await update.message.reply_text("Спасибо! Формирую твою персональную программу…")

        agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
        try:
            plan = await agent.get_program("")
        except Exception:
            logger.exception("Ошибка генерации программы")
            await update.message.reply_text("Не удалось сгенерировать программу. Попробуй ещё раз.")
            return

        plan = _sanitize_for_tg(plan)
        LAST_REPLIES[user_id] = plan
        set_last_reply(user_id, plan)
        await _safe_send(update.effective_chat, plan, use_markdown=True)
        await _send_main_menu(update)
        return

    # Если по какой-то причине анкета ещё не начата — стартуем с имени
    if not completed:
        user_states[user_id] = {"mode": "awaiting_name", "step": 0, "data": {}}
        await update.message.reply_text("Как тебя зовут?")
        return

    # ---------- Остальное трактуем как пожелания к программе ----------
    agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
    try:
        plan = await agent.get_program(text)
    except Exception:
        logger.exception("Ошибка генерации программы (с пожеланиями)")
        await update.message.reply_text("Не получилось сгенерировать программу. Попробуй ещё раз.")
        return

    plan = _sanitize_for_tg(plan)
    LAST_REPLIES[user_id] = plan
    set_last_reply(user_id, plan)
    await _safe_send(update.effective_chat, plan, use_markdown=True)
    await _send_main_menu(update)
