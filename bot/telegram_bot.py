import os
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from app.agent import FitnessAgent
from app.storage import load_user_data, save_user_data

load_dotenv()
GIGACHAT_TOKEN = os.getenv("GIGACHAT_TOKEN")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

user_states = {}

questions = [
    ("gender", "Укажи свой пол: мужской или женский."),
    ("age", "Сколько тебе лет?"),
    ("height", "Твой рост в сантиметрах?"),
    ("weight", "Твой вес в килограммах?"),
    ("goal", "Желаемый вес в килограммах?"),
    ("restrictions", "Есть ли ограничения по здоровью или предпочтения в тренировках?"),
    ("schedule", "Сколько раз в неделю можешь посещать тренажерный зал?"),
    ("level", "Какой у тебя уровень подготовки? (начинающий/опытный)")
]

# Кнопки выбора цели
GOAL_MAPPING = {
    "🏃‍♂️ Похудеть": "похудение",
    "🏋️‍♂️ Набрать массу": "набор массы",
    "🧘 Поддерживать форму": "поддержание формы"
}


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    text = update.message.text.strip()

    user_data = load_user_data(user_id)
    completed = user_data.get("physical_data_completed", False)
    state = user_states.get(user_id, {"step": 0, "data": {}})

    # === 1. Обработка выбора цели ===
    if text in GOAL_MAPPING:
        user_states[user_id] = {
            "step": 1,
            "data": {"target": GOAL_MAPPING[text]}  # сохраняем цель
        }
        await update.message.reply_text(questions[0][1])  # задаём первый вопрос
        return

    # === 2. Опрос ===
    if not completed:
        if state["step"] > 0:
            key = questions[state["step"] - 1][0]
            state["data"][key] = text

        if state["step"] < len(questions):
            next_question = questions[state["step"]][1]
            user_states[user_id] = {
                "step": state["step"] + 1,
                "data": state["data"]
            }
            await update.message.reply_text(next_question)
            return

        # ✅ Опрос завершён
        physical_data = state["data"]
        user_states.pop(user_id)

        user_data["physical_data"] = physical_data
        user_data["physical_data_completed"] = True
        user_data.setdefault("history", [])
        save_user_data(user_id, user_data)

        agent = FitnessAgent(token=GIGACHAT_TOKEN, user_id=user_id)
        response = await agent.get_response("Построй индивидуальную программу тренировок.")

        user_data["history"].append(("🧍 Запрос программы", "🤖 " + response))
        save_user_data(user_id, user_data)

        # Показ кнопки /start после завершения
        keyboard = ReplyKeyboardMarkup([["/start"]], resize_keyboard=True)
        await update.message.reply_text(response, reply_markup=keyboard)
        return

    # === 3. Обычный диалог ===
    agent = FitnessAgent(token=GIGACHAT_TOKEN, user_id=user_id)
    reply = await agent.get_response(text)

    user_data.setdefault("history", []).append(("🧍 " + text, "🤖 " + reply))
    save_user_data(user_id, user_data)

    await update.message.reply_text(reply)
