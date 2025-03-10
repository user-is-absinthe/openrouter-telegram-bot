import os
import sqlite3
import logging
from datetime import datetime

# Настройка логирования
logger = logging.getLogger(__name__)


class DBHandler:
    def __init__(self, db_path):
        """Инициализация подключения к базе данных."""
        self.db_path = db_path
        self.conn = None

        # Создаем директорию для базы данных, если она не существует
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        # Подключение к базе данных
        self.connect()

        # Создание необходимых таблиц, если они не существуют
        self.create_tables()

        # Обновление схемы базы данных, если необходимо
        self.update_schema()

    def connect(self):
        """Подключение к базе данных SQLite."""
        try:
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self.conn.execute("PRAGMA foreign_keys = ON")
        except Exception as e:
            logger.error(f"Ошибка подключения к базе данных: {e}")

    def create_tables(self):
        """Создание необходимых таблиц."""
        try:
            cursor = self.conn.cursor()

            # Создание таблицы пользователей
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                id_chat INTEGER NOT NULL,
                id_user INTEGER NOT NULL,
                first_name TEXT,
                last_name TEXT,
                username TEXT,
                register_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                is_premium INTEGER DEFAULT 0,
                UNIQUE(id_chat, id_user)
            )
            ''')

            # Создание таблицы диалогов с полем displayed
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS dialogs (
                id INTEGER PRIMARY KEY,
                id_chat INTEGER NOT NULL,
                id_user INTEGER NOT NULL,
                number_dialog INTEGER NOT NULL,
                model TEXT,
                model_id TEXT,
                user_ask TEXT,
                model_answer TEXT,
                ask_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                displayed INTEGER DEFAULT 1,
                FOREIGN KEY (id_chat, id_user) REFERENCES users (id_chat, id_user)
            )
            ''')

            # Создание таблицы моделей
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS models (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created INTEGER,
                description TEXT,
                rus_description TEXT,
                context_length INTEGER,
                modality TEXT,
                tokenizer TEXT,
                instruct_type TEXT,
                prompt_price TEXT,
                completion_price TEXT,
                image_price TEXT,
                request_price TEXT,
                provider_context_length INTEGER,
                is_moderated INTEGER,
                is_free INTEGER,
                top_model INTEGER DEFAULT 0,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            ''')

            self.conn.commit()
        except Exception as e:
            logger.error(f"Ошибка создания таблиц: {e}")

    def update_schema(self):
        """Обновление схемы базы данных при необходимости."""
        try:
            cursor = self.conn.cursor()

            # Проверяем, существует ли колонка 'displayed' в таблице 'dialogs'
            cursor.execute("PRAGMA table_info(dialogs)")
            columns = [column[1] for column in cursor.fetchall()]

            if 'displayed' not in columns:
                logger.info("Добавление колонки 'displayed' в таблицу 'dialogs'")
                cursor.execute("ALTER TABLE dialogs ADD COLUMN displayed INTEGER DEFAULT 1")
                self.conn.commit()

                # Устанавливаем значение по умолчанию для существующих записей
                cursor.execute("UPDATE dialogs SET displayed = 1 WHERE displayed IS NULL")
                self.conn.commit()

            # Проверяем, существует ли таблица 'models'
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='models'")
            if not cursor.fetchone():
                logger.info("Создание таблицы 'models'")
                cursor.execute('''
                CREATE TABLE models (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created INTEGER,
                    description TEXT,
                    rus_description TEXT,
                    context_length INTEGER,
                    modality TEXT,
                    tokenizer TEXT,
                    instruct_type TEXT,
                    prompt_price TEXT,
                    completion_price TEXT,
                    image_price TEXT,
                    request_price TEXT,
                    provider_context_length INTEGER,
                    is_moderated INTEGER,
                    is_free INTEGER,
                    top_model INTEGER DEFAULT 0,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                ''')
                self.conn.commit()

            # Проверяем, существует ли колонка 'is_premium' в таблице 'users'
            cursor.execute("PRAGMA table_info(users)")
            columns = [column[1] for column in cursor.fetchall()]

            if 'is_premium' not in columns:
                logger.info("Добавление колонки 'is_premium' в таблицу 'users'")
                cursor.execute("ALTER TABLE users ADD COLUMN is_premium INTEGER DEFAULT 0")
                self.conn.commit()

                # Устанавливаем значение по умолчанию для существующих записей
                cursor.execute("UPDATE users SET is_premium = 0 WHERE is_premium IS NULL")
                self.conn.commit()

        except Exception as e:
            logger.error(f"Ошибка обновления схемы базы данных: {e}")

    def close(self):
        """Закрытие соединения с базой данных."""
        if self.conn:
            self.conn.close()

    def register_user(self, id_chat, id_user, first_name, last_name, username, is_premium=None):
        """Регистрирует пользователя или обновляет его информацию."""
        try:
            cursor = self.conn.cursor()

            # Проверка, существует ли пользователь
            cursor.execute("SELECT id, is_premium FROM users WHERE id_chat = ? AND id_user = ?", (id_chat, id_user))
            result = cursor.fetchone()

            if result:
                # Обновление существующего пользователя
                # Если is_premium не указан, сохраняем текущее значение
                current_premium = result[1] if is_premium is None else is_premium

                cursor.execute(
                    "UPDATE users SET first_name = ?, last_name = ?, username = ?, is_premium = ? WHERE id_chat = ? AND id_user = ?",
                    (first_name, last_name, username, current_premium, id_chat, id_user)
                )
            else:
                # Регистрация нового пользователя
                # По умолчанию не премиум
                premium_status = 0 if is_premium is None else (1 if is_premium else 0)

                cursor.execute(
                    "INSERT INTO users (id_chat, id_user, first_name, last_name, username, is_premium) VALUES (?, ?, ?, ?, ?, ?)",
                    (id_chat, id_user, first_name, last_name, username, premium_status)
                )

            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка при регистрации пользователя: {e}")
            return False

    def log_dialog(self, id_chat, id_user, number_dialog, model, model_id, user_ask, model_answer=None, displayed=1):
        """Логирует диалог."""
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT INTO dialogs (id_chat, id_user, number_dialog, model, model_id, user_ask, model_answer, displayed) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (id_chat, id_user, number_dialog, model, model_id, user_ask, model_answer, displayed)
            )
            self.conn.commit()
            return cursor.lastrowid  # Возвращаем ID вставленной записи
        except Exception as e:
            logger.error(f"Ошибка при логировании диалога: {e}")
            return None

    def update_model_answer(self, dialog_id, model_answer, displayed=1):
        """Обновляет ответ модели в существующей записи диалога."""
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "UPDATE dialogs SET model_answer = ?, displayed = ? WHERE id = ?",
                (model_answer, displayed, dialog_id)
            )
            self.conn.commit()
        except Exception as e:
            logger.error(f"Ошибка при обновлении ответа модели: {e}")

    def get_next_dialog_number(self, id_user):
        """Получает следующий номер диалога для пользователя."""
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT MAX(number_dialog) FROM dialogs WHERE id_user = ?",
                (id_user,)
            )
            result = cursor.fetchone()[0]

            # Если это первый диалог пользователя
            if result is None:
                return 1

            # Иначе увеличиваем номер диалога на 1
            return result + 1
        except Exception as e:
            logger.error(f"Ошибка при получении номера диалога: {e}")
            return 1  # В случае ошибки возвращаем 1

    def mark_last_message(self, id_user, number_dialog):
        """Отмечает, что текущий диалог завершен."""
        try:
            cursor = self.conn.cursor()
            # Фиктивная операция для маркировки завершения диалога
            # В будущем можно добавить специальное поле в таблицу
            cursor.execute(
                "SELECT MAX(id) FROM dialogs WHERE id_user = ? AND number_dialog = ?",
                (id_user, number_dialog)
            )
            self.conn.commit()
        except Exception as e:
            logger.error(f"Ошибка при маркировке завершения диалога: {e}")

    def mark_previous_answers_as_inactive(self, dialog_id):
        """Помечает предыдущий ответ модели как неотображаемый."""
        try:
            cursor = self.conn.cursor()
            # Обновляем только текущий ответ как неотображаемый
            cursor.execute(
                "UPDATE dialogs SET displayed = 0 WHERE id = ?",
                (dialog_id,)
            )
            self.conn.commit()
            logger.info(f"Ответ {dialog_id} помечен как неактивный")
            return True
        except Exception as e:
            logger.error(f"Ошибка при обновлении статуса ответа: {e}")
            return False

    # Методы для работы с моделями
    def save_model(self, model_data):
        """Сохраняет или обновляет информацию о модели в БД."""
        try:
            # Извлекаем данные из JSON
            model_id = model_data.get("id")
            name = model_data.get("name")
            created = model_data.get("created")
            description = model_data.get("description")
            context_length = model_data.get("context_length")

            # Извлекаем данные из вложенных структур
            architecture = model_data.get("architecture", {})
            modality = architecture.get("modality")
            tokenizer = architecture.get("tokenizer")
            instruct_type = architecture.get("instruct_type")

            pricing = model_data.get("pricing", {})
            prompt_price = pricing.get("prompt")
            completion_price = pricing.get("completion")
            image_price = pricing.get("image")
            request_price = pricing.get("request")

            top_provider = model_data.get("top_provider", {})
            provider_context_length = top_provider.get("context_length")
            is_moderated = 1 if top_provider.get("is_moderated") else 0

            # Проверяем, является ли модель бесплатной
            is_free = 1 if model_id.endswith(":free") or (prompt_price == "0" and completion_price == "0") else 0

            cursor = self.conn.cursor()

            # Проверяем, есть ли уже такая модель в БД
            cursor.execute("SELECT id, rus_description, top_model FROM models WHERE id = ?", (model_id,))
            existing = cursor.fetchone()

            if existing:
                # Сохраняем текущие значения rus_description и top_model
                rus_description = existing[1]
                top_model = existing[2]

                # Обновляем существующую запись, сохраняя rus_description и top_model
                cursor.execute("""
                UPDATE models SET 
                    name = ?, 
                    created = ?, 
                    description = ?,
                    context_length = ?,
                    modality = ?,
                    tokenizer = ?,
                    instruct_type = ?,
                    prompt_price = ?,
                    completion_price = ?,
                    image_price = ?,
                    request_price = ?,
                    provider_context_length = ?,
                    is_moderated = ?,
                    is_free = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """, (
                    name, created, description, context_length, modality, tokenizer,
                    instruct_type, prompt_price, completion_price, image_price,
                    request_price, provider_context_length, is_moderated, is_free,
                    model_id
                ))
            else:
                # Добавляем новую запись
                cursor.execute("""
                INSERT INTO models (
                    id, name, created, description, rus_description,
                    context_length, modality, tokenizer, instruct_type,
                    prompt_price, completion_price, image_price, request_price,
                    provider_context_length, is_moderated, is_free, top_model
                ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """, (
                    model_id, name, created, description, context_length, modality, tokenizer,
                    instruct_type, prompt_price, completion_price, image_price,
                    request_price, provider_context_length, is_moderated, is_free
                ))

            self.conn.commit()
            return True

        except Exception as e:
            logger.error(f"Ошибка при сохранении модели {model_data.get('id')}: {e}")
            return False

    def get_models(self, only_free=False, only_top=False):
        """Получает список моделей из БД с возможностью фильтрации."""
        try:
            cursor = self.conn.cursor()

            query = "SELECT id, name, description, rus_description, context_length, is_free, top_model FROM models"
            conditions = []
            params = []

            if only_free:
                conditions.append("is_free = 1")

            if only_top:
                conditions.append("top_model = 1")

            if conditions:
                query += " WHERE " + " AND ".join(conditions)

            # Сортировка: сначала топовые, затем по имени
            query += " ORDER BY top_model DESC, name ASC"

            cursor.execute(query, params)

            models = []
            for row in cursor.fetchall():
                model = {
                    "id": row[0],
                    "name": row[1],
                    "description": row[3] if row[3] else row[2],  # Используем rus_description, если есть
                    "context_length": row[4],
                    "is_free": bool(row[5]),
                    "top_model": bool(row[6])
                }
                models.append(model)

            return models

        except Exception as e:
            logger.error(f"Ошибка при получении списка моделей: {e}")
            return []

    def set_model_description_ru(self, model_id, rus_description):
        """Обновляет русское описание модели."""
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "UPDATE models SET rus_description = ? WHERE id = ?",
                (rus_description, model_id)
            )
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка при обновлении русского описания модели {model_id}: {e}")
            return False

    def update_model_description(self, model_id, rus_description, top_model=None):
        """Обновляет русское описание и/или статус топ-модели."""
        try:
            cursor = self.conn.cursor()

            # Формируем запрос в зависимости от того, что обновляем
            if top_model is not None:
                cursor.execute(
                    "UPDATE models SET rus_description = ?, top_model = ? WHERE id = ?",
                    (rus_description, 1 if top_model else 0, model_id)
                )
            else:
                cursor.execute(
                    "UPDATE models SET rus_description = ? WHERE id = ?",
                    (rus_description, model_id)
                )

            self.conn.commit()
            return True

        except Exception as e:
            logger.error(f"Ошибка при обновлении описания модели {model_id}: {e}")
            return False

    def clear_top_models(self):
        """Сбрасывает статус топ-модели для всех моделей."""
        try:
            cursor = self.conn.cursor()
            cursor.execute("UPDATE models SET top_model = 0")
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка при сбросе статуса топ-моделей: {e}")
            return False

    def get_models_for_translation(self, model_id=None):
        """
        Получает список моделей для перевода.

        Args:
            model_id: ID конкретной модели или None для всех моделей без русского описания

        Returns:
            Список кортежей (id, description) моделей для перевода
        """
        try:
            cursor = self.conn.cursor()

            if model_id:
                # Получаем конкретную модель
                cursor.execute(
                    "SELECT id, description FROM models WHERE id = ?",
                    (model_id,)
                )
            else:
                # Получаем все модели с пустым русским описанием
                cursor.execute(
                    "SELECT id, description FROM models WHERE rus_description IS NULL OR rus_description = ''"
                )

            return cursor.fetchall()

        except Exception as e:
            logger.error(f"Ошибка при получении моделей для перевода: {e}")
            return []

    def get_dialog_history(self, id_user, number_dialog, limit=None):
        """
        Получает историю диалога пользователя.

        Args:
            id_user: ID пользователя
            number_dialog: Номер диалога
            limit: Максимальное количество сообщений для возврата (None = все сообщения)

        Returns:
            Список словарей с сообщениями диалога [{"role": "user/assistant", "content": "..."}]
        """
        try:
            cursor = self.conn.cursor()

            query = """
            SELECT user_ask, model_answer 
            FROM dialogs 
            WHERE id_user = ? AND number_dialog = ? AND displayed = 1 
            ORDER BY id ASC
            """

            params = [id_user, number_dialog]

            if limit:
                query += " LIMIT ?"
                params.append(limit)

            cursor.execute(query, params)
            rows = cursor.fetchall()

            history = []
            for row in rows:
                # Добавляем сообщение пользователя
                if row[0]:  # user_ask
                    history.append({
                        "role": "user",
                        "content": row[0]
                    })

                # Добавляем ответ модели
                if row[1]:  # model_answer
                    history.append({
                        "role": "assistant",
                        "content": row[1]
                    })

            return history

        except Exception as e:
            logger.error(f"Ошибка при получении истории диалога: {e}")
            return []

    def set_premium_status(self, user_id, is_premium=True):
        """
        Устанавливает или снимает премиум-статус пользователя.

        Args:
            user_id: ID пользователя
            is_premium: True для установки премиум, False для снятия

        Returns:
            bool: True при успешном обновлении, False при ошибке
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "UPDATE users SET is_premium = ? WHERE id_user = ?",
                (1 if is_premium else 0, user_id)
            )
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка при обновлении премиум-статуса пользователя {user_id}: {e}")
            return False

    def is_premium_user(self, user_id):
        """
        Проверяет, является ли пользователь премиум-пользователем.

        Args:
            user_id: ID пользователя

        Returns:
            bool: True, если пользователь имеет премиум-статус, иначе False
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT is_premium FROM users WHERE id_user = ?",
                (user_id,)
            )
            result = cursor.fetchone()
            if result:
                return bool(result[0])
            return False
        except Exception as e:
            logger.error(f"Ошибка при проверке премиум-статуса пользователя {user_id}: {e}")
            return False

    def check_user_exists_by_id(self, user_id):
        """
        Проверяет, существует ли пользователь с указанным ID.

        Args:
            user_id: ID пользователя

        Returns:
            bool: True, если пользователь существует, иначе False
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT 1 FROM users WHERE id_user = ? LIMIT 1", (user_id,))
            return cursor.fetchone() is not None
        except Exception as e:
            logger.error(f"Ошибка при проверке существования пользователя: {e}")
            return False

    def get_user_id_by_username(self, username):
        """
        Получает ID пользователя по его username.

        Args:
            username: имя пользователя (без символа @)

        Returns:
            int: ID пользователя или None, если пользователь не найден
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT id_user FROM users WHERE username = ? LIMIT 1", (username,))
            result = cursor.fetchone()
            return result[0] if result else None
        except Exception as e:
            logger.error(f"Ошибка при получении ID пользователя по username: {e}")
            return None

    def get_user_info(self, user_id):
        """
        Получает информацию о пользователе по его ID.

        Args:
            user_id: ID пользователя

        Returns:
            dict: Словарь с информацией о пользователе или None, если пользователь не найден
        """
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT first_name, last_name, username, is_premium FROM users WHERE id_user = ?",
                (user_id,)
            )
            result = cursor.fetchone()

            if result:
                return {
                    'first_name': result[0],
                    'last_name': result[1],
                    'username': result[2],
                    'is_premium': bool(result[3])
                }
            return None
        except Exception as e:
            logger.error(f"Ошибка при получении информации о пользователе: {e}")
            return None
