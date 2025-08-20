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
from app.agent import FitnessAgent
from bot.telegram_bot import handle_message, handle_callback, user_states, GOAL_KEYBOARD

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start:
    - сохраняем только name (если уже был), остальную анкету и историю очищаем;
    - если name нет — спрашиваем имя и ставим mode='awaiting_name';
    - если name есть — сразу просим выбрать цель с обращением по имени.
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
    user_data.pop("last_program_text", None)
    user_data.pop("programs", None)
    save_user_data(user_id, user_data)

    user_states.pop(user_id, None)

    if not name:
        user_states[user_id] = {"mode": "awaiting_name", "step": 0, "data": {}}
        await update.message.reply_text("Привет! Я твой персональный фитнес-тренер GymAiMentor💪🏼 Как тебя зовут?")
        return

    await update.message.reply_text(f"{name}, выбери свою цель тренировок ⬇️", reply_markup=GOAL_KEYBOARD)


async def program_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /program — мгновенная регенерация плана по текущей анкете.
    """
    if not update.message:
        return

    user_id = str(update.effective_user.id)
    user_data = load_user_data(user_id)

    if not user_data.get("physical_data_completed"):
        await update.message.reply_text("Сначала пройди анкету — набери /start.")
        return

    await update.message.reply_text("Окей, обновляю твою программу…")
    agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)

    try:
        response = await agent.get_response("")  # агент сам возьмёт анкету из хранилища
    except Exception:
        context.application.logger.exception("Ошибка генерации программы (/program)")
        await update.message.reply_text("Не вышло обновить программу, попробуй ещё раз.")
        return

    user_data["last_program_text"] = response
    user_data.setdefault("history", []).append(("🧍 Запрос программы (/program)", "🤖 " + response))
    save_user_data(user_id, user_data)

    from bot.telegram_bot import build_program_actions_keyboard
    await update.message.reply_text(response, reply_markup=build_program_actions_keyboard(saved=False))


async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /reset — полный сброс анкеты и истории (имя стираем тоже).
    """
    if not update.message:
        return

    user_id = str(update.effective_user.id)
    # Чистый профиль
    user_data = {
        "history": [],
        "physical_data": {
            "name": None,
            "gender": None,
            "age": None,
            "height": None,
            "weight": None,
            "goal": None,
            "restrictions": None,
            "schedule": None,
            "level": None,
            "target": None,
        },
        "physical_data_completed": False,
    }
    save_user_data(user_id, user_data)
    user_states.pop(user_id, None)

    await update.message.reply_text("Сбросил анкету. Давай начнём заново — как тебя зовут?")
    user_states[user_id] = {"mode": "awaiting_name", "step": 0, "data": {}}


def run_main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("Переменная окружения TELEGRAM_TOKEN не задана")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("program", program_cmd))
    app.add_handler(CommandHandler("reset", reset_cmd))

    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Бот запущен (polling).")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    run_main()
