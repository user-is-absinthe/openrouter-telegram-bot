import sqlite3
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)


class DBHandler:
    def __init__(self, db_path="openrouter_bot.db"):
        """Инициализация обработчика базы данных SQLite"""
        self.db_path = db_path
        self.connection = None
        self.connect()
        self.create_tables()

    def connect(self):
        """Установить соединение с базой данных"""
        try:
            # Проверяем, существует ли директория для базы данных
            db_dir = os.path.dirname(self.db_path)
            if db_dir and not os.path.exists(db_dir):
                os.makedirs(db_dir)

            self.connection = sqlite3.connect(self.db_path, check_same_thread=False)
            # Настраиваем возврат строк в виде словарей
            self.connection.row_factory = sqlite3.Row
            logger.info(f"Подключение к SQLite базе данных '{self.db_path}' установлено")
        except sqlite3.Error as err:
            logger.error(f"Ошибка при подключении к базе данных: {err}")
            raise

    def check_connection(self):
        """Проверяет и восстанавливает соединение с БД"""
        try:
            # Для SQLite просто проверяем, что соединение существует
            if self.connection is None:
                logger.warning("Соединение с базой данных потеряно. Повторное подключение...")
                self.connect()

            # Проверяем соединение простым запросом
            cursor = self.connection.cursor()
            cursor.execute("SELECT 1")
            cursor.close()
        except sqlite3.Error as e:
            logger.error(f"Ошибка при проверке соединения: {e}")
            self.connect()

    def create_tables(self):
        """Создает таблицы, если они не существуют"""
        try:
            self.check_connection()
            cursor = self.connection.cursor()

            # Создание таблицы Users
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS Users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    id_chat INTEGER NOT NULL,
                    id_user INTEGER NOT NULL,
                    first_name TEXT,
                    last_name TEXT,
                    username TEXT,
                    is_active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(id_user)
                )
            ''')

            # Создание таблицы Dialogs
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS Dialogs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    id_chat INTEGER NOT NULL,
                    id_user INTEGER NOT NULL,  -- Добавляем id пользователя для группировки диалогов по пользователям
                    number_dialog INTEGER NOT NULL,
                    last_message INTEGER DEFAULT 0,
                    model TEXT NOT NULL,       -- Название модели для отображения
                    model_id TEXT NOT NULL,    -- Полный ID модели для API
                    user_ask TEXT NOT NULL,
                    model_answered TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (id_user) REFERENCES Users(id_user)
                )
            ''')

            # Создаем индексы для быстрого поиска диалогов
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_chat_dialog ON Dialogs (id_chat, number_dialog)
            ''')

            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_user_dialog ON Dialogs (id_user, number_dialog)
            ''')

            self.connection.commit()
            cursor.close()
            logger.info("Таблицы успешно созданы или уже существуют")
        except sqlite3.Error as err:
            logger.error(f"Ошибка при создании таблиц: {err}")
            raise

    def register_user(self, id_chat, id_user, first_name, last_name, username):
        """Регистрирует или обновляет пользователя в БД"""
        try:
            self.check_connection()
            cursor = self.connection.cursor()

            # Проверяем, существует ли пользователь
            cursor.execute(
                "SELECT id FROM Users WHERE id_user = ?",
                (id_user,)
            )
            user = cursor.fetchone()

            if user:
                # Обновляем данные пользователя
                cursor.execute(
                    """
                    UPDATE Users 
                    SET id_chat = ?, first_name = ?, last_name = ?, username = ?, is_active = 1
                    WHERE id_user = ?
                    """,
                    (id_chat, first_name, last_name, username, id_user)
                )
            else:
                # Создаем нового пользователя
                cursor.execute(
                    """
                    INSERT INTO Users (id_chat, id_user, first_name, last_name, username) 
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (id_chat, id_user, first_name, last_name, username)
                )

            self.connection.commit()
            cursor.close()
            logger.info(f"Пользователь {id_user} зарегистрирован/обновлен в базе данных")
        except sqlite3.Error as err:
            logger.error(f"Ошибка при регистрации пользователя: {err}")

    def mark_user_inactive(self, id_user):
        """Отмечает пользователя как неактивного (заблокировавшего бота)"""
        try:
            self.check_connection()
            cursor = self.connection.cursor()

            cursor.execute(
                "UPDATE Users SET is_active = 0 WHERE id_user = ?",
                (id_user,)
            )

            self.connection.commit()
            cursor.close()
            logger.info(f"Пользователь {id_user} отмечен как неактивный")
        except sqlite3.Error as err:
            logger.error(f"Ошибка при обновлении статуса пользователя: {err}")

    def get_next_dialog_number(self, id_user):
        """Получает следующий номер диалога для пользователя"""
        try:
            self.check_connection()
            cursor = self.connection.cursor()

            cursor.execute(
                "SELECT MAX(number_dialog) as max_dialog FROM Dialogs WHERE id_user = ?",
                (id_user,)
            )
            result = cursor.fetchone()
            cursor.close()

            if result is None or result["max_dialog"] is None:
                return 1
            else:
                return result["max_dialog"] + 1
        except sqlite3.Error as err:
            logger.error(f"Ошибка при получении номера диалога: {err}")
            return 1

    def mark_last_message(self, id_user, number_dialog):
        """Отмечает последнее сообщение диалога"""
        try:
            self.check_connection()
            cursor = self.connection.cursor()

            cursor.execute(
                """
                UPDATE Dialogs 
                SET last_message = 1
                WHERE id_user = ? AND number_dialog = ?
                """,
                (id_user, number_dialog)
            )

            self.connection.commit()
            cursor.close()
            logger.info(f"Диалог {number_dialog} пользователя {id_user} отмечен как завершенный")
        except sqlite3.Error as err:
            logger.error(f"Ошибка при маркировке последнего сообщения: {err}")

    def log_dialog(self, id_chat, id_user, number_dialog, model, model_id, user_ask, model_answered=None,
                   last_message=0):
        """Логирует диалог в базу данных с полным ID модели"""
        try:
            self.check_connection()
            cursor = self.connection.cursor()

            cursor.execute(
                """
                INSERT INTO Dialogs (id_chat, id_user, number_dialog, model, model_id, user_ask, model_answered, last_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (id_chat, id_user, number_dialog, model, model_id, user_ask, model_answered, last_message)
            )

            dialog_id = cursor.lastrowid

            self.connection.commit()
            cursor.close()
            logger.info(f"Диалог {number_dialog} пользователя {id_user} сохранен в БД")
            return dialog_id
        except sqlite3.Error as err:
            logger.error(f"Ошибка при логировании диалога: {err}")
            return None

    def update_model_answer(self, dialog_id, model_answered):
        """Обновляет ответ модели для указанного диалога"""
        try:
            self.check_connection()
            cursor = self.connection.cursor()

            cursor.execute(
                "UPDATE Dialogs SET model_answered = ? WHERE id = ?",
                (model_answered, dialog_id)
            )

            self.connection.commit()
            cursor.close()
            logger.info(f"Ответ модели для диалога {dialog_id} обновлен")
        except sqlite3.Error as err:
            logger.error(f"Ошибка при обновлении ответа модели: {err}")

    def close(self):
        """Закрывает соединение с базой данных"""
        if self.connection:
            self.connection.close()
            logger.info("Соединение с базой данных закрыто")
