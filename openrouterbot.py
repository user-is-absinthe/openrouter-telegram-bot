import logging
import json
import requests
import threading
import time
import asyncio
import queue
import re
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    BotCommand, Bot
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

# Импорт конфигурации из файла config.py
import config
# Импорт обработчика базы данных
from db_handler import DBHandler

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Устанавливаем уровень логирования для библиотеки httpx
httpx_logger = logging.getLogger("httpx")
httpx_logger.setLevel(logging.WARNING)  # Покажет только предупреждения и ошибки


# Функция для получения списка моделей
async def get_free_models():
    try:
        response = requests.get(
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization": f"Bearer {config.OPENROUTER_API_KEY}"}
        )
        models_data = response.json()

        free_models = []
        for model in models_data.get("data", []):
            # Проверяем, что модель бесплатна по ценам за prompt и completion
            pricing = model.get("pricing", {})
            if pricing.get("prompt") == "0" and pricing.get("completion") == "0":
                free_models.append({
                    "id": model.get("id"),
                    "name": model.get("name", model.get("id")),
                    "description": model.get("description", "Нет описания"),
                    "features": model.get("features", [])
                })

        return free_models
    except Exception as e:
        logger.error(f"Ошибка при получении списка моделей: {e}")
        return []


# Функция для преобразования Markdown в Telegram-совместимый формат
def convert_markdown_to_telegram(text):
    """
    Преобразует Markdown в формат, совместимый с Telegram.
    Поддерживает базовые элементы форматирования.
    """
    if not text:
        return text

    # Заменяем некорректные пары символов форматирования
    text = text.replace("**_", "*_").replace("_**", "_*")

    # Обрабатываем блоки кода
    def replace_code_block(match):
        code = match.group(2)
        # Telegram поддерживает только встроенный код без подсветки синтаксиса
        return f'```\n{code}\n```'

    text = re.sub(r'```(\w+)\n(.*?)```', replace_code_block, text, flags=re.DOTALL)

    # Заголовки (конвертируем в жирный текст для Telegram)
    text = re.sub(r'^# (.+)$', r'*\1*', text, flags=re.MULTILINE)
    text = re.sub(r'^## (.+)$', r'*\1*', text, flags=re.MULTILINE)
    text = re.sub(r'^### (.+)$', r'*\1*', text, flags=re.MULTILINE)

    # Обрабатываем встроенные элементы

    # Жирный текст: **text** -> *text* (для Telegram)
    text = re.sub(r'(?<!\*)\*\*(?!\*)(.+?)(?<!\*)\*\*(?!\*)', r'*\1*', text)

    # Курсив: _text_ -> _text_ (оставляем как есть для Telegram)
    # text = re.sub(r'(?<!_)_(?!_)(.+?)(?<!_)_(?!_)', r'_\1_', text)

    # Курсив: *text* -> _text_ (для Telegram)
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'_\1_', text)

    # Гиперссылки: [text](url) -> [text](url) (оставляем как есть для Telegram)

    # Маркированные списки
    text = re.sub(r'^- (.+)$', r'• \1', text, flags=re.MULTILINE)
    text = re.sub(r'^\* (.+)$', r'• \1', text, flags=re.MULTILINE)

    return text


# Настройка команд меню бота
async def setup_commands(application):
    commands = [
        BotCommand("start", "Запустить бота"),
        BotCommand("select_model", "Выбрать AI модель"),
        BotCommand("help", "Помощь по использованию бота"),
    ]

    bot = application.bot
    await bot.set_my_commands(commands)


