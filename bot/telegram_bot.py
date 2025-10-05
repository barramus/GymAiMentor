from __future__ import annotations

import os
import re
import time
import logging
from pathlib import Path
from typing import Optional, Dict, List

from telegram import Update, ReplyKeyboardMarkup, Chat
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.agent import FitnessAgent
from app.storage import (
    load_user_data, save_user_data, set_last_reply, get_last_reply, 
    set_user_goal, update_user_param, get_user_profile_text,
    validate_age, validate_height, validate_weight, validate_schedule
)

logger = logging.getLogger("bot.telegram_bot")

LAST_REPLIES: dict[str, str] = {}

user_states: Dict[str, dict] = {}

# Rate limiting: user_id -> последнее время генерации
last_generation_time: Dict[str, float] = {}
GENERATION_COOLDOWN = 30  # секунд между генерациями

GOAL_MAPPING = {
    "🏃‍♂️ Похудеть": "похудение",
    "🏋️‍♂️ Набрать массу": "набор массы",
    "🧘 Поддерживать форму": "поддержание формы",
}

GOAL_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["🏋️‍♂️ Набрать массу", "🏃‍♂️ Похудеть", "🧘 Поддерживать форму"],
    ],
    resize_keyboard=True,
    one_time_keyboard=True,
)

GENDER_CHOICES = ["👩 Женский", "👨 Мужской"]
GENDER_KEYBOARD = ReplyKeyboardMarkup(
    [GENDER_CHOICES],
    resize_keyboard=True,
    one_time_keyboard=True,
)

LEVEL_CHOICES = ["🚀 Начинающий", "🔥 Опытный"]
LEVEL_KEYBOARD = ReplyKeyboardMarkup(
    [LEVEL_CHOICES],
    resize_keyboard=True,
    one_time_keyboard=True,
)

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["❓ Задать вопрос AI-тренеру"],
        ["🆕 Другая программа", "🎯 Изменить цель"],
        ["📋 Моя анкета", "⚙️ Изменить параметры"],
        ["💾 Сохранить в файл", "📑 История ответов"],
        ["🔁 Начать заново"],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

VARIATIONS_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["💪 Больше базовых", "🎯 Больше изоляции"],
        ["🏋️ Акцент на силу", "⚡ Акцент на выносливость"],
        ["🎲 Случайная вариация"],
        ["◀️ Назад в меню"],
    ],
    resize_keyboard=True,
    one_time_keyboard=True,
)

MUSCLE_GROUPS_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["🦵 Упор на ноги", "🍑 Упор на ягодицы"],
        ["🔙 Упор на спину", "💪 Упор на плечи и руки"],
        ["🎲 Сбалансированная программа"],
        ["◀️ Назад в меню"],
    ],
    resize_keyboard=True,
    one_time_keyboard=True,
)

EDIT_PARAMS_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["👤 Имя", "🔢 Возраст"],
        ["⚖️ Текущий вес", "🎯 Желаемый вес"],
        ["📈 Частота тренировок", "🏋️ Уровень подготовки"],
        ["💪 Акцент на мышцы"],
        ["⚠️ Ограничения / предпочтения"],
        ["◀️ Назад в меню"],
    ],
    resize_keyboard=True,
    one_time_keyboard=True,
)


def _sanitize_for_tg(text: str) -> str:
    """Убираем лишние HTML/markdown артефакты и заголовочные #."""
    out = text or ""
    # убрать #/## из начала строк
    out = re.sub(r"^\s*#{1,6}\s*", "", out, flags=re.MULTILINE)
    # <br>, <p>
    out = re.sub(r"\s*<br\s*/?>\s*", "\n", out, flags=re.IGNORECASE)
    out = re.sub(r"</?p\s*/?>", "\n", out, flags=re.IGNORECASE)
    # убрать лишние пустые строки (>2 подряд -> 2)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()

def _split_for_telegram(text: str, max_len: int = 3500) -> List[str]:
    """Делим длинный текст на части, стараясь резать по границам дней/абзацев."""
    if len(text) <= max_len:
        return [text]

    parts: List[str] = []
    remaining = text
    while len(remaining) > max_len:
        # пробуем найти границу дня
        cut = remaining.rfind("\n\nДень ", 0, max_len)
        if cut < 0:
            cut = remaining.rfind("\n\n**День", 0, max_len)
        if cut < 0:
            cut = remaining.rfind("\n\n", 0, max_len)
        if cut < 0:
            cut = max_len
        parts.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        parts.append(remaining)
    return parts

