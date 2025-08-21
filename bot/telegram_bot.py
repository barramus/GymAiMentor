import os
import io
import time
import logging
from typing import Optional, Dict

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputFile,
)
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.agent import FitnessAgent
from app.storage import load_user_data, save_user_data

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

LEVEL_CHOICES = ["🌱 Начинающий", "🔥 Опытный"]
LEVEL_KEYBOARD = ReplyKeyboardMarkup(
    [LEVEL_CHOICES],
    resize_keyboard=True,
    one_time_keyboard=True,
)

START_KEYBOARD = ReplyKeyboardMarkup([["/start"]], resize_keyboard=True)

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

def _program_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("💾 Сохранить план", callback_data="program:save"),
                InlineKeyboardButton("🗑 Не сохранять", callback_data="program:discard"),
            ],
            [
                InlineKeyboardButton("📝 Экспорт .md", callback_data="program:export:md"),
                InlineKeyboardButton("📄 Экспорт .pdf", callback_data="program:export:pdf"),
            ],
            [
                InlineKeyboardButton("🔁 Другая программа", callback_data="program:new"),
                InlineKeyboardButton("🔄 Начать заново", callback_data="program:restart"),
            ],
        ]
    )

def _store_current_plan(user_id: str, text: str):
    data = load_user_data(user_id)
    data["current_plan"] = text or ""
    save_user_data(user_id, data)

async def _send_program(update: Update, user_id: str, text: str):
    _store_current_plan(user_id, text)
    await update.effective_chat.send_message(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_program_menu(),
        disable_web_page_preview=True,
    )

def _make_md_bytes(plan_text: str) -> bytes:
    return plan_text.encode("utf-8")

def _make_pdf_bytes(plan_text: str) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Preformatted

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
    style = getSampleStyleSheet()["Code"]
    doc.build([Preformatted(plan_text, style)])
    buf.seek(0)
    return buf.read()

async def _export_plan(update: Update, plan_text: str, fmt: str):
    if not plan_text:
        await update.effective_chat.send_message("План для экспорта не найден. Сгенерируй его заново.")
        return
    try:
        if fmt == "md":
            data = _make_md_bytes(plan_text)
            await update.effective_chat.send_document(
                document=InputFile(io.BytesIO(data), filename="program.md"),
                caption="Экспорт в Markdown",
            )
        else:
            data = _make_pdf_bytes(plan_text)
            await update.effective_chat.send_document(
                document=InputFile(io.BytesIO(data), filename="program.pdf"),
                caption="Экспорт в PDF",
            )
    except Exception:
        logger.exception("Ошибка экспорта плана (%s)", fmt)
        await update.effective_chat.send_message("Не удалось экспортировать файл. Попробуй ещё раз позже.")

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

    if text in GOAL_MAPPING:
        user_states[user_id] = {
            "mode": "awaiting_gender",
            "step": 0,
            "data": {"target": GOAL_MAPPING[text]},
        }
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
        await update.message.reply_text(questions[0][1])
        return

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

        await update.message.reply_text("Спасибо! Формирую твою персональную программу…")

        agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
        try:
            plan = await agent.get_response("")
        except Exception:
            logger.exception("Ошибка генерации программы")
            await update.message.reply_text(
                "Сейчас не удалось сгенерировать программу. Попробуй ещё раз чуть позже.",
                reply_markup=START_KEYBOARD,
            )
            return

        await _send_program(update, user_id, plan)
        return

    agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
    reply = await agent.get_response(text)
    user_data.setdefault("history", []).append(("🧍 " + text, "🤖 " + reply))
    save_user_data(user_id, user_data)

    await update.message.reply_text(reply, parse_mode=ParseMode.MARKDOWN)


async def on_program_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return

    user_id = str(q.from_user.id)
    data = q.data or ""
    await q.answer()
    logger.info("PROGRAM ACTION: user=%s data=%s", user_id, data)


    action = data.split(":", 1)[1] if ":" in data else ""

    user_data = load_user_data(user_id)
    current_plan = (user_data.get("current_plan") or "").strip()


    if action == "save":
        if not current_plan:
            await q.message.reply_text("План не найден — сгенерируй его заново.")
            return
        saved = user_data.get("saved_programs") or []
        saved.append({"ts": int(time.time()), "text": current_plan})
        user_data["saved_programs"] = saved
        save_user_data(user_id, user_data)
        await q.message.reply_text("✅ План сохранён. Можешь экспортировать или сгенерировать другой.")
        return


    if action == "discard":
        user_data["current_plan"] = ""
        save_user_data(user_id, user_data)
        await q.message.reply_text("🗑 Текущий план очищён. Хочешь новый — нажми «Другая программа».")
        return


    if action.startswith("export"):
        if not current_plan:
            await q.message.reply_text("Плана нет для экспорта — сгенерируй его заново.")
            return
        fmt = action.split(":", 1)[1] if ":" in action else "md"
        await _export_plan(update, current_plan, fmt)
        return


    if action == "new":
        progress_msg = await q.message.reply_text("Формирую для тебя другую программу…")

        agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
        try:
            plan = await agent.get_response("")
        except Exception:
            logger.exception("Ошибка генерации НОВОЙ программы")
            await progress_msg.edit_text("Не получилось сгенерировать новую программу. Попробуй ещё раз.")
            return

        await progress_msg.edit_text("Готово! Держи альтернативный вариант ⬇️")
        await _send_program(update, user_id, plan)
        return


    if action == "restart":
        name = (user_data.get("physical_data") or {}).get("name")
        user_data["physical_data"] = {"name": name}
        user_data["physical_data_completed"] = False
        user_data["current_plan"] = ""
        save_user_data(user_id, user_data)

        if name:
            await q.message.reply_text(f"{name}, выбери свою цель тренировок ⬇️", reply_markup=GOAL_KEYBOARD)
            user_states[user_id] = {"mode": None, "step": 0, "data": {}}
        else:
            await q.message.reply_text("Начнём заново! Как тебя зовут?")
            user_states[user_id] = {"mode": "awaiting_name", "step": 0, "data": {}}
        return

__all__ = [
    "handle_message",
    "on_program_action",
    "user_states",
    "GOAL_KEYBOARD",
]
