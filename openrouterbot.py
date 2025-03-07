import requests
import html
import os
import queue
import re
import threading
import time
import json
import asyncio
import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, MenuButtonCommands, BotCommandScope
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

import config
from db_handler import DBHandler

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Глобальная переменная для доступа к application из разных частей кода
application = None


def convert_markdown_to_html(markdown_text):
    """Конвертирует базовую разметку Markdown в HTML для Telegram."""
    # Заменяем HTML-специальные символы
    text = html.escape(markdown_text)

    # Заменяем блоки кода
    text = re.sub(r'```([^`]+)```', r'<pre>\1</pre>', text)

    # Заменяем инлайн-код
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)

    # Заменяем жирный текст
    text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)

    # Заменяем курсив
    text = re.sub(r'\*([^*]+)\*', r'<i>\1</i>', text)

    return text


def fetch_and_update_models(context):
    """Получает список моделей из API и обновляет БД."""
    url = "https://openrouter.ai/api/v1/models"
    headers = {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": config.SITE_URL,
        "X-Title": config.SITE_NAME,
    }

    try:
        # Используем requests вместо aiohttp
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code == 200:
            data = response.json()

            # Получаем доступ к БД
            db = context.bot_data.get("db")
            if db:
                # Сохраняем каждую модель
                saved_count = 0
                for model in data.get("data", []):
                    if db.save_model(model):
                        saved_count += 1

                logger.info(f"Обновлено {saved_count} моделей из {len(data.get('data', []))}")
                return True
            else:
                logger.error("Нет доступа к БД для сохранения моделей")
        else:
            logger.error(f"Ошибка при получении моделей: {response.status_code} - {response.text}")
    except Exception as e:
        logger.error(f"Ошибка при обновлении моделей: {e}")

    return False


async def get_free_models(context=None):
    """Получает список бесплатных моделей."""
    # Пытаемся получить контекст
    if not context and 'application' in globals():
        global application
        if hasattr(application, 'bot_data'):
            context = application

    if context:
        db = context.bot_data.get("db")
        if db:
            return db.get_models(only_free=True)

    # Если не удалось получить из БД, возвращаем стандартный список
    return [
        {
            "id": "meta-llama/llama-3-8b-instruct:free",
            "name": "Meta: Llama 3 8B Instruct",
            "description": "Llama 3 8B – компактная модель для диалогов и создания контента"
        },
        {
            "id": "anthropic/claude-3-haiku:free",
            "name": "Anthropic: Claude 3 Haiku",
            "description": "Claude 3 Haiku – самая быстрая и доступная модель в семействе Claude 3"
        },
        {
            "id": "google/gemma-7b-it:free",
            "name": "Google: Gemma 7B IT",
            "description": "Gemma 7B – легкая и мощная модель для работы с текстом"
        }
    ]


async def translate_with_openrouter(text_to_translate, db, model_id=None):
    """
    Переводит текст на русский язык с помощью OpenRouter API.

    Args:
        text_to_translate: Текст для перевода
        db: Экземпляр DBHandler для доступа к базе данных
        model_id: ID модели для использования (по умолчанию Gemini Flash 2.0)

    Returns:
        Переведенный текст или None в случае ошибки
    """
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": config.SITE_URL,
        "X-Title": config.SITE_NAME,
    }

    # Если модель не указана, используем Gemini Flash 2.0 по умолчанию
    if not model_id:
        model_id = "google/gemini-flash-2-0-experimental:free"

    # Формируем промпт для перевода
    prompt = f"Переведи описание на русский язык: {text_to_translate}"

    # Формируем payload
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)

        if response.status_code != 200:
            # Проверяем наличие ошибки квоты
            error_data = response.json()
            error_message = error_data.get('error', {}).get('message', '')
            error_code = error_data.get('error', {}).get('code', 0)

            # Проверяем наличие ошибки квоты (код 429)
            if error_code == 429:
                metadata = error_data.get('error', {}).get('metadata', {})
                raw_error = metadata.get('raw', '')
                provider_name = metadata.get('provider_name', '')

                logger.warning(f"Ошибка квоты у провайдера {provider_name}: {error_message}")
                logger.debug(f"Подробная ошибка: {raw_error}")

                # Получаем следующую доступную бесплатную модель
                next_model = get_next_free_model(db, model_id)

                if next_model and next_model != model_id:
                    logger.info(f"Переключаюсь на модель {next_model}")
                    # Рекурсивно вызываем функцию с новой моделью
                    return await translate_with_openrouter(text_to_translate, db, next_model)
                else:
                    logger.error("Не удалось найти альтернативную модель")
                    return None

            # Другие ошибки
            logger.error(f"Ошибка OpenRouter API: {response.status_code} - {response.text}")
            return None

        response_data = response.json()

        # Извлекаем переведенный текст из ответа
        if (response_data and 'choices' in response_data
                and len(response_data['choices']) > 0
                and 'message' in response_data['choices'][0]
                and 'content' in response_data['choices'][0]['message']):

            translated_text = response_data['choices'][0]['message']['content']

            # Очищаем возможные артефакты перевода
            if "Переведи описание на русский язык:" in translated_text:
                translated_text = translated_text.split(":", 1)[1].strip()

            return translated_text
        else:
            logger.error("Неожиданный формат ответа от OpenRouter API")
            logger.error(f"Ответ: {response_data}")
            return None

    except Exception as e:
        logger.error(f"Ошибка при запросе к OpenRouter API: {e}")
        return None


