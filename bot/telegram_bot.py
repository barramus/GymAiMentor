LAST_REPLIES: dict[str, str] = {}

import os
import re
import time
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.agent import FitnessAgent
from app.storage import (
    load_user_data,
    save_user_data,
    save_lift_history,
)
from app.weights import base_key

__version__ = "tg-bot-1.3.1"
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

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["❓ Задать вопрос AI-тренеру"],
        ["📝 Записать тренировку", "📈 Моя динамика"],
        ["💾 Сохранить в файл", "🔁 Начать заново"],
    ],
    resize_keyboard=True,
    is_persistent=True,
)



async def _send_main_menu(update: Update):
    """Отдельным сообщением — чтобы панель была постоянной."""
    await update.effective_chat.send_message(
        "Что дальше? Выбери действие в меню ниже 👇",
        reply_markup=MAIN_KEYBOARD,
    )


def _normalize_name(raw: str) -> str:
    name = (raw or "").strip()
    if len(name) > 80:
        name = name[:80]
    return name


def sanitize_for_tg(text: str) -> str:
    """Убираем HTML-теги и <br> → обычные переносы строк."""
    text = re.sub(r"\s*<br\s*/?>\s*", "\n", text)
    text = re.sub(r"</?p\s*/?>", "\n", text)
    return text.strip()


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
                InlineKeyboardButton("🆕 Другая программа", callback_data="program:new"),
                InlineKeyboardButton("🔄 Начать заново", callback_data="program:restart"),
            ],
        ]
    )


async def _send_program(update: Update, user_id: str, text: str):
    """Отправляем программу и сразу выводим постоянное меню."""
    await update.effective_chat.send_message(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_program_menu(),
        disable_web_page_preview=True,
    )
    await _send_main_menu(update)



async def _save_last_to_file(update: Update, user_id: str):
    from app.storage import get_last_reply

    text = LAST_REPLIES.get(user_id) or get_last_reply(user_id) or ""
    if not text.strip():
        await update.message.reply_text(
            "Пока нечего сохранять. Сначала запроси программу или задай вопрос.",
        )
        await _send_main_menu(update)
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



