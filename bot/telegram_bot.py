import os
from typing import Optional, Dict, Any
from datetime import datetime
from pathlib import Path

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
)
from telegram.ext import ContextTypes

from app.agent import FitnessAgent
from app.storage import load_user_data, save_user_data


user_states: Dict[str, Dict[str, Any]] = {}

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

LEVEL_CHOICES = ["🌱 Начинающий", "🔥 Опытный"]
LEVEL_KEYBOARD = ReplyKeyboardMarkup(
    [LEVEL_CHOICES],
    resize_keyboard=True,
    one_time_keyboard=True,
)

START_KEYBOARD = ReplyKeyboardMarkup([["/start", "/program", "/reset"]], resize_keyboard=True)

questions = [
    ("age", "Сколько тебе лет?"),
    ("height", "Твой рост в сантиметрах?"),
    ("weight", "Твой текущий вес в килограммах?"),
    ("goal", "Желаемый вес в килограммах?"),
    ("restrictions", "Есть ли ограничения по здоровью или предпочтения в тренировках?"),
    ("schedule", "Сколько раз в неделю можешь посещать тренажерный зал?"),
]


def _normalize_name(raw: str) -> str:
    name = (raw or "").strip()
    if len(name) > 80:
        name = name[:80]
    return name

def normalize_gender(text: str) -> Optional[str]:
    t = (text or "").strip().lower()
    if "жен" in t or "👩" in t:
        return "женский"
    if "муж" in t or "👨" in t:
        return "мужской"
    return None

async def _ask_goal_with_name(update: Update, name: str):
    await update.message.reply_text(f"{name}, выбери свою цель тренировок ⬇️", reply_markup=GOAL_KEYBOARD)

def build_program_actions_keyboard(saved: bool = False) -> InlineKeyboardMarkup:
    """
    Кнопки под выданным планом:
      - 💾 Сохранить план / ✅ Сохранено
      - ❌ Не сохранять
      - 🧾 Экспорт PDF | 📄 Экспорт MD
      - 🔁 Другая программа | 🆕 Начать заново
    """
    row1 = [
        InlineKeyboardButton("✅ Сохранено", callback_data="noop") if saved
        else InlineKeyboardButton("💾 Сохранить план", callback_data="program:save"),
        InlineKeyboardButton("❌ Не сохранять", callback_data="program:discard"),
    ]
    row2 = [
        InlineKeyboardButton("🧾 Экспорт PDF", callback_data="program:export:pdf"),
        InlineKeyboardButton("📄 Экспорт MD", callback_data="program:export:md"),
    ]
    row3 = [
        InlineKeyboardButton("🔁 Другая программа", callback_data="program:new"),
        InlineKeyboardButton("🆕 Начать заново", callback_data="program:restart"),
    ]
    return InlineKeyboardMarkup([row1, row2, row3])

def _ensure_dirs(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)

def _export_md(user_id: str, text: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = Path(f"data/exports/{user_id}/program-{ts}.md")
    _ensure_dirs(path)
    path.write_text(text, encoding="utf-8")
    return path

def _export_pdf(user_id: str, text: str) -> Path:
    from reportlab.platypus import SimpleDocTemplate, Preformatted, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.pagesizes import A4

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = Path(f"data/exports/{user_id}/program-{ts}.pdf")
    _ensure_dirs(path)

    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(path), pagesize=A4)
    story = [Preformatted(text, styles["Code"]), Spacer(1, 12)]
    doc.build(story)
    return path

