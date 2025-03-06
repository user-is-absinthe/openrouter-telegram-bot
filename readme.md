```markdown
# OpenRouter Telegram Bot

Телеграм бот для общения с AI моделями через сервис OpenRouter. Позволяет выбирать и общаться с различными бесплатными языковыми моделями.

## Характеристики

- Выбор бесплатных моделей из каталога OpenRouter
- Потоковая генерация ответов с обновлением в реальном времени
- Возможность остановить генерацию ответа в любой момент
- Перезапуск генерации для получения альтернативного ответа
- Поддержка длинных ответов с автоматическим разбиением на части
- Сохранение истории диалогов в SQLite базе данных
- Корректное отображение форматированного текста в Telegram

## Установка

1. Клонируйте репозиторий:
   ```bash
   git clone https://github.com/user-is-absinthe/openrouter-telegram-bot.git
   cd openrouter-telegram-bot
   ```

2. Создайте и активируйте виртуальное окружение:
   ```bash
   python -m venv venv
   # Linux/macOS
   source venv/bin/activate
   # Windows
   venv\Scripts\activate
   ```

3. Установите необходимые зависимости:
   ```bash
   pip install python-telegram-bot requests md2tgmd
   ```

4. Создайте и настройте файл `config.py`:
   ```python
   # Токены и ключи API
   TELEGRAM_BOT_TOKEN = "tg_token"
   OPENROUTER_API_KEY = "op_token"
   
   DB_PATH = r"data/openrouter_bot.db"
   
   # Настройки сайта для OpenRouter
   SITE_URL = "https://github.com/user-is-absinthe/openrouter-telegram-bot"
   SITE_NAME = "OpenRouter Telegram Bot"
   
   # Настройки обновления сообщений
   STREAM_UPDATE_INTERVAL = 1.5  # Интервал обновления сообщений в секундах при потоковой передаче
   
   # Добавляем поле для ID администраторов (список строк)
   ADMIN_IDS = ["YOUR_ADMIN_ID_1", "YOUR_ADMIN_ID_2"]
   # ADMIN_IDS = ["YOUR_ADMIN_ID_2"]

   ```

5. Запустите бота:
   ```bash
   python openrouterbot.py
   ```

## Использование

1. Отправьте команду `/start` вашему боту в Telegram, чтобы начать взаимодействие.
2. Используйте команду `/select_model` для выбора бесплатной AI модели из списка.
3. После выбора модели отправьте любое текстовое сообщение, и бот передаст его выбранной модели.
4. Во время генерации ответа вы можете нажать кнопку "Остановить генерацию", чтобы прервать процесс.
5. После получения ответа вы можете нажать кнопку "Перезагрузить ответ", чтобы получить новый ответ на тот же запрос.
6. Используйте команду `/help` для получения справки по всем доступным командам.

## Структура проекта

- `openrouterbot.py` - основной файл с логикой телеграм-бота
- `db_handler.py` - обработчик для работы с SQLite базой данных
- `config.py` - файл с конфигурационными параметрами
- `data/openrouter_bot.db` - файл базы данных SQLite (создается автоматически)

## Чеклист для будущих улучшений

- [ ] Вывод "мыслей" думающих моделей
- [x] Исправить форматирование markdown для Telegram
- [ ] Добавить моделям контекст для продолжения диалога
- [ ] Добавить возможность сохранять любимые модели
- [ ] Добавить статистику использования моделей

## База данных

Бот использует SQLite для хранения информации о пользователях и диалогах. Структура базы данных:

### Таблица Users
- id - первичный ключ
- id_chat - ID чата в Telegram
- id_user - ID пользователя в Telegram
- first_name - имя пользователя
- last_name - фамилия пользователя
- username - юзернейм в Telegram
- is_active - активен ли пользователь (1) или заблокировал бота (0)
- created_at - время регистрации пользователя

### Таблица Dialogs
- id - первичный ключ
- id_chat - ID чата в Telegram
- id_user - ID пользователя в Telegram
- number_dialog - порядковый номер диалога для пользователя
- last_message - флаг последнего сообщения в диалоге (1) или нет (0)
- model - название модели для отображения
- model_id - полный ID модели для API запросов
- user_ask - запрос пользователя
- model_answered - ответ модели
- timestamp - время создания записи

## Лицензия

MIT
```