# Обработчики команд
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start."""
    user = update.effective_user
    chat_id = update.effective_chat.id

    # Регистрируем пользователя в БД
    db = context.bot_data.get("db")
    if db:
        db.register_user(
            id_chat=chat_id,
            id_user=user.id,
            first_name=user.first_name,
            last_name=user.last_name,
            username=user.username
        )

    await update.message.reply_text(
        "Привет! Я бот для общения с AI моделями через OpenRouter. "
        "Используйте /select_model для выбора бесплатной модели."
    )


async def select_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /select_model."""
    await update.message.reply_text("Получаю список бесплатных моделей...")

    models = await get_free_models()

    if not models:
        await update.message.reply_text(
            "К сожалению, не удалось получить список моделей или бесплатные модели отсутствуют.")
        return

    keyboard = []
    for model in models:
        model_name = model["name"]
        # Обрезаем название, если оно слишком длинное
        if len(model_name) > 60:
            model_name = model_name[:57] + "..."

        keyboard.append([InlineKeyboardButton(model_name, callback_data=f"model_{model['id']}")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text("Выберите модель для общения:", reply_markup=reply_markup)


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик нажатий на кнопки."""
    query = update.callback_query
    await query.answer()

    data = query.data
    user_id = query.from_user.id
    chat_id = query.message.chat_id

    if data.startswith("model_"):
        # Извлекаем ID модели
        model_id = data[6:]

        # Сохраняем модель
        context.user_data["selected_model"] = model_id

        # Находим название модели и описание для отображения
        models = await get_free_models()
        model_name = model_id
        model_description = "Нет описания"

        for model in models:
            if model["id"] == model_id:
                model_name = model["name"]
                model_description = model["description"]
                break

        # Если текущий диалог существует, отмечаем его как завершенный
        db = context.bot_data.get("db")
        if db and "current_dialog" in context.user_data:
            db.mark_last_message(user_id, context.user_data["current_dialog"])
            # Создаем новый диалог при выборе новой модели
            context.user_data["current_dialog"] = db.get_next_dialog_number(user_id)

        # Формируем сообщение с названием и описанием модели
        response_message = (
            f"Вы выбрали модель: {model_name}\n\n"
            f"Описание модели:\n{model_description}\n\n"
            "Теперь вы можете отправить сообщение, и я передам его выбранной AI модели."
        )

        # Если сообщение слишком длинное, сокращаем
        if len(response_message) > 4096:
            response_message = response_message[:4093] + "..."

        await query.edit_message_text(response_message)

    elif data.startswith("reload_"):
        # Извлекаем идентификаторы из callback_data
        parts = data.split("_")
        if len(parts) >= 3:
            chat_id = parts[1]
            message_id = parts[2]

            if "last_message" in context.user_data:
                # Получаем последнее сообщение пользователя
                user_message = context.user_data["last_message"]["text"]

                # Удаляем сообщение с ответом AI, если возможно
                try:
                    await context.bot.delete_message(
                        chat_id=query.message.chat_id,
                        message_id=query.message.message_id
                    )
                except Exception as e:
                    logger.error(f"Ошибка при удалении сообщения: {e}")
                    # Если не можем удалить, то редактируем
                    await query.edit_message_text("Перезагружаю ответ...")

                # Повторно отправляем запрос
                await process_ai_request(
                    context,
                    query.message.chat_id,
                    user_message
                )
            else:
                await query.answer("Не могу найти предыдущий запрос")
        else:
            await query.answer("Некорректный формат данных")

    elif data == "cancel_stream":
        # Отмена потоковой передачи
        chat_id = str(query.message.chat_id)
        if chat_id in context.bot_data.get("active_streams", {}):
            context.bot_data["active_streams"][chat_id].set()
            await query.edit_message_text(f"{query.message.text}\n\n[Генерация остановлена пользователем]")
        else:
            await query.answer("Поток уже завершен")


# Функция для потоковой обработки ответа от AI
def stream_ai_response(model_id, user_message, update_queue, chat_id, message_id, cancel_event, context):
    """
    Функция для потоковой обработки ответа от AI.
    """
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": config.SITE_URL,
        "X-Title": config.SITE_NAME,
    }

    # Формируем payload
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": user_message}],
        "stream": True
    }

    # Измеряем время начала запроса
    start_time = time.time()

    # Инициализация переменных для хранения результата
    full_response = ""
    last_update_time = time.time()

    # Для отслеживания изменений в ответе
    last_response_txt = ""

    try:
        with requests.post(url, headers=headers, json=payload, stream=True) as r:
            buffer = ""

            # Проверяем статус ответа
            if not r.ok:
                error_msg = f"Ошибка API: {r.status_code} - {r.text}"
                logger.error(error_msg)
                update_queue.put({
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": f"Произошла ошибка при запросе к API: {error_msg}",
                    "is_final": True,
                    "error": True
                })
                return

            # Используем iter_lines для более надежной обработки SSE
            for line in r.iter_lines():
                if cancel_event.is_set():
                    r.close()
                    return

                if not line:
                    continue

                # Декодируем строку
                line_text = line.decode('utf-8')

                # Для отладки
                logger.debug(f"SSE line: {line_text}")

                # Обрабатываем SSE строки
                if line_text.startswith('data: '):
                    data = line_text[6:]
                    if data == '[DONE]':
                        break

                    try:
                        data_obj = json.loads(data)

                        # Проверяем, есть ли выбор в ответе
                        if "choices" in data_obj and len(data_obj["choices"]) > 0:
                            choice = data_obj["choices"][0]

                            # Проверяем, есть ли контент в выборе
                            content_updated = False
                            if "delta" in choice and "content" in choice["delta"] and choice["delta"][
                                "content"] is not None:
                                content_chunk = choice["delta"]["content"]
                                full_response += content_chunk
                                content_updated = True

                            # Обновляем сообщение только если был новый контент
                            if content_updated:
                                # Обновляем сообщение с заданным интервалом
                                current_time = time.time()
                                if current_time - last_update_time > config.STREAM_UPDATE_INTERVAL:
                                    current_response = convert_markdown_to_telegram(full_response)

                                    # Отправляем обновление только если текст изменился
                                    if current_response != last_response_txt:
                                        update_queue.put({
                                            "chat_id": chat_id,
                                            "message_id": message_id,
                                            "text": current_response,
                                            "is_final": False
                                        })
                                        last_response_txt = current_response
                                        last_update_time = current_time

                    except json.JSONDecodeError as e:
                        logger.error(f"Ошибка декодирования JSON: {e} - {data}")

                # Проверяем, не было ли отмены операции
                if cancel_event.is_set():
                    break

    except Exception as e:
        logger.error(f"Ошибка при потоковом получении ответа: {e}")
        update_queue.put({
            "chat_id": chat_id,
            "message_id": message_id,
            "text": f"Произошла ошибка: {str(e)}",
            "is_final": True,
            "error": True
        })
        return

    # Преобразуем финальный текст ответа для корректного отображения
    formatted_response = convert_markdown_to_telegram(full_response)

    # Отправляем финальное обновление
    if formatted_response != last_response_txt:
        update_queue.put({
            "chat_id": chat_id,
            "message_id": message_id,
            "text": formatted_response,
            "is_final": True,
            "dialog_id": context.get("current_dialog_id", None)  # Передаем ID диалога
        })


# Асинхронная функция для обновления сообщений из очереди
async def message_updater(context):
    """Фоновая задача для обновления сообщений с ответами AI"""
    # Для хранения последнего содержимого каждого сообщения
    last_message_content = {}

    while True:
        try:
            # Проверяем, есть ли элементы в очереди
            if not context.bot_data["update_queue"].empty():
                # Получаем данные из синхронной очереди
                update_data = context.bot_data["update_queue"].get_nowait()

                chat_id = update_data["chat_id"]
                message_id = update_data["message_id"]
                text = update_data["text"]
                is_final = update_data.get("is_final", False)
                error = update_data.get("error", False)
                dialog_id = update_data.get("dialog_id", None)

                # Создаем уникальный идентификатор для сообщения
                msg_identifier = f"{chat_id}:{message_id}"

                # Проверяем, изменился ли текст сообщения
                current_content = {
                    "text": text,
                    "is_final": is_final
                }

                # Если контент не изменился, пропускаем обновление
                if msg_identifier in last_message_content and not is_final:
                    prev_content = last_message_content[msg_identifier]
                    if prev_content["text"] == text:
                        # Освобождаем задачу и пропускаем обновление
                        context.bot_data["update_queue"].task_done()
                        continue

                # Сохраняем новое содержимое
                last_message_content[msg_identifier] = current_content

                # Создаем разные клавиатуры в зависимости от статуса
                if is_final:
                    # Для завершенных сообщений добавляем кнопку перезагрузки
                    reply_markup = InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔄 Перезагрузить ответ",
                                             callback_data=f"reload_{chat_id}_{message_id}")
                    ]])

                    # Для финальных сообщений очищаем соответствующую запись в last_message_content
                    if msg_identifier in last_message_content:
                        del last_message_content[msg_identifier]

                    # Если это финальное сообщение, обновляем ответ модели в БД
                    if dialog_id and "db" in context.bot_data:
                        db = context.bot_data["db"]
                        db.update_model_answer(dialog_id, text)
                else:
                    # Для незавершенных сообщений добавляем кнопку отмены
                    reply_markup = InlineKeyboardMarkup([[
                        InlineKeyboardButton("❌ Остановить генерацию", callback_data="cancel_stream")
                    ]])

                # Если текст слишком длинный для одного сообщения Telegram
                if len(text) > 4096:
                    # Если это финальное сообщение, разбиваем на части
                    if is_final:
                        chunks = [text[i:i + 4096] for i in range(0, len(text), 4096)]

                        # Удаляем промежуточное сообщение
                        try:
                            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
                        except Exception as e:
                            logger.error(f"Не удалось удалить сообщение: {e}")

                        # Отправляем части как отдельные сообщения
                        for i, chunk in enumerate(chunks):
                            # Добавляем кнопку только к последнему сообщению
                            if i == len(chunks) - 1:
                                sent_msg = await context.bot.send_message(
                                    chat_id=chat_id,
                                    text=f"Часть {i + 1}/{len(chunks)}:\n\n{chunk}",
                                    reply_markup=reply_markup
                                )
                                # Сохраняем ID последнего сообщения для потенциальной перезагрузки
                                if str(chat_id) in context.bot_data.get("active_streams", {}):
                                    del context.bot_data["active_streams"][str(chat_id)]
                            else:
                                await context.bot.send_message(
                                    chat_id=chat_id,
                                    text=f"Часть {i + 1}/{len(chunks)}:\n\n{chunk}"
                                )
                    else:
                        # Для незавершенного сообщения отображаем только первую часть
                        text_truncated = text[:4093] + "..."
                        try:
                            await context.bot.edit_message_text(
                                text=text_truncated,
                                chat_id=chat_id,
                                message_id=message_id,
                                reply_markup=reply_markup
                            )
                        except Exception as e:
                            if "Message is not modified" in str(e):
                                # Это нормально, просто игнорируем
                                logger.debug("Сообщение не было изменено, пропускаем обновление")
                            else:
                                logger.error(f"Ошибка при обновлении сообщения: {e}")
                else:
                    # Обновляем сообщение
                    try:
                        await context.bot.edit_message_text(
                            text=text,
                            chat_id=chat_id,
                            message_id=message_id,
                            reply_markup=reply_markup
                        )

                        # Если это финальное сообщение, удаляем из активных потоков
                        if is_final and str(chat_id) in context.bot_data.get("active_streams", {}):
                            del context.bot_data["active_streams"][str(chat_id)]
                    except Exception as e:
                        if "Message is not modified" in str(e):
                            # Это нормально, просто игнорируем
                            logger.debug("Сообщение не было изменено, пропускаем обновление")
                        else:
                            logger.error(f"Ошибка при обновлении сообщения: {e}")

                # Отмечаем задачу как выполненную для синхронной очереди
                context.bot_data["update_queue"].task_done()

        except queue.Empty:
            # Если очередь пуста, просто продолжаем
            pass
        except Exception as e:
            logger.error(f"Ошибка в обработчике сообщений: {e}")

        # Небольшая пауза для предотвращения высокой нагрузки на CPU
        await asyncio.sleep(0.1)


async def process_ai_request(context, chat_id, user_message):
    """Обработка запроса к AI модели и отправка ответа."""
    if "selected_model" not in context.user_data:
        await context.bot.send_message(
            chat_id=chat_id,
            text="Пожалуйста, сначала выберите модель с помощью команды /select_model"
        )
        return

    model_id = context.user_data["selected_model"]

    # Отправляем индикатор набора текста
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    # Начальное сообщение с кнопкой отмены
    cancel_keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Остановить генерацию", callback_data="cancel_stream")
    ]])

    initial_message = await context.bot.send_message(
        chat_id=chat_id,
        text="Генерирую ответ...",
        reply_markup=cancel_keyboard
    )

    # Сохраняем информацию о последнем сообщении для перезагрузки
    context.user_data["last_message"] = {
        "id": f"{chat_id}_{initial_message.message_id}",
        "text": user_message
    }

    # Инициализируем очередь обновлений, если её еще нет
    if "update_queue" not in context.bot_data:
        # Используем стандартную синхронную очередь
        context.bot_data["update_queue"] = queue.Queue()
        # Запускаем фоновую задачу для обновления сообщений
        asyncio.create_task(message_updater(context))

    # Инициализируем словарь активных потоков, если его еще нет
    if "active_streams" not in context.bot_data:
        context.bot_data["active_streams"] = {}

    # Создаем событие для отмены потока
    cancel_event = threading.Event()
    context.bot_data["active_streams"][str(chat_id)] = cancel_event

    # Передаем идентификатор текущего диалога в контекст для потоковой функции
    thread_context = {}
    if "current_dialog_id" in context.user_data:
        thread_context["current_dialog_id"] = context.user_data["current_dialog_id"]

    # Запускаем поток для потоковой обработки
    threading.Thread(
        target=stream_ai_response,
        args=(model_id, user_message, context.bot_data["update_queue"], chat_id,
              initial_message.message_id, cancel_event, thread_context)
    ).start()


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик текстовых сообщений."""
    user_message = update.message.text
    user = update.effective_user
    chat_id = update.effective_chat.id
    user_id = user.id

    # Логируем пользователя и его сообщение
    db = context.bot_data.get("db")
    if db:
        # Регистрируем или обновляем пользователя
        db.register_user(
            id_chat=chat_id,
            id_user=user_id,
            first_name=user.first_name,
            last_name=user.last_name,
            username=user.username
        )

        # Получаем или создаем номер текущего диалога
        if "current_dialog" not in context.user_data:
            context.user_data["current_dialog"] = db.get_next_dialog_number(user_id)

        # Проверяем, есть ли выбранная модель
        if "selected_model" in context.user_data:
            model_id = context.user_data["selected_model"]

            # Находим название модели для логирования
            models = await get_free_models()
            model_name = model_id
            for model in models:
                if model["id"] == model_id:
                    model_name = model["name"]
                    break

            # Логируем запрос пользователя (без ответа модели пока)
            dialog_id = db.log_dialog(
                id_chat=chat_id,
                id_user=user_id,
                number_dialog=context.user_data["current_dialog"],
                model=model_name,
                model_id=model_id,
                user_ask=user_message
            )

            # Сохраняем ID диалога для последующего обновления
            if dialog_id:
                context.user_data["current_dialog_id"] = dialog_id
        else:
            # Если модель не выбрана, просто отправляем сообщение
            await update.message.reply_text(
                "Пожалуйста, сначала выберите модель с помощью команды /select_model"
            )
            return

    # Обрабатываем запрос и отправляем ответ
    await process_ai_request(
        context,
        update.message.chat_id,
        user_message
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /help."""
    help_text = (
        "Доступные команды:\n"
        "/start - Начать взаимодействие с ботом\n"
        "/select_model - Выбрать бесплатную AI модель\n"
        "/help - Показать эту справку\n\n"
        "После выбора модели просто отправьте текстовое сообщение, и я передам его выбранной AI модели.\n\n"
        "Особенности использования:\n"
        "• Во время генерации ответа вы можете нажать кнопку 'Остановить генерацию'\n"
        "• Под каждым ответом AI будет кнопка 'Перезагрузить ответ' для повторного запроса"
    )
    await update.message.reply_text(help_text)


async def post_init(application: Application) -> None:
    """Действия после инициализации бота."""
    await setup_commands(application)


async def shutdown(application: Application) -> None:
    """Действия при завершении работы бота"""
    # Закрываем соединение с базой данных
    if "db" in application.bot_data:
        application.bot_data["db"].close()

    logger.info("Бот остановлен")


def main() -> None:
    """Запуск бота."""
    # Инициализация БД
    db = DBHandler(db_path="data/openrouter_bot.db")

    # Создаем билдер приложения
    builder = Application.builder()

    # Задаем токен
    builder.token(config.TELEGRAM_TOKEN)

    # Добавляем пост-инициализацию
    builder.post_init(post_init)

    # Добавляем функцию для завершения работы
    builder.post_shutdown(shutdown)

    # Строим приложение
    application = builder.build()

    # Сохраняем ссылку на объект БД в контексте бота
    application.bot_data["db"] = db

    # Добавление обработчиков
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("select_model", select_model))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Запуск бота
    application.run_polling()


if __name__ == "__main__":
    main()
