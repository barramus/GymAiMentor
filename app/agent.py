from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole
from app.storage import load_user_data, save_user_data


class FitnessAgent:
    def __init__(self, token: str, user_id: str):
        self.token = token
        self.user_id = user_id
        self.user_data = load_user_data(user_id)

        physical_data = self.user_data.get("physical_data", {})
        physical_prompt = self._format_physical_data(physical_data)

        self.payload = Chat(
            messages=[
                Messages(
                    role=MessagesRole.SYSTEM,
                    content=(
                        "Ты — персональный ИИ-тренер. Общайся на 'ты'. "
                        "На основе предоставленных пользователем данных,"
                        "составь индивидуальную программу тренировок, строго на то количество дней, которое укажет пользователь,"
                        "без уточняющих вопросов. Программа должна учитывать цели, физические параметры, ограничения и график."
                    )
                ),
                Messages(
                    role=MessagesRole.USER,
                    content=physical_prompt
                )
            ],
            temperature=1.0,
            max_tokens=2000
        )

    def _format_physical_data(self, data: dict) -> str:
        return (
            f"Цель: {data.get('target', 'не указана')}\n"
            f"Пол: {data.get('gender', 'не указано')}\n"
            f"Возраст: {data.get('age', 'не указано')} лет\n"
            f"Рост: {data.get('height', 'не указано')} см\n"
            f"Текущий вес: {data.get('weight', 'не указано')} кг\n"
            f"Желаемый вес: {data.get('goal', 'не указано')} кг\n"
            f"Ограничения: {data.get('restrictions', 'нет')}\n"
            f"Частота тренировок: {data.get('schedule', 'не указано')}\n"
            f"Уровень подготовки: {data.get('level', 'не указано')}"
        )

    async def get_response(self, user_input: str) -> str:
        from asyncio import to_thread

        if user_input.strip():
            self.payload.messages.append(Messages(role=MessagesRole.USER, content=user_input))

        def _chat_sync():
            with GigaChat(credentials=self.token, verify_ssl_certs=False) as giga:
                response = giga.chat(self.payload)
                return response.choices[0].message

        message = await to_thread(_chat_sync)
        self.payload.messages.append(message)

        # Обновляем историю
        history = self.user_data.get("history", [])
        if user_input.strip():
            history.append(("🧍 " + user_input, "🤖 " + message.content))
        else:
            history.append(("🧍 Запрос программы", "🤖 " + message.content))

        self.user_data["history"] = history
        save_user_data(self.user_id, self.user_data)

        return message.content
