import os
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from app.storage import save_user_data, load_user_data
from bot.telegram_bot import handle_message, user_states  # user_states теперь импортируется явно

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# Кнопки целей
keyboard = ReplyKeyboardMarkup(
    [["🏋️‍♂️ Набрать массу", "🏃‍♂️ Похудеть", "🧘 Поддерживать форму"]],
    resize_keyboard=True,
    one_time_keyboard=True
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return  # безопасный выход

    user_id = str(update.effective_user.id)

    # Полный сброс данных пользователя
    user_data = load_user_data(user_id)
    user_data["physical_data_completed"] = False
    user_data["physical_data"] = {}
    user_data["history"] = []
    save_user_data(user_id, user_data)

    # Очистка состояния диалога
    user_states.pop(user_id, None)

    # Приветствие и кнопки цели
    await update.message.reply_text(
        "Привет! Я — твой персональный фитнес-ассистент 💪\n\nВыбери свою цель:",
        reply_markup=keyboard
    )

def run_main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущен.")
    app.run_polling()

if __name__ == "__main__":
    run_main()
