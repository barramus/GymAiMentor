import os
import io
import logging
from typing import Optional, Dict, Any, Tuple

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

START_KEYBOARD = ReplyKeyboardMarkup([["/start"]], resize_keyboard=True)

def build_program_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💾 Сохранить план", callback_data="program:save"),
            InlineKeyboardButton("❌ Не сохранять", callback_data="program:discard"),
        ],
        [
            InlineKeyboardButton("🧾 Экспорт PDF", callback_data="program:export:pdf"),
            InlineKeyboardButton("📄 Экспорт MD", callback_data="program:export:md"),
        ],
        [
            InlineKeyboardButton("🧭 Другая программа", callback_data="program:new"),
            InlineKeyboardButton("🆕 Начать заново", callback_data="program:restart"),
        ],
    ])


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

def _pick_plan_for_export(data: Dict[str, Any]) -> Optional[str]:
    """
    Сначала берём сохранённый план, иначе черновик.
    """
    plan = (data.get("saved_plan_md") or "").strip()
    if plan:
        return plan
    plan = (data.get("draft_plan_md") or "").strip()
    return plan or None

def _md_bytes(filename: str, content: str) -> Tuple[str, io.BytesIO]:
    bio = io.BytesIO(content.encode("utf-8"))
    bio.name = filename
    bio.seek(0)
    return filename, bio

