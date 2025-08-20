import os
import re
from typing import Optional, Tuple, List

from telegram import Update, ReplyKeyboardMarkup, InputFile
from telegram.ext import ContextTypes

from app.agent import FitnessAgent
from app.storage import load_user_data, save_user_data

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

questions = [
    ("age", "Сколько тебе лет?"),
    ("height", "Твой рост в сантиметрах?"),
    ("weight", "Твой вес в килограммах?"),
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
    await update.message.reply_text(f"{name}, выбери свою цель тренировок.", reply_markup=GOAL_KEYBOARD)

def _to_int_clean(text: str) -> Optional[int]:
    if not text:
        return None
    t = text.replace(",", ".")
    m = re.search(r"\d+([.]\d+)?", t)
    if not m:
        return None
    try:
        return int(round(float(m.group(0))))
    except:
        return None

def _validate_numeric(field: str, value_text: str) -> Tuple[bool, Optional[int], Optional[str]]:
    """
    age: 10..100; height: 120..230; weight|goal: 35..300; schedule: 1..7
    """
    v = _to_int_clean(value_text)
    if v is None:
        return False, None, "Должно быть числом. Пример: 28"
    ranges = {
        "age": (10, 100),
        "height": (120, 230),
        "weight": (35, 300),
        "goal": (35, 300),
        "schedule": (1, 7),
    }
    lo, hi = ranges.get(field, (None, None))
    if lo is None:
        return True, v, None
    if not (lo <= v <= hi):
        return False, None, f"Значение должно быть в диапазоне {lo}..{hi}."
    return True, v, None

def _export_plan_files(user_id: str, plan_text: str, what: Optional[str] = None) -> List[str]:
    """
    what: None -> md и pdf (если есть reportlab), 'md' -> только md, 'pdf' -> только pdf
    """
    from datetime import datetime
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_uid = re.sub(r"\D+", "", user_id) or "user"
    out: List[str] = []

    # MD
    if what in (None, "md"):
        md_path = f"/tmp/plan_{safe_uid}_{stamp}.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(plan_text)
        out.append(md_path)

    # PDF
    if what in (None, "pdf"):
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.pdfgen import canvas
            from reportlab.lib.units import cm
            from textwrap import wrap

            pdf_path = f"/tmp/plan_{safe_uid}_{stamp}.pdf"
            c = canvas.Canvas(pdf_path, pagesize=A4)
            width, height = A4
            x = 2 * cm
            y = height - 2 * cm
            max_width = int((width - 2 * x) / 6.2)
            for line in plan_text.splitlines():
                if not line:
                    y -= 14
                    if y < 2 * cm:
                        c.showPage(); y = height - 2 * cm
                    continue
                for chunk in wrap(line, max_width):
                    c.drawString(x, y, chunk)
                    y -= 14
                    if y < 2 * cm:
                        c.showPage(); y = height - 2 * cm
            c.save()
            out.append(pdf_path)
        except Exception:
            pass

    return out

def _get_last_plan_from_history(user_data: dict) -> Optional[str]:
    hist = user_data.get("history") or []
    for u, a in reversed(hist):
        if a and isinstance(a, str):
            return a[2:].strip() if a.startswith("🤖 ") else a.strip()
    return None

# ===== Команды =====
async def cmd_program(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user_data = load_user_data(user_id)
    if not user_data.get("physical_data_completed"):
        await update.message.reply_text("Сначала пройди анкету: нажми /start", reply_markup=START_KEYBOARD)
        return

    await update.message.reply_text("Окей, формирую программу…")
    agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
    try:
        response = await agent.get_response("")
    except Exception:
        context.application.logger.exception("Ошибка генерации программы (/program)")
        await update.message.reply_text("Не удалось сгенерировать программу. Попробуй ещё раз чуть позже.")
        return

    await update.message.reply_text(response)

async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user_data = load_user_data(user_id)
    user_data["physical_data"] = {}
    user_data["physical_data_completed"] = False
    user_data["history"] = []
    save_user_data(user_id, user_data)
    user_states.pop(user_id, None)
    await update.message.reply_text("Анкета и история сброшены. Нажми /start, чтобы заполнить заново.", reply_markup=START_KEYBOARD)

async def cmd_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /edit age|weight|schedule
    """
    user_id = str(update.effective_user.id)
    args = (update.message.text or "").split()
    if len(args) < 2:
        await update.message.reply_text("Использование: /edit age|weight|schedule")
        return
    field = args[1].strip().lower()
    if field not in {"age", "weight", "schedule"}:
        await update.message.reply_text("Можно редактировать: age, weight, schedule")
        return

    user_states[user_id] = {"mode": "editing_field", "field": field}
    prompts = {
        "age": "Введи новый возраст (число):",
        "weight": "Введи новый вес (кг):",
        "schedule": "Сколько раз в неделю можешь тренироваться? (1..7):",
    }
    await update.message.reply_text(prompts[field])

async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /export [md|pdf]
    """
    user_id = str(update.effective_user.id)
    user_data = load_user_data(user_id)
    plan = _get_last_plan_from_history(user_data)
    if not plan:
        await update.message.reply_text("Плана пока нет. Сначала сгенерируй его командой /program.")
        return

    args = (update.message.text or "").split()
    fmt = None
    if len(args) > 1:
        fmt = args[1].lower()
        if fmt not in {"md", "pdf"}:
            await update.message.reply_text("Поддерживаемые форматы: md, pdf. Пример: /export pdf")
            return

    paths = _export_plan_files(user_id, plan, fmt)
    if not paths:
        await update.message.reply_text("Не удалось подготовить файл(ы) для экспорта.")
        return

    for p in paths:
        try:
            with open(p, "rb") as f:
                await update.message.reply_document(document=InputFile(f, filename=os.path.basename(p)))
        except Exception:
            context.application.logger.exception("Ошибка отправки файла экспорта")
    await update.message.reply_text("Готово ✅")

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

    if state.get("mode") == "editing_field":
        field = state.get("field")
        ok, val, err = _validate_numeric(field, text)
        if not ok:
            await update.message.reply_text(f"{err} Попробуй ещё раз:")
            return

        physical_data[field] = val
        user_data["physical_data"] = physical_data
        save_user_data(user_id, user_data)

        user_states.pop(user_id, None)
        await update.message.reply_text(f"Поле `{field}` обновлено: {val}. Хочешь пересоздать план? Используй /program")
        return

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
        user_states[user_id] = {
            "mode": "survey",
            "step": 2,
            "data": state["data"],
        }
        await update.message.reply_text(questions[0][1])
        return

    if not completed and state.get("mode") == "survey":
        if state["step"] > 1:
            prev_key = questions[state["step"] - 2][0]
            if prev_key in {"age", "height", "weight", "goal", "schedule"}:
                ok, val, err = _validate_numeric(prev_key, text)
                if not ok:
                    await update.message.reply_text(f"{err} Попробуй ещё раз:")
                    return
                state["data"][prev_key] = val
            else:
                state["data"][prev_key] = text

        if state["step"] <= len(questions):
            next_idx = state["step"] - 1
            _next_key, next_text = questions[next_idx]
            user_states[user_id] = {"mode": "survey", "step": state["step"] + 1, "data": state["data"]}
            await update.message.reply_text(next_text)
            return

        user_states[user_id] = {
            "mode": "awaiting_level",
            "step": 0,
            "data": state["data"],
        }
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
            response = await agent.get_response("")  # пустой ввод => программа по анкете
        except Exception:
            context.application.logger.exception("Ошибка генерации программы")
            await update.message.reply_text(
                "Сейчас не удалось сгенерировать программу. Попробуй ещё раз чуть позже.",
                reply_markup=START_KEYBOARD,
            )
            return

        user_data["history"].append(("🧍 Запрос программы", "🤖 " + response))
        save_user_data(user_id, user_data)

        await update.message.reply_text(response, reply_markup=START_KEYBOARD)
        return

    agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
    reply = await agent.get_response(text)

    user_data.setdefault("history", []).append(("🧍 " + text, "🤖 " + reply))
    save_user_data(user_id, user_data)

    await update.message.reply_text(reply)
