# main.py
import os
import logging
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from app.storage import load_user_data, save_user_data
from bot.telegram_bot import user_states, GOAL_KEYBOARD, handle_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("main")

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Сбрасываем анкету (кроме имени, если было) и передаём управление
    в общий обработчик — он покажет клавиатуры и начнёт опрос.
    """
    if not update.message:
        return

    user_id = str(update.effective_user.id)
    d = load_user_data(user_id)
    name = (d.get("physical_data") or {}).get("name")

    d["physical_data"] = {"name": name}
    d["physical_data_completed"] = False
    d["history"] = []
    d["last_program"] = None
    d["last_reply"] = None
    save_user_data(user_id, d)

    user_states.pop(user_id, None)

    if not name:
        # Начинаем с имени
        user_states[user_id] = {"mode": "awaiting_name", "step": 0, "data": {}}
        await update.message.reply_text(
            "Привет! Я твой персональный фитнес-тренер GymAiMentor 💪🏼\n"
            "Давай настроим программу. Как тебя зовут?"
        )
        return

    # Имя уже есть — сразу просим цель
    user_states[user_id] = {"mode": None, "step": 0, "data": {}}
    await update.message.reply_text(
        f"{name}, выбери свою цель тренировок ⬇️",
        reply_markup=GOAL_KEYBOARD,
    )

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # просто прокидываем в общий handler — он покажет актуальные кнопки
    await handle_message(update, context)

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled error", exc_info=context.error)

def run_main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("Переменная окружения TELEGRAM_TOKEN не задана")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(on_error)

    print("Бот запущен (polling).")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    run_main()