def _pdf_bytes(filename: str, md_text: str) -> Tuple[str, io.BytesIO]:
    """
    Простой PDF: переносим Markdown как обычный текст (без рендеринга разметки).
    Нужна библиотека reportlab.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import mm

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    left = 15 * mm
    top = height - 20 * mm
    y = top
    max_width = width - 2 * left

    c.setFont("Helvetica", 11)

    for raw_line in md_text.splitlines():
        line = raw_line.replace("\t", "    ")
    
        while line:
            chunk = line[:95]
            line = line[95:]
            c.drawString(left, y, chunk)
            y -= 6 * mm
            if y < 20 * mm:
                c.showPage()
                c.setFont("Helvetica", 11)
                y = top

    c.save()
    buffer.seek(0)
    buffer.name = filename
    return filename, buffer

async def _send_program(update: Update, text_md: str):
    """
    Отправляет план как Markdown и прикрепляет inline-клавиатуру действий.
    """
    await update.message.reply_text(
        text_md,
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True,
        reply_markup=build_program_keyboard(),
    )

questions = [
    ("age", "Сколько тебе лет?"),
    ("height", "Твой рост в сантиметрах?"),
    ("weight", "Твой текущий вес в килограммах?"),
    ("goal", "Желаемый вес в килограммах?"),
    ("restrictions", "Есть ли ограничения по здоровью или предпочтения в тренировках?"),
    ("schedule", "Сколько раз в неделю можешь посещать тренажерный зал?"),
]

async def _ask_goal_with_name(update: Update, name: str):
    await update.message.reply_text(
        f"{name}, выбери свою цель тренировок ⬇️",
        reply_markup=GOAL_KEYBOARD
    )

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

    # Выбор цели
    if text in GOAL_MAPPING:
        user_states[user_id] = {
            "mode": "awaiting_gender",
            "step": 0,
            "data": {"target": GOAL_MAPPING[text]},
        }
        await update.message.reply_text("Укажи свой пол:", reply_markup=GENDER_KEYBOARD)
        return

    # Имя
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

    # Пол
    if state.get("mode") == "awaiting_gender":
        g = normalize_gender(text)
        if not g:
            await update.message.reply_text("Пожалуйста, выбери пол кнопкой ниже:", reply_markup=GENDER_KEYBOARD)
            return

        state["data"]["gender"] = g
        user_states[user_id] = {
            "mode": "survey",
            "step": 2,
            "data": state["data"],
        }
        await update.message.reply_text(questions[0][1])
        return

    # Опрос
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

        # После последнего вопроса — уровень
        user_states[user_id] = {
            "mode": "awaiting_level",
            "step": 0,
            "data": state["data"],
        }
        await update.message.reply_text("Выбери свой уровень подготовки:", reply_markup=LEVEL_KEYBOARD)
        return

    # Уровень
    if state.get("mode") == "awaiting_level":
        if text not in LEVEL_CHOICES:
            await update.message.reply_text("Пожалуйста, выбери уровень кнопкой ниже:", reply_markup=LEVEL_KEYBOARD)
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
        # сбрасываем черновик/сохранённый план
        user_data["draft_plan_md"] = None
        user_data["saved_plan_md"] = None
        save_user_data(user_id, user_data)

        await update.message.reply_text("Спасибо! Формирую твою персональную программу…")

        # Генерация программы
        agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
        try:
            plan_md = await agent.get_response("")
        except Exception:
            context.application.logger.exception("Ошибка генерации программы")
            await update.message.reply_text(
                "Сейчас не удалось сгенерировать программу. Попробуй ещё раз чуть позже.",
                reply_markup=START_KEYBOARD,
            )
            return

        # Сохраняем как ЧЕРНОВИК и показываем с inline-кнопками
        user_data = load_user_data(user_id)
        user_data["draft_plan_md"] = plan_md
        save_user_data(user_id, user_data)

        await _send_program(update, plan_md)
        return

    # Любой другой текст после анкеты — обычный диалог с агентом
    agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
    reply = await agent.get_response(text)

    user_data.setdefault("history", []).append(("🧍 " + text, "🤖 " + reply))
    save_user_data(user_id, user_data)

    await update.message.reply_text(
        reply,
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True
    )

async def on_program_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        return
    q = update.callback_query
    await q.answer()

    user_id = str(update.effective_user.id)
    action = q.data or ""
    context.application.logger.info("program action: user=%s data=%s", user_id, action)

    data = load_user_data(user_id)

    # program:save
    if action == "program:save":
        plan = (data.get("draft_plan_md") or "").strip()
        if not plan:
            await q.edit_message_reply_markup(reply_markup=build_program_keyboard())
            await q.message.reply_text("Пока нечего сохранять.")
            return
        data["saved_plan_md"] = plan
        save_user_data(user_id, data)
        await q.message.reply_text("Готово! План сохранён. Можно экспортировать в PDF/MD.")
        return

    # program:discard
    if action == "program:discard":
        data["draft_plan_md"] = None
        data["saved_plan_md"] = None
        save_user_data(user_id, data)
        await q.message.reply_text("Черновик очищен. Сгенерировать другой план — кнопкой «🧭 Другая программа».")
        return

    # program:export:pdf / md
    if action.startswith("program:export:"):
        plan = _pick_plan_for_export(data)
        if not plan:
            await q.message.reply_text("Пока нечего экспортировать.")
            return

        kind = action.split(":")[-1]
        if kind == "md":
            name, bio = _md_bytes("workout_plan.md", plan)
            await q.message.reply_document(InputFile(bio, filename=name))
        else:
            name, bio = _pdf_bytes("workout_plan.pdf", plan)
            await q.message.reply_document(InputFile(bio, filename=name))
        return

    # program:new — сгенерировать альтернативный план без опроса
    if action == "program:new":
        await q.message.reply_text("Делаю альтернативный вариант плана…")
        agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
        try:
            alt_prompt = (
                "Сгенерируй альтернативную программу тренировок теми же вводными, "
                "избегай повторов упражнений, оформи строго в Markdown: заголовок, дни жирным, "
                "каждый пункт отдельной строкой с «- ». Без RPE."
            )
            plan = await agent.get_response(alt_prompt)
        except Exception:
            context.application.logger.exception("Ошибка генерации альтернативного плана")
            await q.message.reply_text("Не удалось сгенерировать альтернативный план. Попробуй позже.")
            return

        data = load_user_data(user_id)
        data["draft_plan_md"] = plan
        save_user_data(user_id, data)

        # отправим новый как отдельное сообщение (чтобы не путаться с edit_message)
        await q.message.reply_text(
            plan,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
            reply_markup=build_program_keyboard(),
        )
        return

    # program:restart — стереть анкету и начать заново
    if action == "program:restart":
        physical = data.get("physical_data") or {}
        name = physical.get("name")
        data["physical_data"] = {"name": name}
        data["physical_data_completed"] = False
        data["history"] = []
        data["draft_plan_md"] = None
        data["saved_plan_md"] = None
        save_user_data(user_id, data)

        user_states[user_id] = {"mode": "awaiting_name" if not name else None, "step": 0, "data": {}}

        if not name:
            await q.message.reply_text("Ок, начнём заново. Как тебя зовут?")
        else:
            await q.message.reply_text(f"Ок, {name}! Выбери новую цель ⬇️", reply_markup=GOAL_KEYBOARD)
        return