async def _safe_send(chat: Chat, text: str, use_markdown: bool = True):
    """Безопасная отправка: разбивка на куски + fallback без Markdown при ошибке."""
    text = text.strip()
    for chunk in _split_for_telegram(text):
        try:
            if use_markdown:
                await chat.send_message(
                    chunk,
                    parse_mode=ParseMode.MARKDOWN,
                    disable_web_page_preview=True,
                )
            else:
                await chat.send_message(chunk, disable_web_page_preview=True)
        except Exception as e:
            logger.error("Markdown failed, fallback to plain. Err: %s", e)
            await chat.send_message(chunk, disable_web_page_preview=True)

async def _send_main_menu(update: Update):
    await update.effective_chat.send_message(
        "Что дальше? Выбери действие в меню ⬇️",
        reply_markup=MAIN_KEYBOARD,
    )

async def _save_last_to_file(update: Update, user_id: str):
    """Сохранение последней программы/ответа в файл .txt и отправка документом."""
    text = LAST_REPLIES.get(user_id) or get_last_reply(user_id) or ""
    if not text.strip():
        await update.effective_chat.send_message(
            "Сначала сгенерируй программу (кнопкой «📄 Другая программа»)."
        )
        return
    ts = int(time.time())
    fname = f"program_{user_id}_{ts}.txt"
    out_path = Path("data/users") / fname
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    with open(out_path, "rb") as fh:
        await update.effective_chat.send_document(
            fh, filename=fname, caption="Вот файл с твоим последним запросом 👌🏼"
        )

async def _show_saved_programs(update: Update, user_id: str):
    """Показывает список последних сохраненных программ пользователя."""
    user_dir = Path("data/users")
    pattern = f"program_{user_id}_*.txt"
    
    # Находим все файлы пользователя
    files = list(user_dir.glob(pattern))
    
    if not files:
        await update.effective_chat.send_message(
            "У тебя пока нет сохранённых запросов.\n\nИспользуй кнопку «💾 Сохранить ответ» после генерации ответа."
        )
        return
    
    # Сортируем по дате (последние сверху)
    files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    
    # Берем последние 10
    recent_files = files[:10]
    
    await update.effective_chat.send_message(
        f"📑 Найдено сохранённых ответов: {len(files)}\n\nОтправляю последние {len(recent_files)}..."
    )
    
    for file_path in recent_files:
        # Извлекаем timestamp из имени файла
        try:
            timestamp = int(file_path.stem.split('_')[-1])
            date_str = time.strftime("%d.%m.%Y %H:%M", time.localtime(timestamp))
            caption = f"📎 Запрос от {date_str}"
        except (ValueError, IndexError):
            caption = f"📎 {file_path.name}"
        
        with open(file_path, "rb") as fh:
            await update.effective_chat.send_document(
                fh, filename=file_path.name, caption=caption
            )

def _normalize_name(raw: str) -> str:
    name = (raw or "").strip()
    return name[:80] if len(name) > 80 else name

def _normalize_gender(text: str) -> Optional[str]:
    t = (text or "").lower()
    if "жен" in t or "👩" in t:
        return "женский"
    if "муж" in t or "👨" in t:
        return "мужской"
    return None