async def translate_all_models(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Переводит описания всех моделей, независимо от наличия текущего перевода."""
    user_id = update.effective_user.id

    # Проверяем, является ли пользователь админом
    if str(user_id) not in config.ADMIN_IDS:
        await update.message.reply_text("У вас нет прав для использования этой команды.")
        return

    # Получаем доступ к БД
    db = context.bot_data.get("db")
    if not db:
        await update.message.reply_text("Ошибка доступа к базе данных.")
        return

    # Отправляем сообщение о начале обновления
    message = await update.message.reply_text("Начинаю перевод всех описаний моделей...")

    # Получаем список всех моделей
    cursor = db.conn.cursor()
    cursor.execute("SELECT id, description FROM models WHERE description IS NOT NULL AND description != ''")

    models_to_translate = cursor.fetchall()

    if not models_to_translate:
        await message.edit_text("Нет моделей для перевода.")
        return

    # Счетчики для статистики
    total = len(models_to_translate)
    success = 0
    failed = 0

    # Получаем начальную модель для перевода
    current_tr_model = "google/gemini-flash-2-0-experimental:free"

    # Обновляем статус
    await message.edit_text(
        f"Начинаю перевод {total} описаний моделей.\n"
        f"Текущая модель для перевода: {current_tr_model}"
    )

    # Переводим каждое описание
    for model_data in models_to_translate:
        current_model_id = model_data[0]
        description = model_data[1]

        # Переводим описание, передавая экземпляр БД для возможного переключения моделей
        translated = await translate_with_openrouter(description, db, current_tr_model)

        if translated:
            # Обновляем описание в БД
            if db.update_model_description(current_model_id, translated):
                success += 1
            else:
                failed += 1
        else:
            failed += 1

            # Пробуем получить следующую модель при ошибке
            next_tr_model = get_next_free_model(db, current_tr_model)
            if next_tr_model and next_tr_model != current_tr_model:
                current_tr_model = next_tr_model
                logger.info(f"Модель для перевода изменена на: {current_tr_model}")

        # Обновляем статус каждые 3 модели или на последней модели
        if (success + failed) % 3 == 0 or (success + failed) == total:
            await message.edit_text(
                f"Перевод моделей: {success + failed}/{total}\n"
                f"✅ Успешно: {success}\n"
                f"❌ Ошибок: {failed}\n"
                f"Текущая модель для перевода: {current_tr_model}"
            )

        # Делаем паузу, чтобы не перегружать API
        await asyncio.sleep(2)

    # Финальный отчет
    await message.edit_text(
        f"Перевод завершен!\n"
        f"Всего моделей: {total}\n"
        f"✅ Успешно переведено: {success}\n"
        f"❌ Ошибок: {failed}"
    )


async def translate_descriptions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Переводит описания моделей на русский язык с помощью OpenRouter API."""
    user_id = update.effective_user.id

    # Проверяем, является ли пользователь админом
    if str(user_id) not in config.ADMIN_IDS:
        await update.message.reply_text("У вас нет прав для использования этой команды.")
        return

    # Получаем доступ к БД
    db = context.bot_data.get("db")
    if not db:
        await update.message.reply_text("Ошибка доступа к базе данных.")
        return

    # Проверяем аргументы команды
    model_id = None
    if context.args:
        model_id = context.args[0]

    # Отправляем сообщение о начале обновления
    message = await update.message.reply_text("Начинаю перевод описаний моделей...")

    # Получаем список моделей для перевода
    cursor = db.conn.cursor()

    if model_id:
        # Переводим конкретную модель
        cursor.execute(
            "SELECT id, description FROM models WHERE id = ?",
            (model_id,)
        )
    else:
        # Ищем все модели с пустым rus_description
        cursor.execute(
            "SELECT id, description FROM models WHERE rus_description IS NULL OR rus_description = ''"
        )

    models_to_translate = cursor.fetchall()

    if not models_to_translate:
        await message.edit_text("Нет моделей для перевода.")
        return

    # Счетчики для статистики
    total = len(models_to_translate)
    success = 0
    failed = 0

    # Получаем начальную модель для перевода
    current_tr_model = "google/gemini-flash-2-0-experimental:free"

    # Обновляем статус
    await message.edit_text(
        f"Начинаю перевод {total} описаний моделей.\n"
        f"Текущая модель для перевода: {current_tr_model}"
    )

    # Переводим каждое описание
    for model_data in models_to_translate:
        current_model_id = model_data[0]
        description = model_data[1]

        if not description:
            logger.warning(f"У модели {current_model_id} отсутствует описание")
            failed += 1
            continue

        # Переводим описание, передавая экземпляр БД для возможного переключения моделей
        translated = await translate_with_openrouter(description, db, current_tr_model)

        if translated:
            logger.info(f"Перевод для модели {current_model_id} получен: {translated[:50]}...")
            # Обновляем описание в БД
            if db.update_model_description(current_model_id, translated):
                success += 1
                logger.info(f"Перевод для модели {current_model_id} успешно сохранен")
            else:
                failed += 1
                logger.error(f"Не удалось сохранить перевод для модели {current_model_id}")
        else:
            failed += 1
            logger.error(f"Не удалось получить перевод для модели {current_model_id}")

            # Пробуем получить следующую модель при ошибке
            next_tr_model = get_next_free_model(db, current_tr_model)
            if next_tr_model and next_tr_model != current_tr_model:
                current_tr_model = next_tr_model
                logger.info(f"Модель для перевода изменена на: {current_tr_model}")

        # Обновляем статус каждые 3 модели или на последней модели
        if (success + failed) % 3 == 0 or (success + failed) == total:
            await message.edit_text(
                f"Перевод моделей: {success + failed}/{total}\n"
                f"✅ Успешно: {success}\n"
                f"❌ Ошибок: {failed}\n"
                f"Текущая модель для перевода: {current_tr_model}"
            )

        # Делаем паузу, чтобы не перегружать API
        await asyncio.sleep(2)

    # Финальный отчет
    await message.edit_text(
        f"Перевод завершен!\n"
        f"Всего моделей: {total}\n"
        f"✅ Успешно переведено: {success}\n"
        f"❌ Ошибок: {failed}"
    )


def get_next_free_model(db, current_model_id):
    """
    Получает следующую доступную бесплатную модель после текущей.
    Если текущая модель последняя или не найдена, возвращает первую доступную бесплатную модель.
    """
    try:
        cursor = db.conn.cursor()

        # Получаем все бесплатные модели
        cursor.execute("""
        SELECT id FROM models 
        WHERE (prompt_price = '0' AND completion_price = '0') 
           OR id LIKE '%:free' 
        ORDER BY id
        """)

        free_models = [row[0] for row in cursor.fetchall()]

        if not free_models:
            logger.error("В базе данных нет доступных бесплатных моделей")
            return None

        # Если текущая модель в списке, ищем следующую
        if current_model_id in free_models:
            current_index = free_models.index(current_model_id)
            next_index = (current_index + 1) % len(free_models)
            return free_models[next_index]

        # Если текущая модель не найдена, возвращаем первую
        return free_models[0]

    except Exception as e:
        logger.error(f"Ошибка при получении следующей бесплатной модели: {e}")
        # Возвращаем Claude-3 Haiku как запасной вариант
        return "anthropic/claude-3-haiku:free"


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

    # Используем контекст диалога, если он предоставлен
    messages = context.get("messages", [{"role": "user", "content": user_message}])

    # Если в контексте нет текущего сообщения, добавляем его
    if not messages or messages[-1]["role"] != "user" or messages[-1]["content"] != user_message:
        messages.append({"role": "user", "content": user_message})

    # Формируем payload
    payload = {
        "model": model_id,
        "messages": messages,
        "stream": True
    }

    # Измеряем время начала запроса
    start_time = time.time()
    # Максимальное время ожидания ответа в секундах (5 минут)
    max_wait_time = 300

    # Инициализация переменных для хранения результата
    full_response = ""
    last_update_time = time.time()

    # Для отслеживания изменений в ответе
    last_response_txt = ""

    # Функция для проверки отмены и отправки обновления
    def handle_cancellation():
        if cancel_event.is_set():
            logger.info(f"Генерация для chat_id {chat_id} остановлена пользователем")
            update_queue.put({
                "chat_id": chat_id,
                "message_id": message_id,
                "text": convert_markdown_to_html(full_response) + "\n\n[Генерация остановлена пользователем]",
                "is_final": True,
                "was_canceled": True,
                "dialog_id": context.get("current_dialog_id", None),
                "is_reload": context.get("is_reload", False),
                "user_id": context.get("user_id"),
                "model_name": context.get("model_name"),
                "model_id": context.get("model_id"),
                "user_ask": context.get("user_ask"),
                "dialog_number": context.get("dialog_number")
            })
            return True
        return False

    # Функция для проверки таймаута
    def check_timeout():
        current_time = time.time()
        if current_time - start_time > max_wait_time:
            logger.warning(f"Превышен тайм-аут ответа модели ({max_wait_time} сек) для chat_id {chat_id}")
            update_queue.put({
                "chat_id": chat_id,
                "message_id": message_id,
                "text": convert_markdown_to_html(
                    full_response) + "\n\n[Генерация прервана из-за превышения тайм-аута (5 минут)]",
                "is_final": True,
                "was_canceled": True,
                "dialog_id": context.get("current_dialog_id", None),
                "is_reload": context.get("is_reload", False),
                "user_id": context.get("user_id"),
                "model_name": context.get("model_name"),
                "model_id": context.get("model_id"),
                "user_ask": context.get("user_ask"),
                "dialog_number": context.get("dialog_number")
            })
            return True
        return False

    try:
        # Проверяем не отменена ли генерация до начала запроса
        if handle_cancellation():
            return

        # Устанавливаем тайм-аут для requests
        session = requests.Session()
        response = session.post(url, headers=headers, json=payload, stream=True, timeout=30)

        # Проверяем статус ответа
        if not response.ok:
            error_msg = f"Ошибка API: {response.status_code} - {response.text}"
            logger.error(error_msg)
            update_queue.put({
                "chat_id": chat_id,
                "message_id": message_id,
                "text": f"Произошла ошибка при запросе к API: {error_msg}",
                "is_final": True,
                "error": True,
                "dialog_id": context.get("current_dialog_id", None),
                "is_reload": context.get("is_reload", False)
            })
            return

        # Для просмотра ответа строка за строкой
        line_iter = response.iter_lines()

        # Проверяем отмену каждые 0.1 сек
        while not handle_cancellation() and not check_timeout():
            try:
                # Используем poll с таймаутом для более частой проверки отмены
                line_available = False
                line = None

                # Попытка получить следующую строку с таймаутом
                try:
                    line = next(line_iter)
                    line_available = True
                except StopIteration:
                    # Конец итератора (конец ответа)
                    break

                # Если строка доступна, обрабатываем её
                if line_available and line:
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
                                        current_response = convert_markdown_to_html(full_response)

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
                else:
                    # Если строки нет, делаем небольшую паузу и проверяем отмену
                    time.sleep(0.1)

            except Exception as e:
                logger.error(f"Ошибка при обработке строки ответа: {e}")
                # Продолжаем цикл, возможно следующая строка будет читаться корректно

        # Закрываем соединение
        response.close()

        # Проверяем не было ли отмены или таймаута перед отправкой финального ответа
        if handle_cancellation() or check_timeout():
            return

    except requests.exceptions.Timeout:
        logger.error(f"Timeout при запросе к API для chat_id {chat_id}")
        update_queue.put({
            "chat_id": chat_id,
            "message_id": message_id,
            "text": "Сервер не отвечает. Пожалуйста, попробуйте позже.",
            "is_final": True,
            "error": True,
            "dialog_id": context.get("current_dialog_id", None),
            "is_reload": context.get("is_reload", False)
        })
        return
    except Exception as e:
        logger.error(f"Ошибка при потоковом получении ответа для chat_id {chat_id}: {e}")
        update_queue.put({
            "chat_id": chat_id,
            "message_id": message_id,
            "text": f"Произошла ошибка: {str(e)}",
            "is_final": True,
            "error": True,
            "dialog_id": context.get("current_dialog_id", None),
            "is_reload": context.get("is_reload", False)
        })
        return

    # Преобразуем финальный текст ответа для корректного отображения
    formatted_response = convert_markdown_to_html(full_response)

    # Финальная проверка отмены и таймаута
    if handle_cancellation() or check_timeout():
        return

    # Отправляем финальное обновление
    if formatted_response != last_response_txt:
        update_queue.put({
            "chat_id": chat_id,
            "message_id": message_id,
            "text": formatted_response,
            "is_final": True,
            "dialog_id": context.get("current_dialog_id", None),  # Передаем ID диалога
            "is_reload": context.get("is_reload", False),
            "user_id": context.get("user_id"),
            "model_name": context.get("model_name"),
            "model_id": context.get("model_id"),
            "user_ask": context.get("user_ask"),
            "dialog_number": context.get("dialog_number")
        })


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
                was_canceled = update_data.get("was_canceled", False)  # Флаг отмены
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

                        # Проверяем, является ли это перезагрузкой
                        is_reload = update_data.get("is_reload", False)

                        if is_reload:
                            # Если это перезагрузка, создаем новую запись
                            user_id = update_data.get("user_id")
                            dialog_number = update_data.get("dialog_number")
                            model_name = update_data.get("model_name")
                            model_id = update_data.get("model_id")
                            user_ask = update_data.get("user_ask")

                            if user_id and dialog_number and model_name and model_id and user_ask:
                                # Создаем новую запись с displayed = 1
                                new_dialog_id = db.log_dialog(
                                    id_chat=chat_id,
                                    id_user=user_id,
                                    number_dialog=dialog_number,
                                    model=model_name,
                                    model_id=model_id,
                                    user_ask=user_ask,
                                    model_answer=text,
                                    displayed=1
                                )
                                logger.info(f"Создана новая запись для перезагруженного ответа: {new_dialog_id}")

                                # Обновляем текущий диалог_id в контексте пользователя
                                if user_id and hasattr(context, 'dispatcher') and context.dispatcher:
                                    user_data = context.dispatcher.user_data.get(int(user_id), {})
                                    if user_data:
                                        user_data["current_dialog_id"] = new_dialog_id
                                        logger.info(
                                            f"Обновлен current_dialog_id для пользователя {user_id} на {new_dialog_id}")
                            else:
                                logger.error("Не хватает данных для создания новой записи при перезагрузке")
                        else:
                            # Если это обычный ответ, обновляем существующую запись
                            db.update_model_answer(dialog_id, text, displayed=1)
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
                                try:
                                    sent_msg = await context.bot.send_message(
                                        chat_id=chat_id,
                                        text=f"Часть {i + 1}/{len(chunks)}:\n\n{chunk}",
                                        reply_markup=reply_markup,
                                        parse_mode="HTML"  # Используем HTML форматирование
                                    )
                                except Exception as e:
                                    if "Can't parse entities" in str(e):
                                        logger.error(f"Ошибка HTML-разметки: {e}")
                                        # Очищаем текст от HTML-тегов
                                        clean_chunk = re.sub(r'<[^>]*>', '', chunk)
                                        sent_msg = await context.bot.send_message(
                                            chat_id=chat_id,
                                            text=f"Часть {i + 1}/{len(chunks)}:\n\n{clean_chunk}\n\n[Примечание: форматирование было удалено из-за ошибок разметки]",
                                            reply_markup=reply_markup
                                        )
                                    else:
                                        logger.error(f"Ошибка при отправке сообщения: {e}")
                                        continue

                                # Сохраняем ID последнего сообщения для потенциальной перезагрузки
                                if str(chat_id) in context.bot_data.get("active_streams", {}):
                                    del context.bot_data["active_streams"][str(chat_id)]

                                # Сохраняем информацию о последнем сообщении для перезагрузки
                                if hasattr(context, 'user_data_dict') and int(chat_id) in context.user_data_dict:
                                    user_data = context.user_data_dict[int(chat_id)]
                                    if "last_message" in user_data and user_data["last_message"]["text"]:
                                        user_data["last_message"]["id"] = f"{chat_id}_{sent_msg.message_id}"
                            else:
                                try:
                                    await context.bot.send_message(
                                        chat_id=chat_id,
                                        text=f"Часть {i + 1}/{len(chunks)}:\n\n{chunk}",
                                        parse_mode="HTML"  # Используем HTML форматирование
                                    )
                                except Exception as e:
                                    if "Can't parse entities" in str(e):
                                        logger.error(f"Ошибка HTML-разметки: {e}")
                                        # Очищаем текст от HTML-тегов
                                        clean_chunk = re.sub(r'<[^>]*>', '', chunk)
                                        await context.bot.send_message(
                                            chat_id=chat_id,
                                            text=f"Часть {i + 1}/{len(chunks)}:\n\n{clean_chunk}\n\n[Примечание: форматирование было удалено из-за ошибок разметки]"
                                        )
                                    else:
                                        logger.error(f"Ошибка при отправке сообщения: {e}")
                                        continue
                    else:
                        # Для незавершенного сообщения отображаем только первую часть
                        text_truncated = text[:4093] + "..."
                        try:
                            await context.bot.edit_message_text(
                                text=text_truncated,
                                chat_id=chat_id,
                                message_id=message_id,
                                reply_markup=reply_markup,
                                parse_mode="HTML"  # Используем HTML форматирование
                            )
                        except Exception as e:
                            if "Can't parse entities" in str(e):
                                logger.error(f"Ошибка HTML-разметки: {e}")
                                # Очищаем текст от HTML-тегов
                                clean_text = re.sub(r'<[^>]*>', '', text_truncated)
                                try:
                                    await context.bot.edit_message_text(
                                        text=f"{clean_text}\n\n[Примечание: форматирование было удалено из-за ошибок разметки]",
                                        chat_id=chat_id,
                                        message_id=message_id,
                                        reply_markup=reply_markup
                                    )
                                except Exception as inner_e:
                                    logger.error(f"Не удалось отправить даже очищенный текст: {inner_e}")
                            elif "Message is not modified" in str(e):
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
                            reply_markup=reply_markup,
                            parse_mode="HTML"  # Используем HTML форматирование
                        )

                        # Если это финальное сообщение
                        if is_final:
                            # Удаляем из активных потоков
                            if str(chat_id) in context.bot_data.get("active_streams", {}):
                                del context.bot_data["active_streams"][str(chat_id)]

                            # Обновляем идентификатор последнего сообщения для перезагрузки
                            try:
                                # Получаем user_id из update_data если есть
                                user_id = update_data.get("user_id")

                                # Если не указан user_id, пытаемся найти пользователя по chat_id
                                if not user_id and hasattr(context, 'user_data_dict'):
                                    # В PTB v20 контекст может содержать user_data_dict для доступа к данным пользователя
                                    if int(chat_id) in context.user_data_dict:
                                        user_data = context.user_data_dict[int(chat_id)]
                                        if "last_message" in user_data and user_data["last_message"]["text"]:
                                            user_data["last_message"]["id"] = f"{chat_id}_{message_id}"
                            except Exception as e:
                                logger.error(f"Ошибка при обновлении идентификатора последнего сообщения: {e}")
                    except Exception as e:
                        if "Can't parse entities" in str(e):
                            logger.error(f"Ошибка HTML-разметки: {e}")
                            # Пробуем отправить сообщение без HTML-разметки в случае ошибки
                            try:
                                # Очищаем текст от HTML-тегов
                                clean_text = re.sub(r'<[^>]*>', '', text)
                                await context.bot.edit_message_text(
                                    text=f"{clean_text}\n\n[Примечание: форматирование было удалено из-за ошибок разметки]",
                                    chat_id=chat_id,
                                    message_id=message_id,
                                    reply_markup=reply_markup
                                )

                                # Если это финальное сообщение, удаляем из активных потоков
                                if is_final and str(chat_id) in context.bot_data.get("active_streams", {}):
                                    del context.bot_data["active_streams"][str(chat_id)]
                            except Exception as inner_e:
                                logger.error(f"Не удалось отправить даже очищенный текст: {inner_e}")
                        elif "Message is not modified" in str(e):
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


