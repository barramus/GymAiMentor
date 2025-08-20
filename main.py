import os
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
from bot.telegram_bot import handle_message, handle_callbacks, user_states, GOAL_KEYBOARD

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user_id = str(update.effective_user.id)
    user_data = load_user_data(user_id)

    physical = user_data.get("physical_data") or {}
    name = physical.get("name")

    user_data["physical_data"] = {"name": name}
    user_data["physical_data_completed"] = False
    user_data["history"] = []
    user_data["last_program"] = ""
    save_user_data(user_id, user_data)

    user_states.pop(user_id, None)

    if not name:
        user_states[user_id] = {"mode": "awaiting_name", "step": 0, "data": {}}
        await update.message.reply_text("Привет! Я твой персональный фитнес-тренер GymAiMentor💪🏼 Как тебя зовут?")
        return

    await update.message.reply_text(f"{name}, выбери свою цель тренировок ⬇️", reply_markup=GOAL_KEYBOARD)

def run_main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("Переменная окружения TELEGRAM_TOKEN не задана")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callbacks))  # <- обработчик кнопок сохранить/не сохранять

    print("Бот запущен (polling).")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    run_main()