def _parse_goal(text: str) -> Optional[str]:
    """Пытаемся распознать цель из кнопки/текста."""
    t = (text or "").lower()
    if any(w in t for w in ("похуд", "сброс", "жир")):
        return "похудение"
    if any(w in t for w in ("набра", "мас", "мышц")):
        return "набор массы"
    if any(w in t for w in ("поддерж", "форма", "тони", "укреп")):
        return "поддержание формы"
    return None


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user_id = str(update.effective_user.id)
    text = (update.message.text or "").strip()

    # Текущие данные пользователя
    data = load_user_data(user_id)
    phys = data.get("physical_data") or {}
    name = phys.get("name")
    completed = bool(data.get("physical_data_completed"))
    state = user_states.get(user_id) or {"mode": None, "step": 0, "data": {}}
    
    logger.debug(f"handle_message - user_id: {user_id}, text: {text[:50]}, state.mode: {state.get('mode')}, completed: {completed}")


    if text == "💾 Сохранить в файл":
        logger.info(f"User {user_id} ({name}) saving last reply to file")
        await _save_last_to_file(update, user_id)
        return

    if text == "📑 История ответов":
        logger.info(f"User {user_id} ({name}) viewing saved programs history")
        await _show_saved_programs(update, user_id)
        return

    if text == "📋 Моя анкета":
        if not completed:
            await update.message.reply_text(
                "Сначала нужно заполнить анкету. Используй кнопку «🔁 Начать заново» для заполнения.",
                reply_markup=MAIN_KEYBOARD,
            )
            return
        logger.info(f"User {user_id} ({name}) viewing profile")
        profile_text = get_user_profile_text(user_id)
        await update.message.reply_text(profile_text, parse_mode=ParseMode.MARKDOWN)
        return

    if text == "⚙️ Изменить параметры":
        if not completed:
            await update.message.reply_text(
                "Сначала нужно заполнить анкету. Используй кнопку «🔁 Начать заново» для заполнения.",
                reply_markup=MAIN_KEYBOARD,
            )
            return
        logger.info(f"User {user_id} ({name}) opening edit parameters menu")
        await update.message.reply_text(
            "Выбери параметр для изменения ⬇️",
            reply_markup=EDIT_PARAMS_KEYBOARD,
        )
        return

    if text == "◀️ Назад в меню":
        user_states.pop(user_id, None)
        await update.message.reply_text("Главное меню ⬇️", reply_markup=MAIN_KEYBOARD)
        return

    if text == "🎯 Изменить цель":
        # Проверяем, заполнена ли анкета
        if not completed:
            await update.message.reply_text(
                "Сначала нужно заполнить анкету. Используй кнопку «🔁 Начать заново» для заполнения.",
                reply_markup=MAIN_KEYBOARD,
            )
            return
            
        logger.info(f"User {user_id} ({name}) changing goal from {phys.get('target')}")
        
        # Переход в режим выбора новой цели
        user_states[user_id] = {"mode": "changing_goal", "step": 0, "data": {}}
        
        # Показываем текущую цель
        current_goal = phys.get("target", "не указана")
        await update.message.reply_text(
            f"Текущая цель: {current_goal}\n\nВыбери новую цель тренировок ⬇️",
            reply_markup=GOAL_KEYBOARD,
        )
        return

    if text == "🆕 Другая программа":
        # Показываем меню выбора группы мышц
        await update.message.reply_text(
            "Выбери акцент программы на группу мышц ⬇️",
            reply_markup=MUSCLE_GROUPS_KEYBOARD
        )
        return

    # Обработка выбора группы мышц - сохраняем в состояние и показываем стили
    # ВАЖНО: этот блок только для кнопки "🆕 Другая программа", НЕ для survey!
    muscle_groups_map = {
        "🦵 Упор на ноги": "ноги",
        "🍑 Упор на ягодицы": "ягодицы",
        "🔙 Упор на спину": "спина",
        "💪 Упор на плечи и руки": "плечи и руки",
        "🎲 Сбалансированная программа": "все группы мышц сбалансированно",
    }
    
    # Проверяем: срабатывает ТОЛЬКО если пользователь НЕ в режиме awaiting_muscle_group
    if text in muscle_groups_map and state.get("mode") != "awaiting_muscle_group":
        # Сохраняем выбор группы мышц в состояние
        user_states[user_id] = {
            "mode": "choosing_variation", 
            "step": 0, 
            "data": {"muscle_group": muscle_groups_map[text]}
        }
        await update.message.reply_text(
            f"Отлично! Программа с акцентом на {muscle_groups_map[text]}.\n\nТеперь выбери стиль тренировок ⬇️",
            reply_markup=VARIATIONS_KEYBOARD
        )
        return

    # Обработка вариаций программ (после выбора группы мышц)
    variation_map = {
        "💪 Больше базовых": "Сделай акцент на базовые многосуставные упражнения (приседания, становая, жимы, подтягивания).",
        "🎯 Больше изоляции": "Добавь больше изолирующих упражнений для проработки отдельных мышечных групп.",
        "🏋️ Акцент на силу": "Программа с акцентом на развитие силы: меньше повторений (4-6), больше отдыха, тяжелые веса.",
        "⚡ Акцент на выносливость": "Программа с акцентом на выносливость: больше повторений (15-20), меньше отдыха, умеренные веса.",
        "🎲 Случайная вариация": "Сделай максимально разнообразную и нестандартную программу, используй креативные упражнения.",
    }
    
    if text in variation_map:
        # Rate limiting check
        current_time = time.time()
        last_time = last_generation_time.get(user_id, 0)
        time_since_last = current_time - last_time
        
        if time_since_last < GENERATION_COOLDOWN:
            wait_time = int(GENERATION_COOLDOWN - time_since_last)
            await update.message.reply_text(
                f"⏳ Подожди ещё {wait_time} секунд перед следующей генерацией.\n\n"
                "Это защита от перегрузки 😊"
            )
            return
        
        # Получаем выбранную группу мышц из состояния (если есть)
        muscle_group = state.get("data", {}).get("muscle_group", "")
        
        # Логируем запрос
        logger.info(f"User {user_id} ({name}) requested program variation: {text}, muscle_group: {muscle_group}")
        
        progress_msg = await update.message.reply_text("⏳ Генерирую программу...")
        start_time = time.time()
        
        try:
            agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
            variation = variation_map[text]
            
            # Добавляем акцент на группу мышц, если выбрана
            if muscle_group:
                variation += f" Сделай ОСОБЫЙ АКЦЕНТ на {muscle_group}. Включи больше упражнений для этой группы мышц."
            
            # Генерация с вариацией
            plan = await agent.get_program(variation)
            
            generation_time = time.time() - start_time
            logger.info(f"Program generated for user {user_id} in {generation_time:.2f}s")
            
            await progress_msg.edit_text("✨ Программа готова!")
            
            # Обновляем время последней генерации
            last_generation_time[user_id] = current_time
            
        except Exception as e:
            logger.exception(f"Error generating program for user {user_id}")
            
            # Различные типы ошибок
            error_msg = "❌ Не получилось сгенерировать программу.\n\n"
            
            if "timeout" in str(e).lower():
                error_msg += "⏱️ Сервер не ответил вовремя. Попробуй ещё раз через минуту."
            elif "connection" in str(e).lower():
                error_msg += "🌐 Проблемы с подключением к серверу. Попробуй позже."
            elif "unauthorized" in str(e).lower() or "403" in str(e):
                error_msg += "🔒 Проблема с авторизацией. Свяжись с администратором."
            else:
                error_msg += f"Попробуй ещё раз позже.\n\nТехническая информация: {str(e)[:100]}"
            
            await progress_msg.edit_text(error_msg)
            return
        
        plan = _sanitize_for_tg(plan)
        LAST_REPLIES[user_id] = plan
        set_last_reply(user_id, plan)
        
        # Очищаем состояние после генерации
        user_states.pop(user_id, None)
        
        # Логируем успешную отправку
        logger.info(f"Program sent to user {user_id}, length: {len(plan)} chars")
        
        await _safe_send(update.effective_chat, plan, use_markdown=True)
        await _send_main_menu(update)
        return

    if text == "🔁 Начать заново":
        logger.info(f"User {user_id} ({name}) restarting registration")
        
        # Полный сброс: имя, анкета, история, последняя программа/ответ
        data["physical_data"] = {}                 # <- имя тоже очищаем
        data["physical_data_completed"] = False
        data["history"] = []
        data["last_program"] = None
        data["last_reply"] = None
        save_user_data(user_id, data)

        # Сбрасываем runtime-состояние и начинаем заново с вопроса про имя
        user_states[user_id] = {"mode": "awaiting_name", "step": 0, "data": {}}
        await update.message.reply_text("Заполним анкету заново 📝 Как тебя зовут?")
        return

    if not completed and state.get("mode") is None:
        if not name:
            user_states[user_id] = {"mode": "awaiting_name", "step": 0, "data": {}}
            await update.message.reply_text("Как тебя зовут?")
            return
        # Если имя уже есть, добавляем его в state["data"]
        user_states[user_id] = {"mode": "awaiting_goal", "step": 0, "data": {"name": name}}
        await update.message.reply_text(
            f"{name}, выбери свою цель тренировок ⬇️",
            reply_markup=GOAL_KEYBOARD,
        )
        return

    if text == "❓ Задать вопрос AI-тренеру":
        user_states[user_id] = {"mode": "qa", "step": 0, "data": {}}
        await update.message.reply_text("Задай вопрос по тренировкам/питанию ✍🏼")
        logger.info(f"User {user_id} ({name}) entered Q&A mode")
        return

    if state.get("mode") == "qa":
        logger.info(f"User {user_id} ({name}) asked: {text[:100]}")
        
        progress_msg = await update.message.reply_text("⏳ Думаю над ответом...")
        start_time = time.time()
        
        try:
            agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
            answer = await agent.get_answer(text)
            
            answer_time = time.time() - start_time
            logger.info(f"Answer generated for user {user_id} in {answer_time:.2f}s")
            
            await progress_msg.delete()
        except Exception as e:
            logger.exception(f"Error generating answer for user {user_id}")
            
            error_msg = "❌ Не удалось получить ответ.\n\n"
            
            if "timeout" in str(e).lower():
                error_msg += "⏱️ Сервер не ответил вовремя. Попробуй переформулировать вопрос."
            elif "connection" in str(e).lower():
                error_msg += "🌐 Проблемы с подключением. Попробуй позже."
            else:
                error_msg += "Попробуй задать вопрос ещё раз."
            
            await progress_msg.edit_text(error_msg)
            return
        
        answer = _sanitize_for_tg(answer)
        LAST_REPLIES[user_id] = answer
        set_last_reply(user_id, answer)
        
        logger.info(f"Answer sent to user {user_id}, length: {len(answer)} chars")
        
        await _safe_send(update.effective_chat, answer, use_markdown=True)
        return

    # Имя
    if state.get("mode") == "awaiting_name":
        if not text:
            await update.message.reply_text("Напиши, пожалуйста, имя.")
            return
        normalized_name = _normalize_name(text)
        phys["name"] = normalized_name
        data["physical_data"] = phys
        save_user_data(user_id, data)
        # Добавляем имя в state["data"], чтобы оно попало в финальное сохранение
        user_states[user_id] = {"mode": "awaiting_goal", "step": 0, "data": {"name": normalized_name}}
        await update.message.reply_text(
            f"{normalized_name}, выбери свою цель тренировок ⬇️",
            reply_markup=GOAL_KEYBOARD,
        )
        return

    # Цель
    if state.get("mode") == "awaiting_goal":
        if text in GOAL_MAPPING:
            # цель выбрана — идём дальше к полу, сохраняем имя из предыдущего шага
            user_states[user_id] = {
                "mode": "awaiting_gender", 
                "step": 0, 
                "data": {**state["data"], "target": GOAL_MAPPING[text]}
            }
            await update.message.reply_text("Укажи свой пол:", reply_markup=GENDER_KEYBOARD)
            return

        # если прислали что-то кроме кнопки — повторим просьбу выбрать цель
        await update.message.reply_text("Пожалуйста, выбери цель кнопкой ниже:", reply_markup=GOAL_KEYBOARD)
        return

    # Обработчики редактирования параметров
    if text == "👤 Имя":
        user_states[user_id] = {"mode": "editing_name", "step": 0, "data": {}}
        current_name = phys.get("name", "не указано")
        await update.message.reply_text(
            f"Текущее имя: {current_name}\n\nВведи новое имя:"
        )
        return

    if text == "🔢 Возраст":
        user_states[user_id] = {"mode": "editing_age", "step": 0, "data": {}}
        current_age = phys.get("age", "не указан")
        await update.message.reply_text(
            f"Текущий возраст: {current_age} лет\n\nВведи новый возраст (10-100 лет):"
        )
        return

    if text == "⚖️ Текущий вес":
        user_states[user_id] = {"mode": "editing_weight", "step": 0, "data": {}}
        current_weight = phys.get("weight", "не указан")
        await update.message.reply_text(
            f"Текущий вес: {current_weight} кг\n\nВведи новый текущий вес в килограммах (например: 75 или 75.5):"
        )
        return

    if text == "🎯 Желаемый вес":
        user_states[user_id] = {"mode": "editing_goal_weight", "step": 0, "data": {}}
        current_goal = phys.get("goal", "не указан")
        await update.message.reply_text(
            f"Желаемый вес: {current_goal} кг\n\nВведи новый желаемый вес в килограммах (например: 70 или 70.5):"
        )
        return

    if text == "📈 Частота тренировок":
        user_states[user_id] = {"mode": "editing_schedule", "step": 0, "data": {}}
        current_schedule = phys.get("schedule", "не указана")
        await update.message.reply_text(
            f"Текущая частота: {current_schedule} раз/неделю\n\nСколько раз в неделю сможешь посещать зал (1-7)?"
        )
        return

    if text == "⚠️ Ограничения / предпочтения":
        user_states[user_id] = {"mode": "editing_restrictions", "step": 0, "data": {}}
        current_restrictions = phys.get("restrictions", "нет")
        await update.message.reply_text(
            f"Текущие ограничения: {current_restrictions}\n\nОпиши новые ограничения по здоровью или предпочтения в тренировках (или напиши 'нет'):"
        )
        return

    if text == "🏋️ Уровень подготовки":
        user_states[user_id] = {"mode": "editing_level", "step": 0, "data": {}}
        current_level = phys.get("level", "не указан")
        await update.message.reply_text(
            f"Текущий уровень: {current_level}\n\nВыбери новый уровень подготовки:",
            reply_markup=LEVEL_KEYBOARD,
        )
        return

    if text == "💪 Акцент на мышцы":
        user_states[user_id] = {"mode": "editing_muscle_group", "step": 0, "data": {}}
        muscle_group_display = {
            "ноги": "🦵 Ноги",
            "ягодицы": "🍑 Ягодицы",
            "спина": "🔙 Спина",
            "плечи и руки": "💪 Плечи и руки",
            "сбалансированно": "🎲 Сбалансированно"
        }
        current_group = phys.get("preferred_muscle_group", "не указан")
        display_group = muscle_group_display.get(current_group, current_group)
        await update.message.reply_text(
            f"Текущий акцент: {display_group}\n\nВыбери новый акцент на группу мышц:",
            reply_markup=MUSCLE_GROUPS_KEYBOARD,
        )
        return

    # Изменение цели (после заполнения анкеты)
    if state.get("mode") == "changing_goal":
        if text in GOAL_MAPPING:
            # Сохраняем новую цель через специальную функцию
            set_user_goal(user_id, GOAL_MAPPING[text])
            
            # Очищаем состояние
            user_states.pop(user_id, None)
            
            # Подтверждение
            await update.message.reply_text(
                f"✅ Цель успешно изменена на: {text}\n\nТеперь твои программы тренировок будут адаптированы под новую цель.",
                reply_markup=MAIN_KEYBOARD,
            )
            return
        
        # Если прислали что-то кроме кнопки
        await update.message.reply_text("Пожалуйста, выбери цель кнопкой ниже:", reply_markup=GOAL_KEYBOARD)
        return

    # Обработка ввода нового имени
    if state.get("mode") == "editing_name":
        new_name = _normalize_name(text)
        if not new_name:
            await update.message.reply_text("❌ Имя не может быть пустым.\n\nПопробуй ещё раз:")
            return
        update_user_param(user_id, "name", new_name)
        user_states.pop(user_id, None)
        await update.message.reply_text(
            f"✅ Имя успешно обновлено: {new_name}",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    # Обработка ввода нового возраста
    if state.get("mode") == "editing_age":
        valid, value, error = validate_age(text)
        if not valid:
            await update.message.reply_text(f"❌ {error}\n\nПопробуй ещё раз:")
            return
        update_user_param(user_id, "age", value)
        user_states.pop(user_id, None)
        await update.message.reply_text(
            f"✅ Возраст успешно обновлён: {value} лет",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    # Обработка ввода нового текущего веса
    if state.get("mode") == "editing_weight":
        valid, value, error = validate_weight(text)
        if not valid:
            await update.message.reply_text(f"❌ {error}\n\nПопробуй ещё раз:")
            return
        update_user_param(user_id, "weight", value)
        user_states.pop(user_id, None)
        await update.message.reply_text(
            f"✅ Текущий вес успешно обновлён: {value} кг",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    # Обработка ввода нового желаемого веса
    if state.get("mode") == "editing_goal_weight":
        valid, value, error = validate_weight(text)
        if not valid:
            await update.message.reply_text(f"❌ {error}\n\nПопробуй ещё раз:")
            return
        update_user_param(user_id, "goal", value)
        user_states.pop(user_id, None)
        await update.message.reply_text(
            f"✅ Желаемый вес успешно обновлён: {value} кг",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    # Обработка ввода новой частоты
    if state.get("mode") == "editing_schedule":
        valid, value, error = validate_schedule(text)
        if not valid:
            await update.message.reply_text(f"❌ {error}\n\nПопробуй ещё раз:")
            return
        update_user_param(user_id, "schedule", value)
        user_states.pop(user_id, None)
        await update.message.reply_text(
            f"✅ Частота тренировок успешно обновлена: {value} раз/неделю",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    # Обработка ввода новых ограничений
    if state.get("mode") == "editing_restrictions":
        restrictions = text if text.lower() not in ["нет", "no", "-"] else None
        update_user_param(user_id, "restrictions", restrictions)
        user_states.pop(user_id, None)
        await update.message.reply_text(
            f"✅ Ограничения / предпочтения успешно обновлены: {restrictions or 'нет'}",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    # Обработка выбора нового уровня
    if state.get("mode") == "editing_level":
        if text not in LEVEL_CHOICES:
            await update.message.reply_text(
                "Пожалуйста, выбери уровень кнопкой ниже:",
                reply_markup=LEVEL_KEYBOARD,
            )
            return
        level = "опытный" if ("Опыт" in text or "🔥" in text) else "начинающий"
        update_user_param(user_id, "level", level)
        user_states.pop(user_id, None)
        await update.message.reply_text(
            f"✅ Уровень подготовки успешно обновлён: {level}",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    # Обработка изменения акцента на мышечную группу
    if state.get("mode") == "editing_muscle_group":
        muscle_groups_map = {
            "🦵 Упор на ноги": "ноги",
            "🍑 Упор на ягодицы": "ягодицы",
            "🔙 Упор на спину": "спина",
            "💪 Упор на плечи и руки": "плечи и руки",
            "🎲 Сбалансированная программа": "сбалансированно",
        }
        
        if text not in muscle_groups_map:
            await update.message.reply_text(
                "Пожалуйста, выбери группу мышц кнопкой ниже:",
                reply_markup=MUSCLE_GROUPS_KEYBOARD,
            )
            return
        
        muscle_group = muscle_groups_map[text]
        update_user_param(user_id, "preferred_muscle_group", muscle_group)
        user_states.pop(user_id, None)
        await update.message.reply_text(
            f"✅ Акцент на мышцы успешно обновлён: {text}",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    # Пол
    if state.get("mode") == "awaiting_gender":
        g = _normalize_gender(text)
        if not g:
            await update.message.reply_text(
                "Пожалуйста, выбери пол кнопкой ниже:",
                reply_markup=GENDER_KEYBOARD,
            )
            return
        st = {"mode": "survey", "step": 2, "data": {**state["data"], "gender": g}}
        user_states[user_id] = st
        await update.message.reply_text("Сколько тебе лет?")
        return

    # Последовательность вопросов
    questions = [
        ("age", "Сколько тебе лет?"),
        ("height", "Твой рост в сантиметрах?"),
        ("weight", "Твой текущий вес в килограммах?"),
        ("goal", "Желаемый вес в килограммах?"),
        ("restrictions", "Есть ли ограничения по здоровью или предпочтения в тренировках?"),
        ("schedule", "Сколько раз в неделю можешь посещать тренажёрный зал?"),
    ]

    # Основной опрос (возраст → ... → частота)
    if state.get("mode") == "survey":
        logger.debug(f"Survey mode - step={state['step']}, current data: {state.get('data', {})}, user text: {text[:50] if text else 'empty'}")
        
        # Валидация предыдущего ответа (если это не первый вход в опрос)
        if state["step"] > 1:
            prev_key = questions[state["step"] - 2][0]
            logger.debug(f"Validating prev_key={prev_key}, text={text}")
            
            # Применяем валидацию в зависимости от поля
            if prev_key == "age":
                valid, value, error = validate_age(text)
                if not valid:
                    await update.message.reply_text(f"❌ {error}\n\nПопробуй ещё раз:")
                    return
                state["data"][prev_key] = value
            elif prev_key == "height":
                valid, value, error = validate_height(text)
                if not valid:
                    await update.message.reply_text(f"❌ {error}\n\nПопробуй ещё раз:")
                    return
                state["data"][prev_key] = value
            elif prev_key == "weight":
                valid, value, error = validate_weight(text)
                if not valid:
                    await update.message.reply_text(f"❌ {error}\n\nПопробуй ещё раз:")
                    return
                state["data"][prev_key] = value
            elif prev_key == "goal":
                valid, value, error = validate_weight(text)
                if not valid:
                    await update.message.reply_text(f"❌ {error}\n\nПопробуй ещё раз:")
                    return
                state["data"][prev_key] = value
            elif prev_key == "restrictions":
                # Для ограничений валидация не нужна, принимаем любой текст
                restrictions = text if text.lower() not in ["нет", "no", "-"] else None
                state["data"][prev_key] = restrictions
            elif prev_key == "schedule":
                valid, value, error = validate_schedule(text)
                if not valid:
                    await update.message.reply_text(f"❌ {error}\n\nПопробуй ещё раз:")
                    return
                state["data"][prev_key] = value
            else:
                state["data"][prev_key] = text
            
            logger.debug(f"After validation - state[data]: {state['data']}")
        
        # Проверяем: есть ли еще вопросы?
        if state["step"] <= len(questions):
            idx = state["step"] - 1
            _, qtext = questions[idx]
            # ВАЖНО: сохраняем обновленный state обратно в user_states
            user_states[user_id] = {"mode": "survey", "step": state["step"] + 1, "data": state["data"]}
            logger.debug(f"Moving to next question, saved state: {user_states[user_id]}")
            await update.message.reply_text(qtext)
            return
        
        # Все вопросы пройдены → переход к выбору уровня подготовки
        logger.debug(f"Survey completed - state[data]: {state['data']}")
        user_states[user_id] = {"mode": "awaiting_level", "step": 0, "data": state["data"]}
        await update.message.reply_text("Выбери свой уровень подготовки:", reply_markup=LEVEL_KEYBOARD)
        return

    # Уровень
    if state.get("mode") == "awaiting_level":
        logger.debug(f"awaiting_level triggered - text: {text}, state: {state}")
        if text not in LEVEL_CHOICES:
            await update.message.reply_text(
                "Пожалуйста, выбери уровень кнопкой ниже:",
                reply_markup=LEVEL_KEYBOARD,
            )
            return
        level = "опытный" if ("Опыт" in text or "🔥" in text) else "начинающий"
        logger.debug(f"Level selected: {level}")
        
        # Сохраняем уровень и переходим к выбору мышечной группы
        user_states[user_id] = {
            "mode": "awaiting_muscle_group", 
            "step": 0, 
            "data": {**state["data"], "level": level}
        }
        
        await update.message.reply_text(
            "Отлично! Теперь выбери, на какую группу мышц хочешь сделать акцент в тренировках ⬇️",
            reply_markup=MUSCLE_GROUPS_KEYBOARD
        )
        return
    
    # Выбор мышечной группы (после уровня, перед генерацией первой программы)
    if state.get("mode") == "awaiting_muscle_group":
        muscle_groups_map = {
            "🦵 Упор на ноги": "ноги",
            "🍑 Упор на ягодицы": "ягодицы",
            "🔙 Упор на спину": "спина",
            "💪 Упор на плечи и руки": "плечи и руки",
            "🎲 Сбалансированная программа": "сбалансированно",
        }
        
        if text not in muscle_groups_map:
            await update.message.reply_text(
                "Пожалуйста, выбери группу мышц кнопкой ниже:",
                reply_markup=MUSCLE_GROUPS_KEYBOARD,
            )
            return
        
        # Сохраняем выбранную группу мышц
        muscle_group = muscle_groups_map[text]
        finished = {**state["data"], "preferred_muscle_group": muscle_group}
        user_states.pop(user_id, None)

        logger.debug(f"Before save - state[data]: {state['data']}")
        logger.debug(f"Before save - finished: {finished}")

        base = data.get("physical_data") or {}
        base.update(finished)
        data["physical_data"] = base
        data["physical_data_completed"] = True
        save_user_data(user_id, data)

        logger.info(f"User {user_id} ({base.get('name')}) completed registration with muscle group: {muscle_group}")
        logger.debug(f"Saved physical_data: {base}")

        progress_msg = await update.message.reply_text("⏳ Спасибо! Формирую твою персональную программу…")
        start_time = time.time()

        agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
        try:
            plan = await agent.get_program("")
            
            generation_time = time.time() - start_time
            logger.info(f"First program generated for user {user_id} in {generation_time:.2f}s")
            
            await progress_msg.edit_text("✨ Программа готова!")
        except Exception as e:
            logger.exception(f"Error generating first program for user {user_id}")
            
            error_msg = "❌ Не удалось сгенерировать программу.\n\n"
            
            if "timeout" in str(e).lower():
                error_msg += "⏱️ Сервер не ответил вовремя. Используй кнопку «🆕 Другая программа» чтобы попробовать снова."
            elif "connection" in str(e).lower():
                error_msg += "🌐 Проблемы с подключением. Попробуй через минуту кнопкой «🆕 Другая программа»."
            else:
                error_msg += "Попробуй через кнопку «🆕 Другая программа» в главном меню."
            
            await progress_msg.edit_text(error_msg)
            await _send_main_menu(update)
            return

        plan = _sanitize_for_tg(plan)
        LAST_REPLIES[user_id] = plan
        set_last_reply(user_id, plan)
        
        logger.info(f"First program sent to user {user_id}, length: {len(plan)} chars")
        
        await _safe_send(update.effective_chat, plan, use_markdown=True)
        await _send_main_menu(update)
        return

    if not completed:
        user_states[user_id] = {"mode": "awaiting_name", "step": 0, "data": {}}
        await update.message.reply_text("Как тебя зовут?")
        return

    agent = FitnessAgent(token=os.getenv("GIGACHAT_TOKEN"), user_id=user_id)
    try:
        plan = await agent.get_program(text)
    except Exception:
        logger.exception("Ошибка генерации программы (с пожеланиями)")
        await update.message.reply_text("Не получилось сгенерировать программу. Попробуй ещё раз.")
        return

    plan = _sanitize_for_tg(plan)
    LAST_REPLIES[user_id] = plan
    set_last_reply(user_id, plan)
    await _safe_send(update.effective_chat, plan, use_markdown=True)
    await _send_main_menu(update)