async def process_ai_request(context, chat_id, user_message, is_reload=False):
    """Обработка запроса к AI модели и отправка ответа."""
    if "selected_model" not in context.user_data:
        await context.bot.send_message(
            chat_id=chat_id,
            text="Пожалуйста, сначала выберите модель с помощью команды /select_model"
        )
        return

    model_id = context.user_data["selected_model"]
    user_id = None

    # Получаем ID пользователя
    if hasattr(context, 'user_data_dict') and int(chat_id) in context.user_data_dict:
        user_data = context.user_data_dict[int(chat_id)]
        if 'id' in user_data:
            user_id = user_data['id']

    if not user_id:
        # Используем chat_id как user_id, если не можем найти
        user_id = chat_id

    # Получаем номер текущего диалога
    dialog_number = context.user_data.get("current_dialog", 1)

    # Подготавливаем контекст диалога
    db = context.bot_data.get("db")
    if db:
        messages, context_usage_percent = prepare_context(db, user_id, dialog_number, model_id, user_message)

        # Округляем процент до целого числа
        context_usage_percent = round(context_usage_percent)

        # Информируем пользователя о заполнении контекста
        if context_usage_percent > 70:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ Внимание: контекст модели заполнен на {context_usage_percent}%."
            )
    else:
        # Если нет доступа к БД, используем только текущее сообщение
        messages = [{"role": "user", "content": user_message}]
        context_usage_percent = 0

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
    thread_context = {
        "is_reload": is_reload,  # Флаг перезагрузки
        "messages": messages,  # Контекст диалога
        "context_usage_percent": context_usage_percent  # Процент заполнения контекста
    }

    if "current_dialog_id" in context.user_data:
        thread_context["current_dialog_id"] = context.user_data["current_dialog_id"]

    # Добавляем дополнительную информацию для перезагрузки
    if is_reload and "current_dialog_info" in context.user_data:
        thread_context.update(context.user_data["current_dialog_info"])

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

            # Проверяем, имеет ли пользователь право использовать эту модель
            is_admin = str(user_id) in config.ADMIN_IDS

            # Проверяем, бесплатная ли модель
            cursor = db.conn.cursor()
            cursor.execute(
                "SELECT is_free FROM models WHERE id = ?",
                (model_id,)
            )
            result = cursor.fetchone()

            is_free_model = True  # По умолчанию считаем модель бесплатной
            if result is not None:
                is_free_model = bool(result[0])

            # Если модель платная и пользователь не админ, сообщаем об ошибке
            if not is_free_model and not is_admin:
                await update.message.reply_text(
                    "⚠️ У вас нет доступа к этой модели. Пожалуйста, выберите бесплатную модель с помощью команды /select_model"
                )
                return

            # Находим название модели для логирования
            models = await get_available_models(context, user_id)
            model_name = model_id
            for model in models:
                if model["id"] == model_id:
                    model_name = model["name"]
                    break

            # Подготавливаем контекст диалога для оценки заполнения
            messages, context_usage_percent = prepare_context(db, user_id, context.user_data["current_dialog"],
                                                              model_id, user_message)

            # Если контекст заполнен более чем на 90%, предлагаем начать новый диалог
            if context_usage_percent > 90:
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📝 Начать новый диалог", callback_data="new_dialog")]
                ])
                await update.message.reply_text(
                    f"⚠️ Внимание: контекст модели заполнен на {round(context_usage_percent)}%.\n"
                    f"Рекомендуется начать новый диалог для лучшей производительности.",
                    reply_markup=keyboard
                )
                # Продолжаем выполнение - пользователь может проигнорировать рекомендацию

            # Логируем запрос пользователя (без ответа модели пока)
            dialog_id = db.log_dialog(
                id_chat=chat_id,
                id_user=user_id,
                number_dialog=context.user_data["current_dialog"],
                model=model_name,
                model_id=model_id,
                user_ask=user_message,
                displayed=1  # Новый запрос всегда отображается
            )

            # Сохраняем ID диалога для последующего обновления
            if dialog_id:
                context.user_data["current_dialog_id"] = dialog_id

                # Сохраняем дополнительную информацию для перезагрузок
                context.user_data["current_dialog_info"] = {
                    "user_id": user_id,
                    "dialog_number": context.user_data["current_dialog"],
                    "model_name": model_name,
                    "model_id": model_id,
                    "user_ask": user_message
                }
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


