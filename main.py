# main.py
import os
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from app.storage import load_user_data, save_user_data
from bot.telegram_bot import handle_message

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

    await update.message.reply_text(
        "Привет! Я твой персональный фитнес-тренер GymAiMentor 💪🏼\n"
        "Я могу составить для тебя программу тренировок, а так же ответить на любые вопросы, связанные со спортом, питанием и восстановлением.»."
    )
    # Делегируем дальше — handler сам начнёт с выбора цели
    await handle_message(update, context)

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Делегируем в общий обработчик — он покажет актуальные кнопки/состояние
    await handle_message(update, context)

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled error", exc_info=context.error)

def run_main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("Переменная окружения TELEGRAM_TOKEN не задана")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))

    # Все текстовые сообщения (кроме команд) — в общий обработчик логики
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Глобальный error handler
    app.add_error_handler(on_error)

    print("Бот запущен (polling).")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    run_main()
