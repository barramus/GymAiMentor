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
    MAIN_KEYBOARD,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
)
logger = logging.getLogger("main")

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start:
    - если имени нет — спрашиваем имя и ставим mode='awaiting_name';
    - если имя есть — сразу просим выбрать цель (клавиатура целей);
    - обнуляем прогресс и скрываем главное меню до первой программы.
    """
    if not update.message:
        return

    user_id = str(update.effective_user.id)
    d = load_user_data(user_id)
    name = (d.get("physical_data") or {}).get("name")

    # сброс состояний анкеты и программы
    d["physical_data"] = {"name": name}
    d["physical_data_completed"] = False
    d["history"] = []
    d["last_program"] = ""          # строка, не None
    d["menu_enabled"] = False       # панель появится после первой программы
    save_user_data(user_id, d)

    # сброс FSM
    user_states.pop(user_id, None)

    if not name:
        # просим имя
        user_states[user_id] = {"mode": "awaiting_name", "step": 0, "data": {}}
        await update.message.reply_text(
            "Привет! Я твой персональный фитнес-тренер GymAiMentor💪🏼 Как тебя зовут?"
        )
        return

    # имя уже есть — сразу цель
    await update.message.reply_text(
        f"{name}, выбери свою цель тренировок ⬇️",
        reply_markup=GOAL_KEYBOARD,
    )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /menu:
    - показывает основную панель, только если она уже доступна (после первой программы);
    - иначе подсказывает завершить анкету.
    """
    if not update.message:
        return

    user_id = str(update.effective_user.id)
    d = load_user_data(user_id)

    if d.get("menu_enabled"):
        await update.message.reply_text("Меню ниже 👇", reply_markup=MAIN_KEYBOARD)
    else:
        await update.message.reply_text(
            "Главное меню станет доступно после первой сгенерированной программы. "
            "Пожалуйста, завершите анкету командой /start."
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

    # Все текстовые сообщения (кроме команд) — общий обработчик
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.add_error_handler(on_error)

    print("Бот запущен (polling).")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    run_main()