async def new_dialog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Начинает новый диалог."""
    user_id = update.effective_user.id

    # Получаем доступ к БД
    db = context.bot_data.get("db")
    if db:
        # Если текущий диалог существует, отмечаем его как завершенный
        if "current_dialog" in context.user_data:
            db.mark_last_message(user_id, context.user_data["current_dialog"])

        # Создаем новый диалог
        context.user_data["current_dialog"] = db.get_next_dialog_number(user_id)

        await update.message.reply_text(
            f"Начат новый диалог (номер {context.user_data['current_dialog']}). "
            f"Предыдущая история диалога не будет использоваться."
        )
    else:
        await update.message.reply_text("Ошибка доступа к базе данных.")


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик нажатий на кнопки."""
    query = update.callback_query
    await query.answer()

    data = query.data
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    is_admin = str(user_id) in config.ADMIN_IDS

    if data.startswith("model_"):
        # Извлекаем ID модели
        model_id = data[6:]

        # Проверяем, имеет ли пользователь право использовать эту модель
        db = context.bot_data.get("db")
        if db:
            cursor = db.conn.cursor()
            cursor.execute("SELECT is_free FROM models WHERE id = ?", (model_id,))
            result = cursor.fetchone()

            is_free_model = True  # По умолчанию считаем модель бесплатной
            if result is not None:
                is_free_model = bool(result[0])

            # Если модель платная и пользователь не админ, сообщаем об ошибке
            if not is_free_model and not is_admin:
                await query.edit_message_text(
                    "⚠️ У вас нет доступа к этой модели. Пожалуйста, выберите бесплатную модель."
                )
                return

        # Сохраняем модель
        context.user_data["selected_model"] = model_id

        # Находим название модели и описание для отображения
        models = await get_available_models(context, user_id)
        model_name = model_id
        model_description = "Нет описания"

        for model in models:
            if model["id"] == model_id:
                model_name = model["name"]
                model_description = model["description"]
                break

        # Если текущий диалог существует, отмечаем его как завершенный
        if db and "current_dialog" in context.user_data:
            db.mark_last_message(user_id, context.user_data["current_dialog"])
            # Создаем новый диалог при выборе новой модели
            context.user_data["current_dialog"] = db.get_next_dialog_number(user_id)

        # Для администраторов добавляем информацию о платности модели
        if is_admin and db:
            cursor = db.conn.cursor()
            cursor.execute(
                "SELECT prompt_price, completion_price, is_free FROM models WHERE id = ?",
                (model_id,)
            )
            result = cursor.fetchone()

            if result:
                prompt_price = result[0] or "0"
                completion_price = result[1] or "0"
                is_free = bool(result[2])

                pricing_info = (
                    f"Информация о цене:\n"
                    f"Цена за запрос: {prompt_price}\n"
                    f"Цена за ответ: {completion_price}\n"
                    f"Статус: {'Бесплатная' if is_free else 'Платная'}\n\n"
                )
            else:
                pricing_info = ""
        else:
            pricing_info = ""

        # Формируем сообщение с названием и описанием модели
        response_message = (
            f"Вы выбрали модель: {model_name}\n\n"
            f"{pricing_info}"
            f"Описание модели:\n{model_description}\n\n"
            "Теперь вы можете отправить сообщение, и я передам его выбранной AI модели."
        )

        # Если сообщение слишком длинное, сокращаем
        if len(response_message) > 4096:
            response_message = response_message[:4093] + "..."

        await query.edit_message_text(response_message)

    elif data.startswith("modelpage_"):
        # Обработка навигации по страницам
        if data == "modelpage_info":
            # Просто информация о текущей странице, ничего не делаем
            return

        # Получаем номер страницы
        page = int(data[10:])

        # Сохраняем страницу в контексте пользователя
        context.user_data["model_page"] = page

        # Получаем текущий фильтр
        current_filter = context.user_data.get("model_filter", "all")

        # Получаем модели с учетом фильтра
        models = await get_available_models(context, user_id)

        # Применяем фильтры
        if current_filter == "free":
            models = [model for model in models if model.get("is_free")]
        elif current_filter == "top":
            models = [model for model in models if model.get("top_model")]

        # Строим клавиатуру для новой страницы
        keyboard = build_model_keyboard(models, page)

        # Определяем текст в зависимости от фильтра
        filter_text = ""
        if current_filter == "free":
            filter_text = "(показаны только бесплатные модели)"
        elif current_filter == "top":
            filter_text = "(показаны только топовые модели)"

        # Определяем, есть ли уже выбранная модель
        selected_model_text = ""
        if "selected_model" in context.user_data:
            model_id = context.user_data["selected_model"]
            model_name = model_id

            # Ищем название выбранной модели
            for model in models:
                if model["id"] == model_id:
                    model_name = model["name"]
                    break

            selected_model_text = f"Текущая выбранная модель: {model_name}\n\n"

        # Обновляем сообщение с новой клавиатурой
        admin_info = "👑 Вам доступны все модели, включая платные (💰).\n\n" if is_admin else ""

        await query.edit_message_text(
            f"{admin_info}{selected_model_text}Выберите модель AI для общения {filter_text}:",
            reply_markup=keyboard
        )

    elif data.startswith("modelfilt_"):
        # Обработка фильтрации моделей
        filter_type = data[10:]  # free, top или all

        # Сохраняем фильтр в контексте пользователя
        context.user_data["model_filter"] = filter_type

        # Сбрасываем страницу на первую
        context.user_data["model_page"] = 0

        # Получаем модели с учетом нового фильтра
        models = await get_available_models(context, user_id)

        # Применяем фильтры
        if filter_type == "free":
            models = [model for model in models if model.get("is_free")]
        elif filter_type == "top":
            models = [model for model in models if model.get("top_model")]

        # Строим клавиатуру для новой страницы
        keyboard = build_model_keyboard(models, 0)

        # Определяем текст в зависимости от фильтра
        filter_text = ""
        if filter_type == "free":
            filter_text = "(показаны только бесплатные модели)"
        elif filter_type == "top":
            filter_text = "(показаны только топовые модели)"

        # Определяем, есть ли уже выбранная модель
        selected_model_text = ""
        if "selected_model" in context.user_data:
            model_id = context.user_data["selected_model"]
            model_name = model_id

            # Ищем название выбранной модели
            for model in models:
                if model["id"] == model_id:
                    model_name = model["name"]
                    break

            selected_model_text = f"Текущая выбранная модель: {model_name}\n\n"

        # Обновляем сообщение с новой клавиатурой
        admin_info = "👑 Вам доступны все модели, включая платные (💰).\n\n" if is_admin else ""

        await query.edit_message_text(
            f"{admin_info}{selected_model_text}Выберите модель AI для общения {filter_text}:",
            reply_markup=keyboard
        )

    elif data.startswith("reload_"):
        # Обработка перезагрузки ответа
        try:
            message_id_to_reload = int(data.split("_")[1])
            user_message = context.user_data.get("last_message", {}).get("text", "")

            if not user_message:
                await query.edit_message_text("Не удалось перезагрузить ответ: сообщение не найдено")
                return

            # Создаем новое сообщение для перезагрузки
            cancel_keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Остановить генерацию", callback_data="cancel_stream")
            ]])

            new_message = await context.bot.send_message(
                chat_id=chat_id,
                text="Генерирую новый ответ...",
                reply_markup=cancel_keyboard
            )

            # Устанавливаем флаг перезагрузки для правильного обновления в БД
            await process_ai_request(context, chat_id, user_message, is_reload=True)

        except Exception as e:
            logger.error(f"Ошибка при перезагрузке ответа: {e}")
            await query.edit_message_text(f"Ошибка при перезагрузке ответа: {str(e)}")

    elif data == "cancel_stream":
        # Обработка отмены потоковой передачи
        if "active_streams" in context.bot_data and str(chat_id) in context.bot_data["active_streams"]:
            cancel_event = context.bot_data["active_streams"][str(chat_id)]
            cancel_event.set()

            # Меняем кнопку на индикатор отмены
            await query.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⏳ Отмена генерации...", callback_data="cancel_stream_processing")
                ]])
            )

            # Ждем небольшое время для обработки отмены
            asyncio.create_task(wait_for_cancel_processing(context, chat_id, query.message.message_id))

    elif data == "cancel_stream_processing":
        # Обработка процесса отмены - просто информируем пользователя
        await query.answer("Генерация останавливается...")

    elif data == "cancel_stream":
        # Обработка отмены потоковой передачи
        if "active_streams" in context.bot_data and str(chat_id) in context.bot_data["active_streams"]:
            cancel_event = context.bot_data["active_streams"][str(chat_id)]
            cancel_event.set()

            # Просто удаляем кнопки, чтобы пользователь не мог нажать их повторно
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception as e:
                logger.error(f"Ошибка при удалении кнопок после отмены: {e}")

            # Отправляем уведомление об отмене
            await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ Генерация ответа остановлена."
            )

    elif data == "new_dialog":
        # Создание нового диалога
        db = context.bot_data.get("db")
        if db:
            # Если текущий диалог существует, отмечаем его как завершенный
            if "current_dialog" in context.user_data:
                db.mark_last_message(user_id, context.user_data["current_dialog"])

            # Создаем новый диалог
            context.user_data["current_dialog"] = db.get_next_dialog_number(user_id)

            # Обновляем сообщение с подтверждением
            await query.edit_message_text(
                f"Начат новый диалог (номер {context.user_data['current_dialog']}). "
                f"Предыдущая история диалога не будет использоваться."
            )
        else:
            await query.edit_message_text("Ошибка доступа к базе данных.")


