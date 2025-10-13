# GymAiMentor 💪🏼

Персональный AI-тренер в Telegram на базе GigaChat. Создаёт индивидуальные программы тренировок, отвечает на вопросы по фитнесу и питанию.

## ✨ Возможности

- **Персонализированные программы** — учёт цели, физических параметров, уровня подготовки, 1-7 тренировок/неделю, акцент на группы мышц
- **Вариации** — базовые/изоляция, сила/выносливость, случайные комбинации
- **AI-тренер** — вопросы по технике, питанию, восстановлению
- **Управление** — сохранение в .txt, история, редактирование анкеты

## 🛠 Стек

Python 3.8+ • [python-telegram-bot 20.7](https://github.com/python-telegram-bot/python-telegram-bot) • [GigaChat](https://github.com/ai-forever/gigachat) • python-dotenv • reportlab

## 📦 Установка

```bash
git clone <your-repo-url>
cd GymAiMentor_new/GymAiMentor
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 🔑 Настройка

Получи токены:
- **Telegram**: [@BotFather](https://t.me/BotFather) → `/newbot`
- **GigaChat**: [developers.sber.ru/gigachat](https://developers.sber.ru/gigachat) → API-ключ

Создай `.env` в `GymAiMentor/`:

```bash
TELEGRAM_TOKEN=your_telegram_bot_token
GIGACHAT_TOKEN=your_gigachat_credentials

# Опционально
GIGACHAT_MODEL=GigaChat-2-Max
GIGACHAT_TEMPERATURE=0.35
GIGACHAT_MAX_TOKENS=5000
GIGACHAT_TIMEOUT=90
```

## 🚀 Запуск

```bash
cd GymAiMentor
python main.py  # Увидишь: "Бот запущен (polling)."
```

**Фоновый режим** (Linux): `screen -S gymbot` → запусти бота → `Ctrl+A, D` для отсоединения. Вернуться: `screen -r gymbot`

## 📖 Использование

**Команды:** `/start` (начать/заново), `/menu` (главное меню)

**Процесс:**
1. Заполни анкету (имя, цель, пол, возраст, рост, вес, желаемый вес, ограничения, частота, уровень, акцент на мышцы)
2. Получи первую программу автоматически
3. Используй главное меню: `❓ Вопрос AI` • `🆕 Другая программа` • `🎯 Изменить цель` • `📋 Анкета` • `⚙️ Параметры` • `💾 Сохранить` • `📑 История` • `🔁 Заново`

**Генерация программ:** выбери группу мышц (ноги/ягодицы/спина/плечи/сбалансированно) → стиль (базовые/изоляция/сила/выносливость/случайная)

*Rate limit: 30 сек между генерациями*

## 🔧 Конфигурация

Данные: `data/users/{user_id}.json` • Логи: консоль (DEBUG)

**Частые проблемы:**
- Токены не работают → проверь `.env`
- Таймауты → увеличь `GIGACHAT_TIMEOUT`
- Бот не отвечает → смотри логи, перезапусти

## 📚 API

**FitnessAgent** (`app/agent.py`):
```python
agent = FitnessAgent(token, user_id)
await agent.get_program(user_instruction)  # программа тренировок
await agent.get_answer(question)  # ответ на вопрос
```

**Storage** (`app/storage.py`):
```python
load_user_data(user_id)
save_user_data(user_id, data)
set_user_goal(user_id, goal)
update_user_param(user_id, param, value)
get_user_profile_text(user_id)
```

---

**GymAiMentor** — персональный AI-тренер 💪🏼

[python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) • [GigaChat](https://developers.sber.ru/gigachat)