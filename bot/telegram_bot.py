# bot/telegram_bot.py

from __future__ import annotations

import os
import re
import time
import logging
from pathlib import Path
from typing import Optional, Dict

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest
from telegram.constants import ParseMode

from app.agent import FitnessAgent
from app.storage import (
    load_user_data,
    save_user_data,
    set_last_reply,
    get_last_reply,
    set_last_program,
    get_last_program,
)

__version__ = "tg-bot-1.4.0"
logger = logging.getLogger("bot.telegram_bot")

# ---------- Состояния и клавиатуры ----------

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
GENDER_KEYBOARD = ReplyKeyboardMarkup([GENDER_CHOICES], resize_keyboard=True, one_time_keyboard=True)

LEVEL_CHOICES = ["🚀 Начинающий", "🔥 Опытный"]
LEVEL_KEYBOARD = ReplyKeyboardMarkup([LEVEL_CHOICES], resize_keyboard=True, one_time_keyboard=True)

# Главная панель показывается только после первой сгенерированной программы
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["❓ Задать вопрос AI-тренеру"],
        ["📋 Другая программа"],
        ["💾 Сохранить в файл"],
        ["🔁 Начать заново"],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

START_KEYBOARD = ReplyKeyboardMarkup([["/start"]], resize_keyboard=True)

# ---------- Утилиты ----------

def _normalize_name(raw: str) -> str:
    name = (raw or "").strip()
    return name[:80] if len(name) > 80 else name

def _sanitize_incoming(text: str) -> str:
    return (text or "").strip()

def sanitize_for_tg(text: str) -> str:
    """Приводим HTML к переносам, чистим лишнее. Markdown не трогаем, чтобы не сломать."""
    t = text or ""
    t = re.sub(r"\s*<br\s*/?>\s*", "\n", t, flags=re.IGNORECASE)
    t = re.sub(r"</?p\s*/?>", "\n", t, flags=re.IGNORECASE)
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = t.strip()
    return t