async def wait_for_cancel_processing(context, chat_id, message_id, wait_time=1.5):
    """
    Ожидает некоторое время для обработки отмены генерации и обновляет кнопки в сообщении.

    Args:
        context: Контекст телеграм-бота
        chat_id: ID чата
        message_id: ID сообщения
        wait_time: Время ожидания в секундах
    """
    # Ждем указанное время для обработки отмены
    await asyncio.sleep(wait_time)

    # Обновляем кнопки, только если поток все еще активен
    if "active_streams" in context.bot_data and str(chat_id) in context.bot_data["active_streams"]:
        try:
            # Убираем кнопки совсем, так как генерация уже должна остановиться
            await context.bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=None
            )
        except Exception as e:
            logger.error(f"Ошибка при обновлении кнопок после отмены: {e}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка команды /start."""
    user = update.effective_user
    user_id = user.id
    chat_id = update.effective_chat.id

    # Определяем, является ли пользователь администратором
    is_admin = str(user_id) in config.ADMIN_IDS

    # Базовое приветственное сообщение
    welcome_message = (
        f"Привет, {user.first_name}! 👋\n\n"
        f"Я бот, который поможет вам взаимодействовать с различными моделями AI через OpenRouter.\n\n"
        f"Для начала работы выберите модель AI, используя команду /select_model.\n"
        f"Затем просто отправьте мне текстовое сообщение, и я передам его выбранной модели.\n\n"
    )

    # Дополнительная информация для администраторов
    if is_admin:
        admin_message = (
            "👑 Вы вошли как администратор!\n"
            "Вам доступны дополнительные команды и платные модели:\n"
            "/update_models - Обновить список моделей из API\n"
            "/translate_descriptions - Перевести описания моделей\n"
            "/translate_all - Перевести все описания моделей\n"
            "/set_description - Установить русское описание модели\n"
            "/set_top - Установить или снять статус топ-модели\n"
            "/list_models - Показать список моделей в БД\n\n"
        )
        welcome_message += admin_message

    welcome_message += f"Для получения дополнительной информации используйте команду /help."

    await update.message.reply_text(welcome_message)

    # Устанавливаем команды для пользователя
    await set_user_commands(context, user_id, chat_id)


async def set_user_commands(context, user_id, chat_id):
    """
    Устанавливает команды для конкретного пользователя.

    Args:
        context: Контекст телеграм-бота
        user_id: ID пользователя
        chat_id: ID чата
    """
    # Проверяем, является ли пользователь админом
    is_admin = str(user_id) in config.ADMIN_IDS

    # Базовые команды для всех пользователей
    base_commands = [
        BotCommand("start", "Начать работу с ботом"),
        BotCommand("help", "Показать сообщение помощи"),
        BotCommand("select_model", "Выбрать модель AI"),
        BotCommand("new_dialog", "Начать новый диалог")
    ]

    if is_admin:
        # Дополнительные команды для администраторов
        admin_commands = [
            BotCommand("update_models", "Обновить список моделей"),
            BotCommand("translate_descriptions", "Перевести описания моделей"),
            BotCommand("translate_all", "Перевести все описания"),
            BotCommand("set_description", "Установить описание модели"),
            BotCommand("set_top", "Установить топ-модель"),
            BotCommand("list_models", "Показать список моделей")
        ]
        # Объединяем базовые и админские команды
        commands = base_commands + admin_commands
    else:
        commands = base_commands

    try:
        # Пробуем разные варианты установки команд для конкретного чата
        try:
            # Вариант 1: Использование словаря для scope
            await context.bot.set_my_commands(
                commands=commands,
                scope={"type": "chat", "chat_id": chat_id}
            )
            logger.info(f"Команды установлены для чата {chat_id}")
        except Exception as e1:
            logger.warning(f"Не удалось установить команды (вариант 1): {e1}")

            try:
                # Вариант 2: Использование метода без scope
                await context.bot.delete_my_commands()  # Удаляем прежние команды
                await context.bot.set_my_commands(commands=commands)
                logger.info(f"Команды установлены глобально (вариант 2)")
            except Exception as e2:
                logger.error(f"Не удалось установить команды (вариант 2): {e2}")

    except Exception as e:
        logger.error(f"Ошибка при установке команд: {e}")


def build_model_keyboard(models, page=0, page_size=8):
    """
    Создает клавиатуру с моделями с поддержкой пагинации.

    Args:
        models: Список моделей
        page: Текущая страница (начиная с 0)
        page_size: Количество моделей на странице

    Returns:
        InlineKeyboardMarkup: Клавиатура с моделями и кнопками навигации
    """
    # Вычисляем общее количество страниц
    total_pages = (len(models) + page_size - 1) // page_size

    # Проверяем корректность номера страницы
    if page >= total_pages:
        page = total_pages - 1
    if page < 0:
        page = 0

    # Получаем модели для текущей страницы
    start = page * page_size
    end = min(start + page_size, len(models))
    current_models = models[start:end]

    # Создаем кнопки для моделей
    keyboard = []
    for model in current_models:
        # Добавляем эмодзи для топовых и платных моделей
        top_mark = "⭐️ " if model.get("top_model") else ""
        free_mark = "🆓 " if model.get("is_free") else "💰 "

        button_text = f"{top_mark}{free_mark}{model['name']}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"model_{model['id']}")])

    # Добавляем навигационные кнопки
    nav_buttons = []

    # Кнопка "Предыдущая страница"
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("◀️ Назад", callback_data=f"modelpage_{page - 1}"))

    # Индикатор страницы
    nav_buttons.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="modelpage_info"))

    # Кнопка "Следующая страница"
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Вперед ▶️", callback_data=f"modelpage_{page + 1}"))

    # Добавляем навигационную строку
    if total_pages > 1:  # Показываем только если есть несколько страниц
        keyboard.append(nav_buttons)

    # Добавляем опции фильтрации
    filter_buttons = [
        InlineKeyboardButton("🆓 Бесплатные", callback_data="modelfilt_free"),
        InlineKeyboardButton("⭐️ Топовые", callback_data="modelfilt_top"),
        InlineKeyboardButton("🔄 Все", callback_data="modelfilt_all"),
    ]
    keyboard.append(filter_buttons)

    return InlineKeyboardMarkup(keyboard)


