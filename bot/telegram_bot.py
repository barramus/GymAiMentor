import os
import io
import logging
from typing import Optional, Tuple, List

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.agent import FitnessAgent
from app.storage import load_user_data, save_user_data

LOG = logging.getLogger(__name__)

user_states: dict[str, dict] = {}

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
    await update.message.reply_text(
        f"{name}, выбери свою цель тренировок ⬇️",
        reply_markup=GOAL_KEYBOARD,
    )

def _program_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("💾 Сохранить план", callback_data="program:save"),
                InlineKeyboardButton("❌ Не сохранять", callback_data="program:discard"),
            ],
            [
                InlineKeyboardButton("🧾 Экспорт PDF", callback_data="program:export:pdf"),
                InlineKeyboardButton("📄 Экспорт MD", callback_data="program:export:md"),
            ],
            [
                InlineKeyboardButton("🔁 Другая программа", callback_data="program:new"),
                InlineKeyboardButton("🆕 Начать заново", callback_data="program:restart"),
            ],
        ]
    )

def _store_draft(user_id: str, md_text: str):
    data = load_user_data(user_id)
    data["draft_plan_md"] = md_text
    save_user_data(user_id, data)

def _save_plan(user_id: str) -> bool:
    data = load_user_data(user_id)
    draft = (data.get("draft_plan_md") or "").strip()
    if not draft:
        return False
    data["saved_plan_md"] = draft
    save_user_data(user_id, data)
    return True

def _drop_draft(user_id: str) -> bool:
    data = load_user_data(user_id)
    if not data.get("draft_plan_md"):
        return False
    data["draft_plan_md"] = None
    save_user_data(user_id, data)
    return True

def _pick_plan_for_export(data: dict) -> Optional[str]:
    return (data.get("saved_plan_md") or data.get("draft_plan_md") or None)

def _md_bytes(filename: str, text: str) -> Tuple[str, io.BytesIO]:
    bio = io.BytesIO(text.encode("utf-8"))
    bio.name = filename
    bio.seek(0)
    return filename, bio

def _pdf_bytes(filename: str, text: str) -> Tuple[str, io.BytesIO]:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import mm

    bio = io.BytesIO()
    bio.name = filename
    c = canvas.Canvas(bio, pagesize=A4)
    width, height = A4

    left = 15 * mm
    top = height - 15 * mm
    line_height = 6.5 * mm

    lines: List[str] = text.replace("\r", "").split("\n")
    y = top
    for ln in lines:
        while len(ln) > 0:
            chunk = ln[:95]
            ln = ln[95:]
            c.drawString(left, y, chunk)
            y -= line_height
            if y < 20 * mm:
                c.showPage()
                y = top
    c.showPage()
    c.save()
    bio.seek(0)
    return filename, bio

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
        await update.message.reply_text("Сколько тебе лет?")
        return

    questions = [
        ("age", "Сколько тебе лет?"),
        ("height", "Твой рост в сантиметрах?"),
        ("weight", "Твой текущий вес в килограммах?"),
        ("goal", "Желаемый вес в килограммах?"),
        ("restrictions", "Есть ли ограничения по здоровью или предпочтения в тренировках?"),
        ("schedule", "Сколько раз в неделю можешь посещать тренажёрный зал?"),
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
        await update.message.reply_text("Выбери свой уровень подготовки:", reply_markup=LEVEL_KEYBOARD)
        return

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
            md = await agent.get_response("")
        except Exception as e:
            LOG.exception("Ошибка генерации программы: %s", e)
            await update.message.reply_text(
                "Сейчас не удалось сгенерировать программу. Попробуй ещё раз чуть позже.",
                reply_markup=START_KEYBOARD,
            )
            return

        _store_draft(user_id, md)
        await update.message.reply_text(
            md,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_program_menu(),
        )
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
    await q.answer()

    user_id = str(q.from_user.id)
    action = q.data or ""
    LOG.info("PROGRAM ACTION: user=%s data=%s", user_id, action)

    if action.startswith("program:export:"):
        kind = action.split(":")[-1]
        data = load_user_data(user_id)
        plan = _pick_plan_for_export(data)
        if not plan:
            await q.edit_message_reply_markup(reply_markup=_program_menu())
            await q.message.reply_text("Пока нечего экспортировать.")
            return

        if kind == "pdf":
            name, bio = _pdf_bytes("workout_plan.pdf", plan)
        else:
            name, bio = _md_bytes("workout_plan.md", plan)

        await q.message.reply_document(bio)
        return

    if action == "program:save":
        ok = _save_plan(user_id)
        await q.edit_message_reply_markup(reply_markup=_program_menu())
        if ok:
            await q.message.reply_text("Готово! План сохранён. Для экспорта используй /export md или /export pdf.")
        else:
            await q.message.reply_text("Нет черновика плана для сохранения.")
        return

    if action == "program:discard":
        ok = _drop_draft(user_id)
        await q.edit_message_reply_markup(reply_markup=_program_menu())
        await q.message.reply_text("Черновик удалён." if ok else "Черновика не было.")
        return

    if action == "program:new":
        await q.edit_message_reply_markup(reply_markup=_program_menu())
        await q.message.reply_text("Генерирую альтернативную программу…")

        agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
        try:
            md = await agent.get_response("Сгенерируй альтернативную версию программы.")
        except Exception as e:
            LOG.exception("Ошибка регенерации: %s", e)
            await q.message.reply_text("Не удалось сгенерировать новую версию. Попробуй позже.")
            return

        _store_draft(user_id, md)
        await q.message.reply_text(
            md,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_program_menu(),
        )
        return

    if action == "program:restart":
        data = load_user_data(user_id)
        name = (data.get("physical_data") or {}).get("name")
        data["physical_data"] = {"name": name}
        data["physical_data_completed"] = False
        data["history"] = []
        data["draft_plan_md"] = None
        data["saved_plan_md"] = None
        save_user_data(user_id, data)

        user_states.pop(user_id, None)
        await q.edit_message_reply_markup(reply_markup=None)
        await q.message.reply_text("Ок, начнём заново. Выбери цель ⬇️", reply_markup=GOAL_KEYBOARD)
        return