def _normalize_piece_name(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


async def _parse_and_save_log(update: Update, user_id: str, text: str):
    """
    Ожидает строку наподобие:
    "присед 50×8, жим лёжа 35×10, верхний блок 40×12"
    """
    raw = text.replace("x", "×").replace("*", "×")
    parts = re.split(r"[,\n;]+", raw)
    saved, errors = [], []

    for p in parts:
        p = p.strip()
        if not p:
            continue
        m = re.search(r"(.+?)\s+(\d+(?:[.,]\d+)?)\s*×\s*(\d{1,2})", p, flags=re.IGNORECASE)
        if not m:
            errors.append(p)
            continue
        name, wtxt, reps_txt = m.group(1), m.group(2), m.group(3)
        try:
            weight = float(wtxt.replace(",", "."))
            reps = int(reps_txt)
        except Exception:
            errors.append(p)
            continue

        key = base_key(_normalize_piece_name(name)) or ""
        if not key:
            errors.append(p)
            continue

        save_lift_history(user_id, key, weight, reps, rir=None)
        saved.append((name.strip(), weight, reps))

    if saved:
        msg = "✅ Сохранил:\n" + "\n".join(
            [f"• {n} — {int(w) if float(w).is_integer() else round(float(w),1)}×{r}" for n, w, r in saved]
        )
        await update.message.reply_text(msg)

    if errors and not saved:
        await update.message.reply_text(
            "Не понял формат для:\n" + "\n".join([f"• {e}" for e in errors]) + "\n\nПример: присед 50×8",
        )
    elif errors:
        await update.message.reply_text(
            "Не распознал часть записей:\n" + "\n".join([f"• {e}" for e in errors]) + "\nПример: жим лёжа 35×10",
        )

    await _send_main_menu(update)


_NAME_BY_KEY = {
    "squat": "Приседания",
    "deadlift": "Становая тяга",
    "bench": "Жим штанги лёжа",
    "ohp": "Жим стоя",
    "row": "Тяга штанги в наклоне",
    "lat_pulldown": "Тяга верхнего блока",
    "leg_curl": "Сгибание ног в тренажёре",
    "leg_press": "Жим ногами",
}


async def _send_dynamics(update: Update, user_id: str):
    data = load_user_data(user_id)
    lifts = data.get("lifts") or {}
    if not lifts:
        await update.message.reply_text(
            "Пока нет записей. Нажми «📝 Записать тренировку» и пришли результаты.",
        )
        await _send_main_menu(update)
        return

    lines = ["Твоя динамика (последние записи):"]
    for key, rec in lifts.items():
        name = _NAME_BY_KEY.get(key, key)
        last_w = rec.get("last_weight")
        reps = rec.get("reps")
        hist = rec.get("history") or []
        tail = hist[-3:]
        hist_str = ", ".join(
            [
                f"{int(h['last_weight']) if float(h['last_weight']).is_integer() else round(float(h['last_weight']),1)}×{h['reps']} ({datetime.utcfromtimestamp(h['ts']).strftime('%d.%m')})"
                for h in tail
            ]
        )
        if last_w and reps:
            lines.append(
                f"• {name}: последняя — {int(last_w) if float(last_w).is_integer() else round(float(last_w),1)}×{reps}; история: {hist_str}"
            )
        else:
            lines.append(f"• {name}: есть записи, но не распознан формат.")
    await update.message.reply_text("\n".join(lines))
    await _send_main_menu(update)



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

    # --- Кнопки главного меню ---
    if text == "❓ Задать вопрос AI-тренеру":
        user_states[user_id] = {"mode": "qa", "step": 0, "data": {}}
        await update.message.reply_text("Задай вопрос по тренировкам 👇")
        await _send_main_menu(update)
        return

    if text == "📈 Моя динамика":
        await _send_dynamics(update, user_id)
        return

    if text == "📝 Записать тренировку":
        user_states[user_id] = {"mode": "log", "step": 0, "data": {}}
        await update.message.reply_text(
            "Пришли результаты в рабочих подходах в формате (где «50» — вес, «8» — повторы):\n"
            "`присед 50×8, жим лёжа 35×10`\n"
            "Можно одной строкой или несколькими сообщениями.",
            parse_mode=ParseMode.MARKDOWN,
        )
        await _send_main_menu(update)
        return

    if text == "💾 Сохранить в файл":
        await _save_last_to_file(update, user_id)
        return

    if text == "🔁 Начать заново":
        user_data["physical_data"] = {"name": name}
        user_data["physical_data_completed"] = False
        save_user_data(user_id, user_data)
        await update.message.reply_text("Начнём заново! Как тебя зовут?")
        await _send_main_menu(update)
        user_states[user_id] = {"mode": "awaiting_name", "step": 0, "data": {}}
        return

    if text == "📋 Другая программа":
        user_states[user_id] = {"mode": None, "step": 0, "data": {}}
        agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
        try:
            plan = await agent.get_response("")
        except Exception:
            await update.message.reply_text("Не удалось сгенерировать программу. Попробуй позже.")
            await _send_main_menu(update)
            return
        plan = sanitize_for_tg(plan)
        await _send_program(update, user_id, plan)
        LAST_REPLIES[user_id] = plan
        from app.storage import set_last_reply; set_last_reply(user_id, plan)
        return

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
            await _send_main_menu(update)
            return

        plan = sanitize_for_tg(plan)
        await _send_program(update, user_id, plan)
        LAST_REPLIES[user_id] = plan
        from app.storage import set_last_reply; set_last_reply(user_id, plan)
        return

    if state.get("mode") == "log":
        await _parse_and_save_log(update, user_id, text)
        user_states[user_id] = {"mode": None, "step": 0, "data": {}}
        return

    agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
    reply = await agent.get_response(text)
    user_data.setdefault("history", []).append(("🧍 " + text, "🤖 " + reply))
    save_user_data(user_id, user_data)

    reply = sanitize_for_tg(reply)
    LAST_REPLIES[user_id] = reply
    from app.storage import set_last_reply; set_last_reply(user_id, reply)

    await update.message.reply_text(reply, parse_mode=ParseMode.MARKDOWN)
    await _send_main_menu(update)



async def on_program_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return

    user_id = str(q.from_user.id)
    data = q.data or ""
    await q.answer()
    logger.info("PROGRAM ACTION: user=%s data=%s", user_id, data)

    action = data.split(":", 1)[1] if ":" in data else ""

    if action == "new":
        progress_msg = await q.message.reply_text("Формирую для тебя другую программу…")

        agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
        try:
            plan = await agent.get_response("")
        except Exception:
            logger.exception("Ошибка генерации НОВОЙ программы")
            await progress_msg.edit_text("Не получилось сгенерировать новую программу. Попробуй ещё раз.")
            return

        await progress_msg.edit_text("Готово! Держи альтернативный вариант 🆕")
        plan = sanitize_for_tg(plan)
        await _send_program(update, user_id, plan)
        LAST_REPLIES[user_id] = plan
        from app.storage import set_last_reply; set_last_reply(user_id, plan)
        return

    if action == "restart":
        user_data = load_user_data(user_id)
        name = (user_data.get("physical_data") or {}).get("name")

        user_data["physical_data"] = {"name": name}
        user_data["physical_data_completed"] = False
        save_user_data(user_id, user_data)

        if name:
            await q.message.reply_text(f"{name}, выбери свою цель тренировок ⬇️", reply_markup=GOAL_KEYBOARD)
            user_states[user_id] = {"mode": None, "step": 0, "data": {}}
        else:
            await q.message.reply_text("Начнём заново! Как тебя зовут?")
            user_states[user_id] = {"mode": "awaiting_name", "step": 0, "data": {}}
        await _send_main_menu(update)
        return


__all__ = ["handle_message", "on_program_action", "user_states", "GOAL_KEYBOARD"]