async def set_bot_commands(context, is_admin=False, chat_id=None):
    """
    Устанавливает доступные команды меню в зависимости от статуса пользователя.

    Args:
        context: Контекст телеграм-бота
        is_admin: Является ли пользователь администратором
        chat_id: ID чата для установки команд (None = глобально)
    """
    # Базовые команды для всех пользователей
    base_commands = [
        BotCommand("start", "Начать работу с ботом"),
        BotCommand("help", "Показать сообщение помощи"),
        BotCommand("select_model", "Выбрать модель AI"),
        BotCommand("new_dialog", "Начать новый диалог")
    ]

    if is_admin:
        # Дополнительные команды для администраторов
        admin_commands = [
            BotCommand("update_models", "Обновить список моделей"),
            BotCommand("translate_descriptions", "Перевести описания моделей"),
            BotCommand("translate_all", "Перевести все описания"),
            BotCommand("set_description", "Установить описание модели"),
            BotCommand("set_top", "Установить топ-модель"),
            BotCommand("list_models", "Показать список моделей")
        ]
        # Объединяем базовые и админские команды
        commands = base_commands + admin_commands
    else:
        commands = base_commands

    try:
        if chat_id:
            # Пробуем установить команды для конкретного чата
            try:
                # Попытка использовать scope как словарь
                await context.bot.set_my_commands(
                    commands=commands,
                    scope={"type": "chat", "chat_id": chat_id}
                )
            except Exception as e:
                logger.warning(f"Не удалось установить команды для чата {chat_id}: {e}")
                # Устанавливаем глобально как запасной вариант
                await context.bot.set_my_commands(commands=commands)
        else:
            # Глобальная установка команд
            await context.bot.set_my_commands(commands=commands)
    except Exception as e:
        logger.error(f"Ошибка при установке команд: {e}")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка команды /help."""
    help_message = (
        "📚 *Как пользоваться ботом:*\n\n"
        "1️⃣ Выберите модель AI с помощью команды /select_model\n"
        "2️⃣ Отправьте текстовое сообщение, которое хотите передать модели\n"
        "3️⃣ Дождитесь ответа модели\n\n"
        "📋 *Доступные команды:*\n"
        "/start - Начать работу с ботом\n"
        "/help - Показать это сообщение помощи\n"
        "/select_model - Выбрать модель AI для общения\n"
        "/new_dialog - Начать новый диалог (сбросить контекст)\n\n"
        "💬 *О контексте диалога:*\n"
        "Бот сохраняет историю вашего диалога и передает её модели. "
        "Это позволяет вести непрерывную беседу, где модель помнит предыдущие сообщения. "
        "Если контекст заполняется на 90% и более, рекомендуется начать новый диалог "
        "с помощью команды /new_dialog.\n\n"
        "💡 *Примечание:* Вы всегда можете остановить генерацию ответа, нажав на кнопку 'Остановить генерацию'."
    )

    await update.message.reply_text(help_message)


async def get_available_models(context, user_id):
    """
    Получает список доступных моделей в зависимости от статуса пользователя.

    Args:
        context: Контекст телеграм-бота
        user_id: ID пользователя

    Returns:
        Список доступных моделей
    """
    # Проверяем, является ли пользователь админом
    is_admin = str(user_id) in config.ADMIN_IDS

    # Получаем доступ к БД
    db = context.bot_data.get("db")
    if db:
        if is_admin:
            # Для админов возвращаем все модели
            return db.get_models()
        else:
            # Для обычных пользователей возвращаем только бесплатные модели
            return db.get_models(only_free=True)

    # Если не удалось получить из БД, возвращаем стандартный список бесплатных моделей
    return []


async def select_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает список доступных моделей для выбора."""
    user_id = update.effective_user.id

    # Получаем страницу из контекста или используем 0
    page = context.user_data.get("model_page", 0)

    # Получаем текущий фильтр из контекста или используем "all"
    current_filter = context.user_data.get("model_filter", "all")

    # Получаем модели в зависимости от статуса пользователя и фильтра
    models = await get_available_models(context, user_id)

    # Применяем фильтры
    if current_filter == "free":
        models = [model for model in models if model.get("is_free")]
    elif current_filter == "top":
        models = [model for model in models if model.get("top_model")]

    # Построение клавиатуры с пагинацией
    keyboard = build_model_keyboard(models, page)

    # Определяем, есть ли уже выбранная модель
    selected_model_text = ""
    if "selected_model" in context.user_data:
        model_id = context.user_data["selected_model"]
        model_name = model_id

        # Ищем название выбранной модели
        for model in models:
            if model["id"] == model_id:
                model_name = model["name"]
                break

        selected_model_text = f"Текущая выбранная модель: {model_name}\n\n"

    # Определяем текст в зависимости от фильтра
    filter_text = ""
    if current_filter == "free":
        filter_text = "(показаны только бесплатные модели)"
    elif current_filter == "top":
        filter_text = "(показаны только топовые модели)"

    # Дополнительная информация для администраторов
    if str(user_id) in config.ADMIN_IDS:
        admin_info = "👑 Вам доступны все модели, включая платные (💰).\n\n"
    else:
        admin_info = ""

    # Отправляем сообщение с инлайн-клавиатурой
    await update.message.reply_text(
        f"{admin_info}{selected_model_text}Выберите модель AI для общения {filter_text}:",
        reply_markup=keyboard
    )