async def send_program_with_actions(update: Update, text: str, saved: bool = False):
    """Показать план + обновить last_program_text"""
    user_id = str(update.effective_user.id)
    user_data = load_user_data(user_id)
    user_data["last_program_text"] = text
    save_user_data(user_id, user_data)
    await update.message.reply_text(text, reply_markup=build_program_actions_keyboard(saved=saved))


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

    # выбор цели
    if text in GOAL_MAPPING:
        user_states[user_id] = {"mode": "awaiting_gender", "step": 0, "data": {"target": GOAL_MAPPING[text]}}
        await update.message.reply_text("Укажи свой пол:", reply_markup=GENDER_KEYBOARD)
        return

    # имя
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

    # пол
    if state.get("mode") == "awaiting_gender":
        g = normalize_gender(text)
        if not g:
            await update.message.reply_text("Пожалуйста, выбери пол кнопкой ниже:", reply_markup=GENDER_KEYBOARD)
            return
        state["data"]["gender"] = g
        user_states[user_id] = {"mode": "survey", "step": 2, "data": state["data"]}
        await update.message.reply_text(questions[0][1])
        return

    # основная анкета
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

        # спрашиваем уровень
        user_states[user_id] = {"mode": "awaiting_level", "step": 0, "data": state["data"]}
        await update.message.reply_text("Выбери свой уровень подготовки:", reply_markup=LEVEL_KEYBOARD)
        return

    # уровень
    if state.get("mode") == "awaiting_level":
        if text not in LEVEL_CHOICES:
            await update.message.reply_text("Пожалуйста, выбери уровень кнопкой ниже:", reply_markup=LEVEL_KEYBOARD)
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

        await update.message.reply_text("Спасибо! Формирую твою персональную программу…")

        agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
        try:
            response = await agent.get_response("")
        except Exception:
            context.application.logger.exception("Ошибка генерации программы")
            await update.message.reply_text("Сейчас не удалось сгенерировать программу. Попробуй ещё раз позже.")
            return

        user_data = load_user_data(user_id)
        user_data["last_program_text"] = response
        user_data.setdefault("history", []).append(("🧍 Запрос программы", "🤖 " + response))
        save_user_data(user_id, user_data)

        await update.message.reply_text(response, reply_markup=build_program_actions_keyboard(saved=False))
        return

    # свободный диалог
    agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
    reply = await agent.get_response(text)
    user_data.setdefault("history", []).append(("🧍 " + text, "🤖 " + reply))
    save_user_data(user_id, user_data)
    await update.message.reply_text(reply)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()

    user_id = str(update.effective_user.id)
    chat_id = update.effective_chat.id
    user_data = load_user_data(user_id)
    text = user_data.get("last_program_text")
    data = (query.data or "").strip()

    if data == "noop":
        return

    # сохранить
    if data == "program:save":
        if not text:
            await query.edit_message_reply_markup(reply_markup=build_program_actions_keyboard(saved=False))
            await context.bot.send_message(chat_id, "Нет сгенерированной программы для сохранения.")
            return
        programs = user_data.get("programs") or []
        programs.append({"ts": datetime.now().isoformat(timespec="seconds"), "text": text})
        user_data["programs"] = programs
        save_user_data(user_id, user_data)
        await query.edit_message_reply_markup(reply_markup=build_program_actions_keyboard(saved=True))
        await context.bot.send_message(chat_id, "Программа сохранена ✅")
        return

    # не сохранять
    if data == "program:discard":
        await context.bot.send_message(chat_id, "Ок, не сохраняю. Если передумаешь — нажми «💾 Сохранить план».")
        # оставляем прежнюю клавиатуру (чтобы можно было передумать)
        return

    # экспорт
    if data == "program:export:md":
        if not text:
            await context.bot.send_message(chat_id, "Пока нечего экспортировать.")
            return
        path = _export_md(user_id, text)
        with path.open("rb") as f:
            await context.bot.send_document(chat_id, document=InputFile(f, filename=path.name))
        return

    if data == "program:export:pdf":
        if not text:
            await context.bot.send_message(chat_id, "Пока нечего экспортировать.")
            return
        path = _export_pdf(user_id, text)
        with path.open("rb") as f:
            await context.bot.send_document(chat_id, document=InputFile(f, filename=path.name))
        return

    # другая программа (регенерация без анкеты)
    if data == "program:new":
        agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
        try:
            new_text = await agent.get_response("")
        except Exception:
            context.application.logger.exception("Ошибка регенерации программы (program:new)")
            await context.bot.send_message(chat_id, "Не вышло создать другую программу, попробуй ещё раз.")
            return

        user_data["last_program_text"] = new_text
        user_data.setdefault("history", []).append(("🧍 Другая программа", "🤖 " + new_text))
        save_user_data(user_id, user_data)

        await context.bot.send_message(chat_id, new_text, reply_markup=build_program_actions_keyboard(saved=False))
        return

    # начать заново (сменить цель и пройти опрос)
    if data == "program:restart":
        # сбрасываем всё, но сохраняем имя (если было) — как /start
        name = (user_data.get("physical_data") or {}).get("name")
        user_data["physical_data"] = {"name": name}
        user_data["physical_data_completed"] = False
        user_data["history"] = []
        user_data.pop("last_program_text", None)
        save_user_data(user_id, user_data)

        user_states.pop(user_id, None)
        if not name:
            user_states[user_id] = {"mode": "awaiting_name", "step": 0, "data": {}}
            await context.bot.send_message(chat_id, "Давай начнём заново. Как тебя зовут?")
        else:
            await context.bot.send_message(chat_id, f"{name}, выбери свою цель тренировок ⬇️", reply_markup=GOAL_KEYBOARD)
        return
