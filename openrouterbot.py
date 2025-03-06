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
        "is_reload": is_reload  # Флаг перезагрузки
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

            # Находим название модели для логирования
            models = await get_free_models(context)
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
        models = await get_free_models(context)
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
            callback_chat_id = parts[1]
            message_id = parts[2]

            if "last_message" in context.user_data:
                # Получаем последнее сообщение пользователя
                user_message = context.user_data["last_message"]["text"]

                # Отмечаем текущий ответ как неотображаемый в БД
                db = context.bot_data.get("db")
                if db and "current_dialog_id" in context.user_data:
                    current_dialog_id = context.user_data["current_dialog_id"]
                    # Отмечаем текущий ответ как неактивный (displayed = 0)
                    success = db.mark_previous_answers_as_inactive(current_dialog_id)
                    if not success:
                        logger.warning(f"Не удалось пометить ответ {current_dialog_id} как неактивный")

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

                # Повторно отправляем запрос с флагом перезагрузки
                await process_ai_request(
                    context,
                    query.message.chat_id,
                    user_message,
                    is_reload=True
                )
            else:
                await query.answer("Не могу найти предыдущий запрос")
        else:
            await query.answer("Некорректный формат данных")

    elif data == "cancel_stream":
        # Отмена потоковой передачи
        chat_id = str(query.message.chat_id)
        if chat_id in context.bot_data.get("active_streams", {}):
            # Устанавливаем флаг отмены
            context.bot_data["active_streams"][chat_id].set()
            # Добавляем визуальную индикацию, что запрос на отмену принят
            await query.answer("Останавливаем генерацию...")
            # Изменяем текст кнопки, чтобы показать, что запрос обрабатывается
            cancel_keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("⏱️ Останавливаем генерацию...", callback_data="cancel_stream_processing")
            ]])
            try:
                await query.edit_message_reply_markup(reply_markup=cancel_keyboard)
            except Exception as e:
                logger.error(f"Не удалось обновить кнопку: {e}")
        else:
            await query.answer("Поток уже завершен")

    elif data == "cancel_stream_processing":
        # Уже в процессе отмены, просто уведомляем пользователя
        await query.answer("Генерация останавливается...")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка команды /start."""
    user = update.effective_user

    welcome_message = (
        f"Привет, {user.first_name}! 👋\n\n"
        f"Я бот, который поможет вам взаимодействовать с различными моделями AI через OpenRouter.\n\n"
        f"Для начала работы выберите модель AI, используя команду /select_model.\n"
        f"Затем просто отправьте мне текстовое сообщение, и я передам его выбранной модели.\n\n"
        f"Для получения дополнительной информации используйте команду /help."
    )

    await update.message.reply_text(welcome_message)


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
        "/select_model - Выбрать модель AI для общения\n\n"
        "💡 *Примечание:* Вы всегда можете остановить генерацию ответа, нажав на кнопку 'Остановить генерацию'."
    )

    await update.message.reply_text(help_message)


async def select_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает список доступных моделей для выбора."""
    # Получаем список доступных моделей
    models = await get_free_models(context)

    # Создаем кнопки для каждой модели
    keyboard = []
    for model in models:
        # Добавляем эмодзи для топовых моделей
        top_mark = "⭐️ " if model.get("top_model") else ""
        free_mark = "🆓 " if model.get("is_free") else ""

        button_text = f"{top_mark}{free_mark}{model['name']}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"model_{model['id']}")])

    reply_markup = InlineKeyboardMarkup(keyboard)

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

    # Отправляем сообщение с инлайн-клавиатурой
    await update.message.reply_text(
        f"{selected_model_text}Выберите модель AI для общения:",
        reply_markup=reply_markup
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


def main() -> None:
    """Запускает бота."""
    # Создаем обработчик обновлений
    global application
    application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

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

    # Команды для управления моделями (только для админов)
    application.add_handler(CommandHandler("update_models", update_models_command))
    application.add_handler(CommandHandler("set_description", set_model_description))
    application.add_handler(CommandHandler("set_top", set_top_model))
    application.add_handler(CommandHandler("list_models", list_models))

    # Добавляем обработчик инлайн-кнопок
    application.add_handler(CallbackQueryHandler(button_callback))

    # Добавляем обработчик текстовых сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Запускаем бота
    application.run_polling()


if __name__ == "__main__":
    main()