async def update_models_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обновляет список моделей из API."""
    user_id = update.effective_user.id

    # Проверяем, является ли пользователь админом
    if str(user_id) not in config.ADMIN_IDS:
        await update.message.reply_text("У вас нет прав для использования этой команды.")
        return

    # Отправляем сообщение о начале обновления
    message = await update.message.reply_text("Обновляю список моделей...")

    # Запускаем обновление в отдельном потоке, чтобы не блокировать бота
    def run_update():
        return fetch_and_update_models(context)

    # Запускаем в отдельном потоке
    success = await context.application.loop.run_in_executor(None, run_update)

    if success:
        await message.edit_text("Список моделей успешно обновлен!")
    else:
        await message.edit_text("Произошла ошибка при обновлении моделей. Подробности в логах.")


async def set_model_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Устанавливает русское описание для модели."""
    user_id = update.effective_user.id

    # Проверяем, является ли пользователь админом
    if str(user_id) not in config.ADMIN_IDS:
        await update.message.reply_text("У вас нет прав для использования этой команды.")
        return

    # Проверяем аргументы команды
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Используйте формат: /set_description model_id Описание модели на русском языке"
        )
        return

    model_id = context.args[0]
    description = " ".join(context.args[1:])

    # Получаем доступ к БД
    db = context.bot_data.get("db")
    if db:
        if db.update_model_description(model_id, description):
            await update.message.reply_text(f"Описание для модели {model_id} успешно обновлено!")
        else:
            await update.message.reply_text(f"Произошла ошибка при обновлении описания модели {model_id}.")
    else:
        await update.message.reply_text("Ошибка доступа к базе данных.")


