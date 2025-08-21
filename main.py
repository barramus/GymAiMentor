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

from bot.telegram_bot import handle_message, on_program_action, GOAL_KEYBOARD, user_states
from app.storage import load_user_data, save_user_data

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

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
    user_data["current_plan"] = ""
    save_user_data(user_id, user_data)
    user_states.pop(user_id, None)

    if not name:
        user_states[user_id] = {"mode": "awaiting_name", "step": 0, "data": {}}
        await update.message.reply_text("Привет! Я твой персональный фитнес-тренер GymAiMentor 💪 Как тебя зовут?")
        return

    await update.message.reply_text(f"{name}, выбери свою цель тренировок ⬇️", reply_markup=GOAL_KEYBOARD)

def run_main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("Переменная окружения TELEGRAM_TOKEN не задана")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    async def regen(update: Update, context: ContextTypes.DEFAULT_TYPE):
        from app.agent import FitnessAgent
        user_id = str(update.effective_user.id)
        await update.message.reply_text("Делаю новый вариант программы…")
        agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
        plan = await agent.get_response("")
        from bot.telegram_bot import _send_program
        await _send_program(update, user_id, plan)
    app.add_handler(CommandHandler("program", regen))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.add_handler(CallbackQueryHandler(on_program_action, pattern=r"^program:"))

    print("Бот запущен (polling).")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    run_main()
