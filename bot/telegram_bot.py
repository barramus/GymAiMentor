LAST_REPLIES: dict[str, str] = {}

import os
import re
import time
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict

from telegram import Update, ReplyKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.agent import FitnessAgent
from app.storage import (
    load_user_data,
    save_user_data,
    set_last_reply,
    get_last_reply,
)
from app.weights import base_key  # оставили на будущее

__version__ = "tg-bot-1.4.0"
logger = logging.getLogger("bot.telegram_bot")

user_states: Dict[str, dict] = {}

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

START_KEYBOARD = ReplyKeyboardMarkup([["/start"]], resize_keyboard=True)

# Главная панель (показываем после первой программы)
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["❓ Задать вопрос AI-тренеру"],
        ["📄 Другая программа"],
        ["💾 Сохранить в файл", "🔁 Начать заново"],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

def sanitize_for_tg(text: str) -> str:
    text = re.sub(r"\s*<br\s*/?>\s*", "\n", text)
    text = re.sub(r"</?p\s*/?>", "\n", text)
    return text.strip()

async def _send_main_menu(update: Update):
    await update.effective_chat.send_message(
        "Что дальше? Выбери действие в меню ниже 👇",
        reply_markup=MAIN_KEYBOARD,
    )

async def _send_program(update: Update, user_id: str, text: str):
    await update.effective_chat.send_message(
        text,
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True,
    )
    await _send_main_menu(update)

async def _save_last_to_file(update: Update, user_id: str):
    text = LAST_REPLIES.get(user_id) or get_last_reply(user_id) or ""
    if not text.strip():
        await update.message.reply_text("Пока нечего сохранять. Сначала запроси программу или задай вопрос.")
        return
    ts = int(time.time())
    fname = f"program_{user_id}_{ts}.txt"
    out_path = Path("data") / "users" / fname
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    with open(out_path, "rb") as fh:
        await update.effective_chat.send_document(fh, filename=fname, caption="Файл с твоим последним ответом")

def _normalize_name(raw: str) -> str:
    name = (raw or "").strip()
    return name[:80] if len(name) > 80 else name

def normalize_gender(text: str) -> Optional[str]:
    t = (text or "").strip().lower()
    if "жен" in t or "👩" in t:
        return "женский"
    if "муж" in t or "👨" in t:
        return "мужской"
    return None

async def _ask_goal_with_name(update: Update, name: str):
    await update.message.reply_text(f"{name}, выбери свою цель тренировок ⬇️", reply_markup=GOAL_KEYBOARD)