async def set_top_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Устанавливает или снимает метку 'top_model' для модели."""
    user_id = update.effective_user.id

    # Проверяем, является ли пользователь админом
    if str(user_id) not in config.ADMIN_IDS:
        await update.message.reply_text("У вас нет прав для использования этой команды.")
        return

    # Проверяем аргументы команды
    if not context.args:
        await update.message.reply_text(
            "Используйте формат: /set_top model_id [0|1]"
        )
        return

    model_id = context.args[0]
    top_status = True

    # Если указан второй аргумент, используем его как статус
    if len(context.args) > 1:
        top_status = context.args[1] != "0"

    # Получаем доступ к БД
    db = context.bot_data.get("db")
    if db:
        # Если устанавливаем статус top_model, сначала сбрасываем для всех моделей
        if top_status:
            db.clear_top_models()

        if db.update_model_description(model_id, None, top_status):
            status_text = "добавлена в" if top_status else "удалена из"
            await update.message.reply_text(f"Модель {model_id} {status_text} топ-моделей!")
        else:
            await update.message.reply_text(f"Произошла ошибка при обновлении статуса модели {model_id}.")
    else:
        await update.message.reply_text("Ошибка доступа к базе данных.")


async def list_models(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает список моделей в БД."""
    user_id = update.effective_user.id

    # Проверяем, является ли пользователь админом
    if str(user_id) not in config.ADMIN_IDS:
        await update.message.reply_text("У вас нет прав для использования этой команды.")
        return

    # Получаем аргументы команды (если есть)
    filter_type = "all"
    if context.args and context.args[0] in ["free", "top", "all"]:
        filter_type = context.args[0]

    # Получаем доступ к БД
    db = context.bot_data.get("db")
    if db:
        only_free = (filter_type == "free")
        only_top = (filter_type == "top")

        models = db.get_models(only_free=only_free, only_top=only_top)

        if not models:
            await update.message.reply_text("Список моделей пуст.")
            return

        # Формируем сообщение со списком моделей
        message = f"Список моделей ({filter_type}):\n\n"

        for i, model in enumerate(models, 1):
            top_mark = "⭐️ " if model["top_model"] else ""
            free_mark = "🆓 " if model["is_free"] else ""

            model_info = (
                f"{i}. {top_mark}{free_mark}{model['name']}\n"
                f"ID: {model['id']}\n"
                f"Описание: {model['description'] or 'Нет описания'}\n\n"
            )

            # Если сообщение становится слишком длинным, отправляем его и начинаем новое
            if len(message + model_info) > 4000:
                await update.message.reply_text(message)
                message = model_info
            else:
                message += model_info

        # Отправляем оставшуюся часть сообщения
        if message:
            await update.message.reply_text(message)
    else:
        await update.message.reply_text("Ошибка доступа к базе данных.")


def estimate_tokens(text):
    """
    Оценивает количество токенов в тексте.
    Это простая эвристика: для английского текста ~4 символа на токен,
    для русского текста ~2 символа на токен.

    Args:
        text: Текст для оценки

    Returns:
        Примерное количество токенов
    """
    if not text:
        return 0

    # Определяем, какие символы преобладают - латинские или кириллические
    latin_chars = sum(1 for c in text if 'a' <= c.lower() <= 'z')
    cyrillic_chars = sum(1 for c in text if 'а' <= c.lower() <= 'я')

    # Если кириллических символов больше, считаем как русский текст
    if cyrillic_chars > latin_chars:
        tokens = len(text) / 2
    else:
        tokens = len(text) / 4

    # Добавим 5 токенов на роль и другие метаданные
    tokens += 5

    return int(tokens)


def prepare_context(db, user_id, dialog_number, model_id, current_message, max_context_size=None):
    """
    Подготавливает контекст диалога с учетом лимитов модели.

    Args:
        db: Экземпляр DBHandler
        user_id: ID пользователя
        dialog_number: Номер диалога
        model_id: ID модели
        current_message: Текущее сообщение пользователя
        max_context_size: Максимальный размер контекста (если None, берем из БД)

    Returns:
        (messages, context_usage_percent): Список сообщений для контекста и процент заполнения контекста
    """
    # Получаем лимит контекста для модели
    context_limit = max_context_size
    if not context_limit:
        cursor = db.conn.cursor()
        cursor.execute("SELECT context_length FROM models WHERE id = ?", (model_id,))
        result = cursor.fetchone()
        if result and result[0]:
            context_limit = result[0]
        else:
            # Если не нашли в БД, используем значение по умолчанию
            context_limit = 4096

    # Получаем историю диалога
    history = db.get_dialog_history(user_id, dialog_number)

    # Добавляем текущее сообщение
    messages = history + [{"role": "user", "content": current_message}]

    # Оцениваем общее количество токенов
    token_count = 0
    for message in messages:
        token_count += estimate_tokens(message["content"])

    # Если превышаем лимит, удаляем старые сообщения, пока не уложимся
    while token_count > context_limit * 0.95 and len(messages) > 1:
        # Удаляем самые старые сообщения
        removed_message = messages.pop(0)
        token_count -= estimate_tokens(removed_message["content"])

    # Вычисляем процент заполнения контекста
    context_usage_percent = (token_count / context_limit) * 100

    return messages, context_usage_percent


async def post_init(application: Application) -> None:
    """
    Выполняется после инициализации приложения, но до обработки обновлений.
    Устанавливает базовые команды для всех пользователей.
    """
    # Устанавливаем базовые команды для всех пользователей
    base_commands = [
        BotCommand("start", "Начать работу с ботом"),
        BotCommand("help", "Показать сообщение помощи"),
        BotCommand("select_model", "Выбрать модель AI"),
        BotCommand("new_dialog", "Начать новый диалог")
    ]

    try:
        # Глобальная установка базовых команд
        await application.bot.set_my_commands(commands=base_commands)
        logger.info("Базовые команды установлены успешно")
    except Exception as e:
        logger.error(f"Ошибка при установке базовых команд: {e}")


def main() -> None:
    """Запускает бота."""
    # Создаем обработчик обновлений
    global application
    application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    # Инициализируем базу данных
    db = DBHandler(config.DB_PATH)
    application.bot_data["db"] = db

    # Обновляем модели при запуске в отдельном потоке
    def update_models_at_startup():
        fetch_and_update_models(application)

    threading.Thread(target=update_models_at_startup).start()

    # Добавляем обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("select_model", select_model))
    application.add_handler(CommandHandler("new_dialog", new_dialog))

    # Команды для управления моделями (только для админов)
    application.add_handler(CommandHandler("update_models", update_models_command))
    application.add_handler(CommandHandler("set_description", set_model_description))
    application.add_handler(CommandHandler("set_top", set_top_model))
    application.add_handler(CommandHandler("list_models", list_models))
    application.add_handler(CommandHandler("translate_descriptions", translate_descriptions))
    application.add_handler(CommandHandler("translate_all", translate_all_models))

    # Добавляем обработчик инлайн-кнопок
    application.add_handler(CallbackQueryHandler(button_callback))

    # Добавляем обработчик текстовых сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Запускаем бота
    application.run_polling()





if __name__ == "__main__":
    main()