async def _safe_send(chat, text: str, use_markdown: bool = True):
    """Шлём Markdown, а при ошибке Telegram — plain text."""
    if not text:
        return
    try:
        if use_markdown:
            await chat.send_message(text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
        else:
            await chat.send_message(text, disable_web_page_preview=True)
    except BadRequest:
        await chat.send_message(text, disable_web_page_preview=True)

async def _send_main_menu_if_enabled(update: Update, enabled: bool):
    if not enabled:
        return
    await update.effective_chat.send_message("Что дальше? Выбери действие в меню ниже 👇", reply_markup=MAIN_KEYBOARD)

def _menu_enabled(user_data: dict) -> bool:
    return bool(user_data.get("menu_enabled"))

def _enable_menu(user_data: dict, value: bool = True):
    user_data["menu_enabled"] = bool(value)

def _normalize_gender(text: str) -> Optional[str]:
    t = (text or "").strip().lower()
    if "жен" in t or "👩" in t:
        return "женский"
    if "муж" in t or "👨" in t:
        return "мужской"
    return None

async def _ask_goal_with_name(update: Update, name: str):
    await update.message.reply_text(f"{name}, выбери свою цель тренировок ⬇️", reply_markup=GOAL_KEYBOARD)

# ---------- Основные обработчики ----------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user_id = str(update.effective_user.id)
    text = _sanitize_incoming(update.message.text)

    user_data = load_user_data(user_id)
    physical_data = user_data.get("physical_data", {}) or {}
    name = physical_data.get("name")
    completed = bool(user_data.get("physical_data_completed"))
    state = user_states.get(user_id, {"mode": None, "step": 0, "data": {}})

    # ====== КНОПКИ ГЛАВНОГО МЕНЮ (не воспринимать как вопросы) ======

    # 1) Вход в Q&A-режим
    if text == "❓ Задать вопрос AI-тренеру":
        user_states[user_id] = {"mode": "qa", "step": 0, "data": {}}
        await update.message.reply_text("Пиши вопрос по тренировкам/нагрузкам/питанию 👇", reply_markup=MAIN_KEYBOARD)
        return

    # Если уже в Q&A — отвечаем на произвольный текст как на вопрос
    if state.get("mode") == "qa" and text not in {"📋 Другая программа", "💾 Сохранить в файл", "🔁 Начать заново"}:
        agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
        thinking = await update.message.reply_text("Думаю над ответом…")
        try:
            answer = await agent.get_answer(text)
        finally:
            try:
                await thinking.delete()
            except Exception:
                pass
        # сохраняем историю (опционально)
        user_data.setdefault("history", []).append(("🧍 " + text, "🤖 " + answer))
        save_user_data(user_id, user_data)
        await _safe_send(update.effective_chat, answer, use_markdown=True)
        return

    # 2) Другая программа — только генерация нового плана по анкете
    if text == "📋 Другая программа":
        if not completed:
            await update.message.reply_text("Сначала заполни анкету и получи первую программу через /start.")
            return
        agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
        thinking = await update.message.reply_text("Думаю над ответом на твой запрос…")
        try:
            plan = await agent.get_response("")
            plan = sanitize_for_tg(plan)
        finally:
            try:
                await thinking.delete()
            except Exception:
                pass
        set_last_reply(user_id, plan)          # последняя выдача
        set_last_program(user_id, plan)        # последняя программа (для сохранения в файл)
        await _safe_send(update.effective_chat, plan, use_markdown=True)
        await _send_main_menu_if_enabled(update, _menu_enabled(user_data))
        return

    # 3) Сохранить в файл — только последняя программа
    if text == "💾 Сохранить в файл":
        plan = get_last_program(user_id) or ""
        if not plan.strip():
            await update.message.reply_text("Сначала сгенерируй программу (через /start или «📋 Другая программа»).")
            return
        ts = int(time.time())
        fname = f"program_{user_id}_{ts}.txt"
        out_path = Path("data") / "users" / fname
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(plan, encoding="utf-8")
        with open(out_path, "rb") as fh:
            await update.effective_chat.send_document(fh, filename=fname, caption="Файл с твоей последней программой")
        return

    # 4) Начать заново — только перезапуск анкеты
    if text == "🔁 Начать заново":
        user_data["physical_data"] = {"name": name}
        user_data["physical_data_completed"] = False
        user_data["last_program"] = ""
        _enable_menu(user_data, False)  # панель спрятана до первой новой программы
        save_user_data(user_id, user_data)
        await update.message.reply_text("Начнём заново! Как тебя зовут?")
        user_states[user_id] = {"mode": "awaiting_name", "step": 0, "data": {}}
        return

    # ====== ШАГИ АНКЕТЫ ======

    # Выбор цели из клавиатуры
    if text in GOAL_MAPPING:
        user_states[user_id] = {"mode": "awaiting_gender", "step": 0, "data": {"target": GOAL_MAPPING[text]}}
        await update.message.reply_text("Укажи свой пол:", reply_markup=GENDER_KEYBOARD)
        return

    # Ожидание имени
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

    # Ожидание пола
    if state.get("mode") == "awaiting_gender":
        g = _normalize_gender(text)
        if not g:
            await update.message.reply_text("Пожалуйста, выбери пол кнопкой ниже:", reply_markup=GENDER_KEYBOARD)
            return
        state["data"]["gender"] = g
        user_states[user_id] = {"mode": "survey", "step": 2, "data": state["data"]}
        await update.message.reply_text("Сколько тебе лет?")
        return

    # Вопросы анкеты
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

    # Выбор уровня
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
        save_user_data(user_id, user_data)

        # Первая программа
        agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
        thinking = await update.message.reply_text("Спасибо! Формирую твою персональную программу…")
        try:
            plan = await agent.get_response("")
            plan = sanitize_for_tg(plan)
        finally:
            try:
                await thinking.delete()
            except Exception:
                pass

        set_last_reply(user_id, plan)
        set_last_program(user_id, plan)
        _enable_menu(user_data, True)  # теперь показываем общую панель
        save_user_data(user_id, user_data)

        await _safe_send(update.effective_chat, plan, use_markdown=True)
        await _send_main_menu_if_enabled(update, True)
        return

    # ====== Если мы здесь — это произвольный текст ВНЕ режимов ======
    # Если анкета ещё не завершена
    if not completed:
        await update.message.reply_text("Давай завершим анкету. Напиши, пожалуйста, ответ на последний вопрос.")
        return

    # Если меню включено и это не команда — по умолчанию отвечаем как Q&A с учётом анкеты
    agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
    thinking = await update.message.reply_text("Думаю над ответом…")
    try:
        answer = await agent.get_answer(text)
    finally:
        try:
            await thinking.delete()
        except Exception:
            pass
    await _safe_send(update.effective_chat, answer, use_markdown=True)


__all__ = ["handle_message", "user_states", "GOAL_KEYBOARD", "MAIN_KEYBOARD"]
