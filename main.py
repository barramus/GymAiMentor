import os
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from app.storage import load_user_data, save_user_data
from bot.telegram_bot import (
    handle_message,
    on_program_action,
    user_states,
    GOAL_KEYBOARD,
    MAIN_KEYBOARD,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("main")

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start:
    - сохраняем только name (если уже был), остальную анкету и историю очищаем;
    - если name нет — спрашиваем имя и ставим mode='awaiting_name';
    - если name есть — сразу просим выбрать цель и показываем панель.
    """
    if not update.message:
        return

    user_id = str(update.effective_user.id)
    user_data = load_user_data(user_id)

    physical = user_data.get("physical_data") or {}
    name = physical.get("name")

    user_data["physical_data"] = {"name": name}
    user_data["physical_data_completed"] = False
    user_data["history"] = []
    user_data["current_plan"] = ""
    save_user_data(user_id, user_data)

    user_states.pop(user_id, None)

    if not name:
        user_states[user_id] = {"mode": "awaiting_name", "step": 0, "data": {}}
        await update.message.reply_text(
            "Привет! Я твой персональный фитнес-тренер GymAiMentor💪🏼 Как тебя зовут?",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    await update.message.reply_text(
        f"{name}, выбери свою цель тренировок ⬇️",
        reply_markup=GOAL_KEYBOARD,
    )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ручная команда, чтобы вернуть постоянную панель."""
    if update.message:
        await update.message.reply_text(
            "Меню ниже 👇",
            reply_markup=MAIN_KEYBOARD,
        )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled error", exc_info=context.error)


def run_main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("Переменная окружения TELEGRAM_TOKEN не задана")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))

    # Колбэки инлайн-кнопок
    app.add_handler(CallbackQueryHandler(on_program_action, pattern=r"^program:"))

    # Все текстовые сообщения (кроме команд)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Error handler
    app.add_error_handler(on_error)

    print("Бот запущен (polling).")
    # Отсекаем старые непрочитанные апдейты при запуске
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    run_main()