# ----------------- Основная логика -----------------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user_id = str(update.effective_user.id)
    text = (update.message.text or "").strip()

    user_data = load_user_data(user_id)
    physical_data = user_data.get("physical_data", {}) or {}
    name = physical_data.get("name")
    completed = bool(user_data.get("physical_data_completed"))

    state = user_states.get(user_id, {"mode": None, "step": 0, "data": {}})

    # ====== КНОПКА: Вопрос AI-тренеру ======
    if text == "❓ Задать вопрос AI-тренеру":
        user_states[user_id] = {"mode": "qa", "step": 0, "data": {}}
        await update.message.reply_text("Готов ответить на твой вопрос. Напиши его ниже 👇")
        return

    # Режим Q&A — даём ответ, НЕ генерируем программу
    if state.get("mode") == "qa":
        thinking = await update.message.reply_text("Думаю над ответом на твой запрос…")
        agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
        try:
            answer = await agent.get_answer(text)
        finally:
            try:
                await thinking.delete()
            except Exception:
                pass
        answer = sanitize_for_tg(answer)
        user_data.setdefault("history", []).append(("🧍 " + text, "🤖 " + answer))
        save_user_data(user_id, user_data)
        LAST_REPLIES[user_id] = answer
        set_last_reply(user_id, answer)
        await update.message.reply_text(answer, parse_mode=ParseMode.MARKDOWN)
        return

    # ====== КНОПКА: Сохранить в файл ======
    if text == "💾 Сохранить в файл":
        await _save_last_to_file(update, user_id)
        return

    # ====== КНОПКА: Другая программа ======
    if text == "📄 Другая программа":
        # только выдача другой программы по текущей анкете
        user_states[user_id] = {"mode": None, "step": 0, "data": {}}
        progress = await update.message.reply_text("Формирую для тебя другую программу…")
        agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
        try:
            plan = await agent.get_response("")
        finally:
            try:
                await progress.delete()
            except Exception:
                pass
        plan = sanitize_for_tg(plan)
        await _send_program(update, user_id, plan)
        LAST_REPLIES[user_id] = plan
        set_last_reply(user_id, plan)
        return

    # ====== КНОПКА: Начать заново ======
    if text == "🔁 Начать заново":
        # всегда полный опрос заново, начиная с выбора цели
        user_states[user_id] = {"mode": None, "step": 0, "data": {}}
        base_name = (user_data.get("physical_data") or {}).get("name")
        user_data["physical_data"] = {"name": base_name}
        user_data["physical_data_completed"] = False
        user_data["history"] = []
        save_user_data(user_id, user_data)

        await update.message.reply_text("Ок, начинаем заново.")
        if base_name:
            await _ask_goal_with_name(update, base_name)
        else:
            user_states[user_id] = {"mode": "awaiting_name", "step": 0, "data": {}}
            await update.message.reply_text("Как тебя зовут?")
        return

    # ====== Обработка анкеты ======
    if text in GOAL_MAPPING:
        user_states[user_id] = {"mode": "awaiting_gender", "step": 0, "data": {"target": GOAL_MAPPING[text]}}
        await update.message.reply_text("Укажи свой пол:", reply_markup=GENDER_KEYBOARD)
        return

    if state.get("mode") == "awaiting_name":
        if not text:
            await update.message.reply_text("Пожалуйста, напиши своё имя одним сообщением.")
            return
        name = _normalize_name(text)
        physical_data["name"] = name
        user_data["physical_data"] = physical_data
        save_user_data(user_id, user_data)
        user_states[user_id] = {"mode": None, "step": 0, "data": {}}
        await _ask_goal_with_name(update, name)
        return

    if state.get("mode") == "awaiting_gender":
        g = normalize_gender(text)
        if not g:
            await update.message.reply_text("Пожалуйста, выбери пол кнопкой ниже:", reply_markup=GENDER_KEYBOARD)
            return
        state["data"]["gender"] = g
        user_states[user_id] = {"mode": "survey", "step": 2, "data": state["data"]}
        await update.message.reply_text("Сколько тебе лет?")
        return

    questions = [
        ("age", "Сколько тебе лет?"),
        ("height", "Твой рост в сантиметрах?"),
        ("weight", "Твой текущий вес в килограммах?"),
        ("goal", "Желаемый вес в килограммах?"),
        ("restrictions", "Есть ли ограничения по здоровью или предпочтения в тренировках?"),
        ("schedule", "Сколько раз в неделю можешь посещать тренажерный зал?"),
    ]

    if not completed and state.get("mode") == "survey":
        if state["step"] > 1:
            prev_key = questions[state["step"] - 2][0]
            state["data"][prev_key] = text
        if state["step"] <= len(questions):
            next_idx = state["step"] - 1
            _next_key, next_text = questions[next_idx]
            user_states[user_id] = {"mode": "survey", "step": state["step"] + 1, "data": state["data"]}
            await update.message.reply_text(next_text)
            return

        user_states[user_id] = {"mode": "awaiting_level", "step": 0, "data": state["data"]}
        await update.message.reply_text(
            "Выбери свой уровень подготовки:",
            reply_markup=ReplyKeyboardMarkup([LEVEL_CHOICES], resize_keyboard=True, one_time_keyboard=True),
        )
        return

    if state.get("mode") == "awaiting_level":
        if text not in LEVEL_CHOICES:
            await update.message.reply_text(
                "Пожалуйста, выбери уровень кнопкой ниже:",
                reply_markup=ReplyKeyboardMarkup([LEVEL_CHOICES], resize_keyboard=True, one_time_keyboard=True),
            )
            return
        level = "начинающий" if "Начинающий" in text else "опытный"
        state["data"]["level"] = level

        finished_data = state["data"]
        user_states.pop(user_id, None)

        base_physical = user_data.get("physical_data", {}) or {}
        base_physical.update(finished_data)
        user_data["physical_data"] = base_physical
        user_data["physical_data_completed"] = True
        user_data.setdefault("history", [])
        save_user_data(user_id, user_data)

        progress = await update.message.reply_text("Спасибо! Формирую твою персональную программу…")

        agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
        try:
            plan = await agent.get_response("")
        finally:
            try:
                await progress.delete()
            except Exception:
                pass

        plan = sanitize_for_tg(plan)
        await _send_program(update, user_id, plan)
        LAST_REPLIES[user_id] = plan
        set_last_reply(user_id, plan)
        return

    # ------ Свободный ввод (вне анкеты и не в Q&A) -> универсальный ответ (учитывает анкету) ------
    agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
    thinking = await update.message.reply_text("Думаю над ответом на твой запрос…")
    try:
        reply = await agent.get_response(text)
    finally:
        try:
            await thinking.delete()
        except Exception:
            pass

    reply = sanitize_for_tg(reply)
    user_data.setdefault("history", []).append(("🧍 " + text, "🤖 " + reply))
    save_user_data(user_id, user_data)
    LAST_REPLIES[user_id] = reply
    set_last_reply(user_id, reply)

    await update.message.reply_text(reply, parse_mode=ParseMode.MARKDOWN)
