# GymAiMentor REST API Спецификация

## Общая информация
**Базовый URL**: `https://api.gymaimentor.com/v1`
**Формат данных**: JSON
**Аутентификация**: Bearer Token

## Эндпоинты API

### Пользователи

| Метод | Путь | Описание | Параметры | Тело запроса | Ответ |
|-------|------|----------|-----------|--------------|--------|
| **GET** | `/users/{user_id}` | Получить профиль пользователя | `user_id` (path) | - | `200`: UserProfile<br>`404`: UserNotFound |
| **PUT** | `/users/{user_id}` | Обновить профиль пользователя | `user_id` (path) | UserUpdateRequest | `200`: UserProfile<br>`400`: ValidationError |
| **GET** | `/users/{user_id}/profile` | Получить анкету пользователя | `user_id` (path) | - | `200`: UserProfileText<br>`404`: UserNotFound |

### Программы тренировок

| Метод | Путь | Описание | Параметры | Тело запроса | Ответ |
|-------|------|----------|-----------|--------------|--------|
| **POST** | `/users/{user_id}/programs` | Создать программу тренировок | `user_id` (path) | ProgramRequest | `201`: ProgramResponse<br>`400`: ValidationError<br>`429`: RateLimitExceeded |
| **GET** | `/users/{user_id}/programs/last` | Получить последнюю программу | `user_id` (path) | - | `200`: ProgramResponse<br>`404`: ProgramNotFound |
| **GET** | `/users/{user_id}/programs` | Получить историю программ | `user_id` (path), `limit` (query), `offset` (query) | - | `200`: ProgramListResponse |

### Вопросы и ответы

| Метод | Путь | Описание | Параметры | Тело запроса | Ответ |
|-------|------|----------|-----------|--------------|--------|
| **POST** | `/users/{user_id}/questions` | Задать вопрос AI-тренеру | `user_id` (path) | QuestionRequest | `200`: AnswerResponse<br>`400`: ValidationError<br>`429`: RateLimitExceeded |

### История взаимодействий

| Метод | Путь | Описание | Параметры | Тело запроса | Ответ |
|-------|------|----------|-----------|--------------|--------|
| **GET** | `/users/{user_id}/history` | Получить историю взаимодействий | `user_id` (path), `limit` (query), `offset` (query) | - | `200`: HistoryListResponse |

## Модели данных

### UserProfile
```json
{
  "user_id": "string",
  "physical_data": {
    "name": "string",
    "gender": "string",
    "age": "number",
    "height": "number",
    "weight": "number",
    "goal": "number",
    "restrictions": "string",
    "level": "string",
    "schedule": "number",
    "target": "string",
    "preferred_muscle_group": "string"
  },
  "physical_data_completed": "boolean",
  "created_at": "string",
  "updated_at": "string"
}
```

### UserUpdateRequest
```json
{
  "physical_data": {
    "name": "string",
    "gender": "string",
    "age": "number",
    "height": "number",
    "weight": "number",
    "goal": "number",
    "restrictions": "string",
    "level": "string",
    "schedule": "number",
    "target": "string",
    "preferred_muscle_group": "string"
  }
}
```

### ProgramRequest
```json
{
  "user_instruction": "string",
  "preferred_muscle_group": "string"
}
```

### ProgramResponse
```json
{
  "program_id": "string",
  "user_id": "string",
  "program_text": "string",
  "created_at": "string",
  "metadata": {
    "generation_time": "number",
    "model_used": "string"
  }
}
```

### QuestionRequest
```json
{
  "question": "string",
  "context": "string"
}
```

### AnswerResponse
```json
{
  "answer_id": "string",
  "user_id": "string",
  "question": "string",
  "answer": "string",
  "created_at": "string"
}
```

### HistoryItem
```json
{
  "timestamp": "string",
  "type": "string",
  "user_input": "string",
  "ai_response": "string"
}
```

## Коды ошибок

| Код | Описание | Пример |
|-----|----------|--------|
| `400` | Ошибка валидации | `{"error": "VALIDATION_ERROR", "message": "Некорректный возраст"}` |
| `401` | Неавторизованный доступ | `{"error": "UNAUTHORIZED", "message": "Invalid token"}` |
| `404` | Ресурс не найден | `{"error": "USER_NOT_FOUND", "message": "User not found"}` |
| `429` | Превышен лимит запросов | `{"error": "RATE_LIMIT_EXCEEDED", "message": "Maximum 5 programs per day"}` |
| `500` | Внутренняя ошибка сервера | `{"error": "INTERNAL_ERROR", "message": "Service temporarily unavailable"}` |
| `503` | Сервис недоступен | `{"error": "SERVICE_UNAVAILABLE", "message": "AI service is down"}` |

## Ограничения

- **Лимит генерации программ**: 5 запросов в сутки на пользователя
- **Максимальное время генерации**: 60 секунд
- **Размер программы**: до 5000 токенов
- **Размер вопроса**: до 2500 токенов
- **История взаимодействий**: сохраняется последние 100 записей

## Примеры использования

### Создание программы тренировок
```bash
curl -X POST https://api.gymaimentor.com/v1/users/12345/programs \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "user_instruction": "Сделай программу с акцентом на ноги",
    "preferred_muscle_group": "ноги"
  }'
```

### Задать вопрос AI-тренеру
```bash
curl -X POST https://api.gymaimentor.com/v1/users/12345/questions \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Как правильно делать приседания?",
    "context": "У меня болит колено"
  }'
