# bot/telegram_bot.py
from __future__ import annotations

import os
import re
import time
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict

from telegram import Update, ReplyKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from app.agent import FitnessAgent
from app.storage import load_user_data, save_user_data
from app.storage import get_last_reply, set_last_reply  # для сохранения/чтения последнего ответа

__version__ = "tg-bot-1.4.0"
logger = logging.getLogger("bot.telegram_bot")

# -------------------------- Глобальные состояния/кэш --------------------------

# Временный кэш последнего ответа (для быстрого сохранения в файл)
LAST_REPLIES: dict[str, str] = {}

# user_states[user_id] = {"mode": "...", "step": int, "data": dict}
user_states: Dict[str, dict] = {}

# -------------------------- Клавиатуры --------------------------

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

# Главное постоянное меню (показываем только после первой программы)
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["❓ Задать вопрос AI-тренеру"],
        ["📋 Другая программа"],
        ["💾 Сохранить в файл", "🔁 Начать заново"],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

# -------------------------- Утилиты --------------------------

def _normalize_name(raw: str) -> str:
    name = (raw or "").strip()
    return name[:80] if len(name) > 80 else name

def sanitize_for_tg(text: str) -> str:
    """Убираем HTML-теги и <br> → переносы строк, приводим к чистому тексту."""
    text = re.sub(r"\s*<br\s*/?>\s*", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</?p\s*/?>", "\n", text, flags=re.IGNORECASE)
    return text.strip()

def normalize_gender(text: str) -> Optional[str]:
    t = (text or "").strip().lower()
    if "жен" in t or "👩" in t:
        return "женский"
    if "муж" in t or "👨" in t:
        return "мужской"
    return None

def _split_chunks(text: str, limit: int = 3500) -> list[str]:
    """Режем длинные сообщения на части < limit, стараясь по строкам/абзацам."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    buf: list[str] = []
    size = 0
    for line in text.splitlines(keepends=True):
        if size + len(line) > limit:
            parts.append("".join(buf).rstrip())
            buf, size = [line], len(line)
        else:
            buf.append(line)
            size += len(line)
    if buf:
        parts.append("".join(buf).rstrip())
    return parts

async def _safe_send_text(chat, text: str, reply_markup=None, try_markdown: bool = True):
    """
    Пробуем отправить с Markdown; если Telegram ругнётся — Plain text;
    в крайнем случае вычищаем спецсимволы Markdown.
    """
    chunks = _split_chunks(text)
    for chunk in chunks:
        if try_markdown:
            try:
                await chat.send_message(
                    chunk,
                    parse_mode=ParseMode.MARKDOWN,
                    disable_web_page_preview=True,
                    reply_markup=reply_markup,
                )
                continue
            except BadRequest:
                # Невалидная разметка — отправим как обычный текст
                pass
        try:
            await chat.send_message(
                chunk,
                disable_web_page_preview=True,
                reply_markup=reply_markup,
            )
        except BadRequest:
            cleaned = re.sub(r"[*_`[\]]", "", chunk)
            await chat.send_message(cleaned, reply_markup=reply_markup)

async def _send_main_menu(update: Update, *, completed: bool | None = None):
    """
    Показываем панель только если анкета пройдена/есть первая программа.
    completed=None — определим по данным пользователя.
    """
    uid = str(update.effective_user.id)
    if completed is None:
        data = load_user_data(uid)
        completed = bool(data.get("physical_data_completed"))
    if completed:
        await update.effective_chat.send_message(
            "Что дальше? Выбери действие в меню ниже 👇",
            reply_markup=MAIN_KEYBOARD,
        )

async def _send_program(update: Update, user_id: str, text: str):
    await _safe_send_text(update.effective_chat, text)
    await _send_main_menu(update, completed=True)

async def _save_last_to_file(update: Update, user_id: str):
    text = LAST_REPLIES.get(user_id) or get_last_reply(user_id) or ""
    if not text.strip():
        await update.message.reply_text(
            "Пока нечего сохранять. Сначала запроси программу или задай вопрос."
        )
        await _send_main_menu(update)  # покажем, если доступно
        return

    ts = int(time.time())
    fname = f"program_{user_id}_{ts}.txt"
    out_path = Path("data") / "users" / fname
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")

    with open(out_path, "rb") as fh:
        await update.effective_chat.send_document(
            fh,
            filename=fname,
            caption="Файл с твоим последним ответом",
        )
    await _send_main_menu(update)

async def _ask_goal_with_name(update: Update, name: str):
    await update.message.reply_text(
        f"{name}, выбери свою цель тренировок ⬇️",
        reply_markup=GOAL_KEYBOARD,
    )

# -------------------------- Основной обработчик --------------------------

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

    # Вход в Q&A
    if text == "❓ Задать вопрос AI-тренеру":
        user_states[user_id] = {"mode": "qa", "step": 0, "data": {}}
        await update.message.reply_text(
            "Задай вопрос по тренировкам 👇",
            reply_markup=MAIN_KEYBOARD if completed else None,
        )
        return

    # Режим Q&A — отвечаем кратко, без генерации программы
    if state.get("mode") == "qa":
        agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
        try:
            answer = await agent.get_answer(text)
        except Exception:
            logger.exception("Ошибка Q&A ответа")
            await update.message.reply_text("Не получилось ответить сейчас, попробуй ещё раз.")
            return

        user_data.setdefault("history", []).append(("🧍 " + text, "🤖 " + answer))
        save_user_data(user_id, user_data)
        await _safe_send_text(update.effective_chat, answer, reply_markup=MAIN_KEYBOARD if completed else None)
        return

    # --- Кнопки главного меню (доступны только после первой программы) ---

    if text == "📋 Другая программа":
        agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
        try:
            plan = await agent.get_response("")  # пустой ввод → другая программа на основе анкеты
        except Exception:
            logger.exception("Ошибка генерации другой программы")
            await update.message.reply_text("Не удалось сгенерировать программу. Попробуй позже.")
            await _send_main_menu(update)
            return
        plan = sanitize_for_tg(plan)
        await _send_program(update, user_id, plan)
        LAST_REPLIES[user_id] = plan
        set_last_reply(user_id, plan)
        return

    if text == "💾 Сохранить в файл":
        await _save_last_to_file(update, user_id)
        return

    if text == "🔁 Начать заново":
        user_data["physical_data"] = {"name": name}
        user_data["physical_data_completed"] = False
        save_user_data(user_id, user_data)
        await update.message.reply_text("Начнём заново! Как тебя зовут?")
        user_states[user_id] = {"mode": "awaiting_name", "step": 0, "data": {}}
        return

    # --- Анкета ---
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

        # Уровень
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

        # Сохраняем анкету
        finished_data = state["data"]
        user_states.pop(user_id, None)

        base_physical = user_data.get("physical_data", {}) or {}
        base_physical.update(finished_data)
        user_data["physical_data"] = base_physical
        user_data["physical_data_completed"] = True
        user_data.setdefault("history", [])
        save_user_data(user_id, user_data)

        await update.message.reply_text("Спасибо! Формирую твою персональную программу…")

        # Генерируем первую программу
        agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
        try:
            plan = await agent.get_response("")
        except Exception:
            logger.exception("Ошибка генерации первой программы")
            await update.message.reply_text(
                "Сейчас не удалось сгенерировать программу. Попробуй ещё раз чуть позже."
            )
            return

        plan = sanitize_for_tg(plan)
        await _send_program(update, user_id, plan)
        LAST_REPLIES[user_id] = plan
        set_last_reply(user_id, plan)
        return

    # --- Свободный ввод (после первой программы) ---
    agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
    reply = await agent.get_response(text)
    user_data.setdefault("history", []).append(("🧍 " + text, "🤖 " + reply))
    save_user_data(user_id, user_data)

    reply = sanitize_for_tg(reply)
    LAST_REPLIES[user_id] = reply
    set_last_reply(user_id, reply)

    await _safe_send_text(update.effective_chat, reply, reply_markup=MAIN_KEYBOARD if completed else None)

# Экспорт для импорта в main.py
__all__ = [
    "handle_message",
    "user_states",
    "GOAL_KEYBOARD",
    "MAIN_KEYBOARD",
]
