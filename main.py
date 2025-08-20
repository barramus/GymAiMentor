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
from bot.telegram_bot import (
    handle_message,
    user_states,
    GOAL_KEYBOARD,
    cmd_program,
    cmd_reset,
    cmd_edit,
    cmd_export,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

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
    save_user_data(user_id, user_data)

    user_states.pop(user_id, None)

    if not name:
        user_states[user_id] = {"mode": "awaiting_name", "step": 0, "data": {}}
        await update.message.reply_text("Привет! Я твой персональный фитнес-тренер GymAiMentor 💪 Как тебя зовут?")
        return

    await update.message.reply_text(f"{name}, выбери свою цель тренировок ⬇️", reply_markup=GOAL_KEYBOARD)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Краткая справка по доступным командам."""
    text = (
        "Доступные команды:\n"
        "/start — начать заново (сохранит только имя)\n"
        "/program — сгенерировать программу по текущей анкете\n"
        "/reset — полностью сбросить анкету и историю\n"
        "/edit age|weight|schedule — изменить одно поле анкеты\n"
        "/export [md|pdf] — выгрузить последний план в файл\n"
    )
    await update.message.reply_text(text)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Глобальный обработчик ошибок — пишет в лог, не падаем молча."""
    logging.exception("Update %r caused error: %s", update, context.error)


def run_main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("Переменная окружения TELEGRAM_TOKEN не задана")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("program", cmd_program))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("edit", cmd_edit))
    app.add_handler(CommandHandler("export", cmd_export))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.add_error_handler(on_error)

    print("Бот запущен (polling).")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    run_main()
