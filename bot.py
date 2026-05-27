import asyncio
import os
import sqlite3
import json
import random
import logging
import glob
from datetime import datetime, timedelta
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.functions.contacts import GetContactsRequest
from telethon.tl.functions.auth import ResetAuthorizationsRequest, LogOutRequest
from telethon.tl.types import DocumentAttributeFilename
from telethon.tl.custom import Button
from aiohttp import web

# ==========================================
# 🔧 НАСТРОЙКИ И ЛОГИРОВАНИЕ
# ==========================================
BOT_TOKEN = os.getenv('BOT_TOKEN')
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
CORRECT_PIN = "6611"
ADMIN_ID = int(os.getenv('ADMIN_ID', 0))  # ID админа для уведомлений
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB
HOURLY_LIMIT = 1000  # Лимит сообщений в час

# Логирование
logging.basicConfig(
    filename='bot.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# База данных
conn = sqlite3.connect('bot_database.db', check_same_thread=False)
cursor = conn.cursor()

# Создание таблиц
cursor.execute('''
    CREATE TABLE IF NOT EXISTS materials (
        user_id INTEGER PRIMARY KEY,
        file_path TEXT,
        caption TEXT,
        name TEXT,
        original_name TEXT,
        size INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
''')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS stats (
        user_id INTEGER,
        total_sent INTEGER DEFAULT 0,
        broadcasts INTEGER DEFAULT 0,
        last_broadcast TIMESTAMP,
        PRIMARY KEY (user_id)
    )
''')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS broadcast_progress (
        user_id INTEGER PRIMARY KEY,
        sent INTEGER,
        total INTEGER,
        status TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
''')

conn.commit()

if not all([BOT_TOKEN, API_ID, API_HASH]):
    logger.error("❌ Ошибка: Не все переменные заданы!")
    exit(1)

# Глобальные переменные
accounts = {}
current_step = {}
current_materials = {}
broadcast_stats = {}
authorized_users = {}
broadcast_cancelled = {}
broadcast_queue = {}  # Очередь рассылок
acc_stats = {}  # Статистика по аккаунтам

for d in ['sessions', 'materials', 'logs']:
    os.makedirs(d, exist_ok=True)

# ==========================================
# 🎨 СОВРЕМЕННЫЙ ДИЗАЙН
# ==========================================
def modern_embed(title, desc=None, fields=None, footer=None, color="🟦"):
    """Современное оформление сообщений"""
    emojis = {
        'success': '✅',
        'error': '❌',
        'warning': '⚠️',
        'info': 'ℹ️',
        'process': '⏳'
    }
    
    text = f"{color} **{title.upper()}**\n"
    text += "━━━━━━━━━━━━━━━━━━━━\n\n"
    if desc:
        text += f"{desc}\n\n"
    if fields:
        for f in fields:
            emoji = f.get('emoji', '•')
            text += f"{emoji} **{f['name']}**\n{f['value']}\n\n"
    if footer:
        text += f"\n━━━━━━━━━━━━━━━━━━━━\n*{footer}*"
    return text

def get_main_keyboard():
    """Главные кнопки"""
    return [
        [Button.inline("🚀 НОВАЯ РАССЫЛКА", b'broadcast')],
        [Button.inline("👥 АККАУНТЫ", b'accounts'), Button.inline("📎 ФАЙЛ", b'material')],
        [Button.inline("📊 СТАТИСТИКА", b'stats'), Button.inline("⚙️ НАСТРОЙКИ", b'settings')]
    ]

def get_accounts_keyboard():
    """Кнопки аккаунтов"""
    return [
        [Button.inline("📱 ДОБАВИТЬ ПО НОМЕРУ", b'phone')],
        [Button.inline("💾 ЗАГРУЗИТЬ SESSION", b'sess_file')],
        [Button.inline("🔐 SESSION STRING", b'sess_str')],
        [Button.inline("🔙 НАЗАД В МЕНЮ", b'main')]
    ]

def get_material_keyboard():
    """Кнопки материалов"""
    return [
        [Button.inline("📤 ЗАГРУЗИТЬ ФАЙЛ", b'upload_mat')],
        [Button.inline("🗑 ОЧИСТИТЬ", b'clear_material')],
        [Button.inline("🔙 НАЗАД", b'main')]
    ]

def get_broadcast_keyboard():
    """Кнопки рассылки"""
    return [
        [Button.inline("✅ ЗАПУСТИТЬ", b'confirm'), Button.inline("❌ ОТМЕНА", b'main')]
    ]

def get_active_broadcast_kb():
    """Кнопки активной рассылки"""
    return [[Button.inline("🛑 СРОЧНО ОСТАНОВИТЬ", b'cancel_broadcast')]]

def get_after_broadcast_kb():
    """Кнопки после рассылки"""
    return [
        [Button.inline("🔁 ПОВТОРИТЬ", b'repeat'), Button.inline("📎 СМЕНИТЬ ФАЙЛ", b'new_mat')],
        [Button.inline("👥 СМЕНИТЬ АККАУНТ", b'accounts'), Button.inline("🏠 ГЛАВНОЕ МЕНЮ", b'main')]
    ]

def get_cancel_keyboard():
    """Кнопка отмены"""
    return [[Button.inline("❌ ОТМЕНИТЬ", b'cancel')]]

# ==========================================
# 💾 БАЗА ДАННЫХ
# ==========================================
def save_material_to_db(uid, mat):
    """Сохранение материала в БД"""
    cursor.execute('''
        INSERT OR REPLACE INTO materials (user_id, file_path, caption, name, original_name, size)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (uid, mat['file'], mat['caption'], mat['name'], mat['original_name'], mat.get('size', 0)))
    conn.commit()
    logger.info(f"[DB] Material saved for user {uid}: {mat['name']}")

def load_material_from_db(uid):
    """Загрузка материала из БД"""
    cursor.execute('SELECT * FROM materials WHERE user_id = ?', (uid,))
    row = cursor.fetchone()
    if row:
        return {
            'file': row[1],
            'caption': row[2],
            'name': row[3],
            'original_name': row[4],
            'size': row[5]
        }
    return None

def save_progress(uid, sent, total, status='running'):
    """Автосохранение прогресса"""
    cursor.execute('''
        INSERT OR REPLACE INTO broadcast_progress (user_id, sent, total, status)
        VALUES (?, ?, ?, ?)
    ''', (uid, sent, total, status))
    conn.commit()

def load_progress(uid):
    """Загрузка прогресса"""
    cursor.execute('SELECT * FROM broadcast_progress WHERE user_id = ?', (uid,))
    return cursor.fetchone()

def update_user_stats(uid, count):
    """Обновление статистики пользователя"""
    cursor.execute('''
        INSERT INTO stats (user_id, total_sent, broadcasts, last_broadcast)
        VALUES (?, ?, 1, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET
            total_sent = total_sent + ?,
            broadcasts = broadcasts + 1,
            last_broadcast = CURRENT_TIMESTAMP
    ''', (uid, count, count))
    conn.commit()

# ==========================================
# 🔧 ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================
def check_file_integrity(mat):
    """Проверка целостности файла"""
    if not mat.get('file'):
        return False, "Файл не указан"
    if not os.path.exists(mat['file']):
        return False, "Файл не найден на диске"
    if os.path.getsize(mat['file']) > MAX_FILE_SIZE:
        return False, "Файл превышает 2GB"
    return True, "OK"

def cleanup_old_files(uid):
    """Очистка старых файлов"""
    try:
        for f in glob.glob(f"sessions/acc_{uid}*"):
            os.remove(f)
            logger.info(f"[CLEANUP] Removed session: {f}")
        for f in glob.glob(f"materials/{uid}_*"):
            os.remove(f)
            logger.info(f"[CLEANUP] Removed material: {f}")
    except Exception as e:
        logger.error(f"[CLEANUP] Error: {e}")

def get_random_delay():
    """Случайная задержка для анти-спама"""
    return random.uniform(2.0, 5.0)

async def notify_admin(message):
    """Уведомление админа"""
    if ADMIN_ID:
        try:
            await bot.send_message(ADMIN_ID, f"⚠️ **УВЕДОМЛЕНИЕ**\n\n{message}")
        except:
            pass

# ==========================================
# 🎨 ОФОРМЛЕНИЕ СООБЩЕНИЙ
# ==========================================
def format_account_info(acc):
    """Красивое оформление аккаунта"""
    return modern_embed(
        "✅ АККАУНТ ПОДКЛЮЧЁН",
        "Инициализация завершена успешно",
        [
            {'name': '👤 Профиль', 'value': f"@{acc.get('username', 'нет')}", 'emoji': '👤'},
            {'name': '📞 Номер', 'value': f"+{acc.get('phone', 'нет')}", 'emoji': '📞'},
            {'name': '💬 Контакты', 'value': f"Всего: {acc.get('total', 0)}\nВзаимных: {acc.get('mutual', 0)}", 'emoji': '💬'},
            {'name': '⚡ Статус', 'value': '🟢 Активен и готов', 'emoji': '⚡'}
        ],
        "Инициализация потока доставки..."
    )

def format_broadcast_progress(sent, total, failed, acc_name):
    """Прогресс рассылки"""
    pct = int((sent/total)*100) if total else 0
    bar = "█"*(pct//10) + "░"*(10-pct//10)
    
    return modern_embed(
        "🚀 РАССЫЛКА АКТИВНА",
        "Отправка сообщений контактам",
        [
            {'name': '📊 Прогресс', 'value': f"{bar} {pct}%", 'emoji': '📊'},
            {'name': '✅ Отправлено', 'value': f"{sent}/{total}", 'emoji': '✅'},
            {'name': '❌ Ошибок', 'value': str(failed), 'emoji': '❌'},
            {'name': '👤 Аккаунт', 'value': acc_name, 'emoji': '👤'},
            {'name': '⏱ Задержка', 'value': f"{get_random_delay():.1f} сек", 'emoji': '⏱'}
        ],
        "Нажмите СТОП для экстренной остановки"
    )

def format_broadcast_result(sent, total, failed, stats):
    """Результат рассылки"""
    success_rate = int((sent/total)*100) if total else 0
    
    return modern_embed(
        "✅ РАССЫЛКА ЗАВЕРШЕНА",
        f"Успешность: {success_rate}%",
        [
            {'name': '✅ Успешно', 'value': str(sent), 'emoji': '✅'},
            {'name': '❌ Ошибок', 'value': str(failed), 'emoji': '❌'},
            {'name': '📊 Всего', 'value': str(stats.get('total', 0)), 'emoji': '📊'},
            {'name': '📅 Сегодня', 'value': str(stats.get('today', 0)), 'emoji': '📅'},
            {'name': '🔄 Всего рассылок', 'value': str(stats.get('broadcasts', 0)), 'emoji': '🔄'}
        ],
        "Завершение сессий..."
    )

def format_logout_report(success, failed):
    """Отчёт о выходе из сессий"""
    fields = []
    
    if success:
        fields.append({'name': '✅ Успешно закрыто', 'value': '\n'.join(success[:5]), 'emoji': '✅'})
        if len(success) > 5:
            fields.append({'name': '...', 'value': f'и ещё {len(success) - 5}', 'emoji': '•'})
    
    if failed:
        fields.append({'name': '❌ Ошибки', 'value': '\n'.join(failed[:5]), 'emoji': '❌'})
        if len(failed) > 5:
            fields.append({'name': '...', 'value': f'и ещё {len(failed) - 5}', 'emoji': '•'})
    
    status = "✅ Все сессии закрыты" if not failed else f"⚠️ {len(failed)} ошибок"
    color = "🟢" if not failed else "🟡"
    
    return modern_embed(
        f"{color} ЗАВЕРШЕНИЕ СЕССИЙ",
        f"Результат: {status}",
        fields,
        "Все устройства вылогинены"
    )

# ==========================================
# 🔧 ФУНКЦИИ
# ==========================================
def get_stats(uid, period):
    s = broadcast_stats.get(uid, {})
    d = datetime.now().date()
    if period == 'day': return s.get('daily', {}).get(str(d), 0)
    if period == 'week': return sum(s.get('daily', {}).get(str(d-timedelta(days=i)), 0) for i in range(7))
    if period == 'month': return sum(s.get('daily', {}).get(str(d-timedelta(days=i)), 0) for i in range(30))
    return s.get('total', 0)

def update_stats(uid, count):
    broadcast_stats.setdefault(uid, {'total': 0, 'daily': {}, 'broadcasts': 0})
    today = str(datetime.now().date())
    broadcast_stats[uid]['total'] += count
    broadcast_stats[uid]['broadcasts'] += 1
    broadcast_stats[uid]['daily'][today] = broadcast_stats[uid]['daily'].get(today, 0) + count
    update_user_stats(uid, count)

def clear_user(uid):
    """Полная очистка пользователя"""
    if uid in accounts:
        for acc in list(accounts[uid].values()):
            try:
                if acc.get('client'): 
                    asyncio.create_task(acc['client'].disconnect())
            except: pass
    accounts[uid] = {}
    current_step[uid] = 'menu'
    current_materials.pop(uid, None)
    broadcast_cancelled.pop(uid, None)
    broadcast_queue.pop(uid, None)
    logger.info(f"[CLEAR] User {uid} data cleared")

def main_menu_text(uid):
    """Текст главного меню"""
    acc_count = len(accounts.get(uid, {}))
    mat_count = len(current_materials.get(uid, {}))
    
    return modern_embed(
        "🦆 DUCK SPAM BOT",
        "Панель управления",
        [
            {'name': '📊 Статус', 'value': '🟢 Бот активен', 'emoji': '📊'},
            {'name': '👥 Аккаунтов', 'value': str(acc_count), 'emoji': '👥'},
            {'name': '📎 Материалов', 'value': str(mat_count), 'emoji': '📎'}
        ],
        "Выберите действие"
    )

# ==========================================
# 🌐 ВЕБ-СЕРВЕР
# ==========================================
async def start_web():
    app = web.Application()
    app.router.add_get('/', lambda r: web.Response(text="🦆 DUCK BOT ONLINE"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.environ.get('PORT', 8080)))
    await site.start()
    logger.info("🌐 Web server started")

# ==========================================
# 🏁 ГЛАВНЫЙ БОТ
# ==========================================
async def main():
    global bot
    bot = TelegramClient('bot_session', API_ID, API_HASH)
    await bot.start(bot_token=BOT_TOKEN)
    bot_id = (await bot.get_me()).id
    logger.info(f"✅ Bot started: {bot_id}")

    # КОМАНДА /START
    @bot.on(events.NewMessage(pattern=r'/start'))
    async def start(e):
        uid = e.sender_id
        clear_user(uid)
        authorized_users[uid] = False
        current_step[uid] = 'pin'
        await e.respond(
            modern_embed(
                "🔐 АВТОРИЗАЦИЯ",
                "Введите PIN-код для доступа",
                [
                    {'name': '📝 PIN-код', 'value': '4 цифры', 'emoji': '📝'},
                    {'name': '🔑 По умолчанию', 'value': '6611', 'emoji': '🔑'}
                ],
                "Без авторизации доступ запрещён"
            ),
            buttons=None
        )

    # ОБРАБОТКА СООБЩЕНИЙ
    @bot.on(events.NewMessage)
    async def handler(e):
        uid = e.sender_id
        txt = e.text
        step = current_step.get(uid, 'menu')
        
        if e.sender_id == bot_id or (txt and txt.startswith('/')): 
            return

        # ПРОВЕРКА PIN
        if step == 'pin':
            if txt and txt.isdigit() and len(txt) == 4:
                if txt == CORRECT_PIN:
                    authorized_users[uid] = True
                    current_step[uid] = 'menu'
                    logger.info(f"[AUTH] User {uid} authorized")
                    await e.respond(
                        modern_embed(
                            "✅ ДОСТУП РАЗРЕШЁН",
                            "Добро пожаловать в систему!",
                            [
                                {'name': '🦆 Бот', 'value': 'DUCK SPAM BOT v3.0', 'emoji': '🦆'},
                                {'name': '📊 Возможности', 'value': '• Массовая рассылка\n• Управление аккаунтами\n• Загрузка файлов\n• Статистика\n• Автосохранение', 'emoji': '📊'}
                            ],
                            "Выберите действие в меню"
                        ),
                        buttons=get_main_keyboard()
                    )
                else:
                    await e.respond("❌ **Неверный PIN-код**\n\nПопробуйте снова.", buttons=None)
            else:
                await e.respond("⚠️ **PIN-код = 4 цифры**\n\nВведите корректный код.", buttons=None)
            return

        if not authorized_users.get(uid):
            await e.respond("🔐 **Требуется авторизация**\n\nОтправьте /start", buttons=None)
            return

        # ЗАГРУЗКА SESSION
        if step == 'sess_file':
            if e.file and e.file.name.endswith('.session'):
                msg = await e.respond("⏳ **Загрузка сессии...**\n*Пожалуйста, подождите*", buttons=None)
                try:
                    # Отключаем старые аккаунты, но сохраняем материалы
                    if uid in accounts:
                        for acc in list(accounts[uid].values()):
                            try:
                                if acc.get('client'): 
                                    await acc['client'].disconnect()
                            except: pass
                        accounts[uid] = {}
                    
                    path = await e.download_media(file=f"sessions/acc_{uid}.session")
                    session_name = path.replace('.session', '')
                    client = TelegramClient(session_name, API_ID, API_HASH)
                    await client.connect()
                    
                    if not await client.is_user_authorized():
                        await msg.edit("❌ **Сессия недействительна!**\n\nПопробуйте другую.", buttons=None)
                        await client.disconnect()
                        return
                    
                    me = await client.get_me()
                    try:
                        contacts = await client(GetContactsRequest(0))
                        total = len([u for u in contacts.users if not u.bot])
                        mutual = len([u for u in contacts.users if u.mutual_contact and not u.bot])
                    except: 
                        total, mutual = 0, 0
                    
                    accounts.setdefault(uid, {})['active'] = {
                        'client': client, 
                        'phone': me.phone if me.phone else 'скрыт', 
                        'name': me.first_name or 'No name',
                        'username': me.username or 'нет', 
                        'total': total, 
                        'mutual': mutual
                    }
                    current_step[uid] = 'menu'
                    
                    await msg.edit(format_account_info(accounts[uid]['active']), buttons=get_main_keyboard())
                    logger.info(f"[SESSION] User {uid} loaded session: {me.first_name}")
                    
                except Exception as err:
                    await msg.edit(f"❌ **Ошибка:** {str(err)[:200]}", buttons=None)
                    logger.error(f"[SESSION] Error for user {uid}: {err}")
            return

        # ВХОД ПО НОМЕРУ
        if step == 'phone':
            if txt and txt.startswith('+') and txt[1:].replace(' ', '').isdigit():
                msg = await e.respond("🔄 **Подключение...**", buttons=None)
                try:
                    if uid in accounts:
                        for acc in list(accounts[uid].values()):
                            try:
                                if acc.get('client'): 
                                    await acc['client'].disconnect()
                            except: pass
                        accounts[uid] = {}

                    client = TelegramClient(f'acc_{uid}', API_ID, API_HASH)
                    await client.connect()
                    await client.send_code_request(txt)
                    accounts.setdefault(uid, {})['temp_client'] = client
                    accounts[uid]['temp_phone'] = txt
                    current_step[uid] = 'wait_code'
                    await msg.edit(f"✅ **Код отправлен на {txt}**\n\nВведите код из Telegram:", buttons=get_cancel_keyboard())
                except Exception as err:
                    await msg.edit(f"❌ **Ошибка:** {str(err)[:200]}", buttons=None)
            return

        # ВВОД КОДА
        if step == 'wait_code':
            if txt and txt.isdigit() and 4 <= len(txt) <= 6:
                temp_client = accounts.get(uid, {}).get('temp_client')
                temp_phone = accounts.get(uid, {}).get('temp_phone')
                
                if not temp_client or not temp_phone:
                    await e.respond("❌ **Сессия истекла**\n\nНачните сначала.", buttons=None)
                    return
                
                try:
                    await temp_client.sign_in(temp_phone, txt)
                    if not await temp_client.is_user_authorized():
                        await e.respond("❌ **Неверный код**", buttons=None)
                        return
                    
                    me = await temp_client.get_me()
                    try:
                        contacts = await temp_client(GetContactsRequest(0))
                        total = len([u for u in contacts.users if not u.bot])
                        mutual = len([u for u in contacts.users if u.mutual_contact and not u.bot])
                    except:
                        total, mutual = 0, 0
                    
                    accounts.setdefault(uid, {})['active'] = {
                        'client': temp_client,
                        'phone': me.phone if me.phone else 'скрыт',
                        'name': me.first_name or 'No name',
                        'username': me.username or 'нет',
                        'total': total,
                        'mutual': mutual
                    }
                    
                    accounts[uid].pop('temp_client', None)
                    accounts[uid].pop('temp_phone', None)
                    current_step[uid] = 'menu'
                    
                    await e.respond(format_account_info(accounts[uid]['active']), buttons=get_main_keyboard())
                    logger.info(f"[PHONE] User {uid} logged in: {me.first_name}")
                    
                except Exception as err:
                    await e.respond(f"❌ **Ошибка:** {str(err)[:200]}", buttons=None)
            return

        # ЗАГРУЗКА МАТЕРИАЛА
        if step == 'upload_mat':
            if e.file or txt:
                try:
                    # Проверка размера
                    if e.file and e.file.size > MAX_FILE_SIZE:
                        return await e.respond("❌ **Файл слишком большой!**\n\nМаксимум: 2GB", buttons=None)
                    
                    old_mat = current_materials.get(uid)
                    if old_mat:
                        await e.respond(f"🗑️ **Старый файл удалён:**\n`{old_mat.get('name', 'Unknown')}`", buttons=None)
                        await asyncio.sleep(0.5)
                    
                    current_materials.pop(uid, None)
                    
                    if e.file:
                        original_name = e.file.name or 'file'
                        msg_wait = await e.respond(f"📥 **Загрузка:** `{original_name}`\n*Подождите...*", buttons=None)
                        
                        path = await e.download_media(file=f"materials/{uid}_{original_name}")
                        cap = txt or ''
                        
                        mat = {
                            'file': path, 
                            'caption': cap, 
                            'name': original_name,
                            'original_name': original_name,
                            'size': e.file.size
                        }
                    else:
                        mat = {
                            'file': None, 
                            'caption': txt, 
                            'name': 'Text', 
                            'original_name': 'Text',
                            'size': len(txt) if txt else 0
                        }
                    
                    current_materials[uid] = mat
                    current_step[uid] = 'menu'
                    
                    # Сохранение в БД
                    save_material_to_db(uid, mat)
                    
                    # Проверка целостности
                    is_valid, msg = check_file_integrity(mat)
                    if not is_valid:
                        await e.respond(f"❌ **Ошибка файла:** {msg}", buttons=None)
                        return
                    
                    await e.respond(
                        modern_embed(
                            "✅ ФАЙЛ ГОТОВ",
                            "Материал загружен и проверен",
                            [
                                {'name': '📁 Имя', 'value': f"`{mat['name']}`", 'emoji': '📁'},
                                {'name': '📝 Текст', 'value': mat['caption'][:100] or 'нет', 'emoji': '📝'},
                                {'name': '📊 Размер', 'value': f"{mat.get('size', 0)} байт", 'emoji': '📊'},
                                {'name': '✅ Статус', 'value': 'Готов к рассылке', 'emoji': '✅'}
                            ],
                            "Можно запускать рассылку"
                        ),
                        buttons=get_material_keyboard()
                    )
                    logger.info(f"[MATERIAL] User {uid} uploaded: {mat['name']}")
                    
                except Exception as err:
                    logger.error(f"[MATERIAL] Error for user {uid}: {err}")
                    await e.respond(f"❌ **Ошибка загрузки:**\n`{str(err)[:200]}`", buttons=None)
            return

        # КОМАНДА /STOP
        if txt == '/stop':
            if uid in broadcast_queue and broadcast_queue[uid]:
                broadcast_cancelled[uid] = True
                await e.respond("🛑 **Остановка рассылки...**\n*Подождите завершения*", buttons=None)
                logger.info(f"[STOP] User {uid} stopped broadcast")
            else:
                await e.respond("ℹ️ **Нет активной рассылки**", buttons=None)
            return

    # ОБРАБОТКА КНОПОК
    @bot.on(events.CallbackQuery)
    async def cb(e):
        uid = e.sender_id
        if not authorized_users.get(uid):
            return await e.answer("🔐 Сначала /start", alert=True)
        
        d = e.data.decode()

        if d == 'main':
            current_step[uid] = 'menu'
            await e.edit(main_menu_text(uid), buttons=get_main_keyboard())
        
        elif d == 'accounts':
            current_step[uid] = 'menu'
            accs = accounts.get(uid, {})
            
            fields = []
            if accs:
                for i, a in enumerate(accs.values(), 1):
                    val = f"👤 {a.get('name', 'Unknown')}\n📞 {a.get('phone', 'Unknown')}\n@{a.get('username', 'нет')}\n🤝 {a.get('mutual', 0)} вз."
                    fields.append({'name': f'👤 Аккаунт {i}', 'value': val, 'emoji': '👤'})
            else:
                fields = [{'name': '⚠️', 'value': 'Нет аккаунтов\nДобавьте через меню', 'emoji': '⚠️'}]
            
            await e.edit(modern_embed("👥 АККАУНТЫ", f"Всего: {len(accs)}", fields, "Управление аккаунтами"), buttons=get_accounts_keyboard())
        
        elif d == 'material':
            current_step[uid] = 'menu'
            cur = current_materials.get(uid)
            
            if cur:
                fields = [
                    {'name': '📎 Активный файл', 'value': f"`{cur.get('name', 'Unknown')}`", 'emoji': '📎'},
                    {'name': '📝 Текст', 'value': cur.get('caption', 'нет')[:100], 'emoji': '📝'},
                    {'name': '📊 Размер', 'value': f"{cur.get('size', 0)} байт", 'emoji': '📊'}
                ]
            else:
                fields = [{'name': '⚠️', 'value': 'Файл не загружен\nНажмите ЗАГРУЗИТЬ ФАЙЛ', 'emoji': '⚠️'}]
            
            await e.edit(modern_embed("📎 МАТЕРИАЛЫ", "Управление файлами", fields, "Выберите действие"), buttons=get_material_keyboard())
        
        elif d == 'stats':
            current_step[uid] = 'menu'
            t = broadcast_stats.get(uid, {}).get('total', 0)
            await e.edit(modern_embed("📊 СТАТИСТИКА", "Общая информация", [
                {'name': '📊 Всего отправлено', 'value': str(t), 'emoji': '📊'},
                {'name': '📅 Сегодня', 'value': str(get_stats(uid,'day')), 'emoji': '📅'},
                {'name': '📅 За неделю', 'value': str(get_stats(uid,'week')), 'emoji': '📅'},
                {'name': '📅 За месяц', 'value': str(get_stats(uid,'month')), 'emoji': '📅'},
                {'name': '🔄 Всего рассылок', 'value': str(broadcast_stats.get(uid, {}).get('broadcasts', 0)), 'emoji': '🔄'}
            ], "Статистика рассылок"), buttons=get_main_keyboard())
        
        elif d == 'phone':
            current_step[uid] = 'phone'
            await e.edit("**📱 ВХОД ПО НОМЕРУ**\n\nВведите номер:\n`+79991234567`", buttons=get_cancel_keyboard())
        
        elif d == 'sess_file':
            current_step[uid] = 'sess_file'
            await e.edit("**💾 ЗАГРУЗКА SESSION**\n\nОтправьте файл .session", buttons=get_cancel_keyboard())
        
        elif d == 'sess_str':
            current_step[uid] = 'sess_str'
            await e.edit("**🔐 SESSION STRING**\n\nВведите строку", buttons=get_cancel_keyboard())
        
        elif d == 'upload_mat':
            current_step[uid] = 'upload_mat'
            await e.edit("**📤 ЗАГРУЗКА ФАЙЛА**\n\nОтправьте:\n• APK\n• Фото\n• Видео\n• Документ\n\nИли текст", buttons=get_cancel_keyboard())
        
        elif d == 'clear_material':
            current_materials.pop(uid, None)
            await e.answer("🗑️ Материал очищен", alert=True)
            await e.edit(main_menu_text(uid), buttons=get_main_keyboard())
        
        elif d == 'broadcast':
            current_step[uid] = 'menu'
            accs = accounts.get(uid, {})
            
            if not accs: 
                return await e.answer("❌ Добавьте аккаунты!", alert=True)
            if uid not in current_materials: 
                return await e.answer("❌ Загрузите файл!", alert=True)
            
            # Проверка очереди
            if uid in broadcast_queue and broadcast_queue[uid]:
                return await e.answer("⏳ Рассылка уже идёт!", alert=True)
            
            total = sum(a.get('mutual',0) for a in accs.values())
            fields = [{'name': f"👤 {a['name']} (@{a.get('username', 'нет')})", 'value': f"📞 {a.get('mutual',0)} вз.", 'emoji': '👤'} for a in accs.values()]
            fields += [
                {'name': '📎 Файл', 'value': current_materials[uid]['name'], 'emoji': '📎'}, 
                {'name': '📊 Всего контактов', 'value': str(total), 'emoji': '📊'}
            ]
            
            await e.edit(modern_embed("🚀 РАССЫЛКА", "Параметры запуска", fields, "Подтвердите запуск"), buttons=get_broadcast_keyboard())
        
        elif d == 'confirm':
            broadcast_cancelled[uid] = False
            broadcast_queue[uid] = True
            await e.edit("⏳ **ЗАПУСК...**\n*Инициализация*", buttons=get_active_broadcast_kb())
            asyncio.create_task(do_broadcast(bot, uid, e))
        
        elif d == 'repeat':
            if uid in current_materials:
                broadcast_cancelled[uid] = False
                broadcast_queue[uid] = True
                await e.edit("🔁 **ПОВТОР...**", buttons=get_active_broadcast_kb())
                asyncio.create_task(do_broadcast(bot, uid, e))
            else:
                await e.answer("❌ Нет файла", alert=True)
        
        elif d == 'new_mat':
            current_step[uid] = 'upload_mat'
            await e.edit("**📤 НОВЫЙ ФАЙЛ**\n\nОтправьте файл", buttons=get_cancel_keyboard())
        
        elif d == 'cancel':
            current_step[uid] = 'menu'
            await e.edit(main_menu_text(uid), buttons=get_main_keyboard())
        
        elif d == 'cancel_broadcast':
            broadcast_cancelled[uid] = True
            await e.answer("🛑 ОСТАНОВКА...", alert=True)
            logger.info(f"[CANCEL] User {uid} cancelled broadcast")
        
        await e.answer()

    # РАССЫЛКА
    async def do_broadcast(bot, uid, e):
        try:
            accs = accounts.get(uid, {})
            mat = current_materials.get(uid)
            
            logger.info(f"[BROADCAST] Starting for user {uid}")
            logger.info(f"[BROADCAST] Accounts: {list(accs.keys())}")
            logger.info(f"[BROADCAST] Material: {mat}")
            
            if not accs:
                return await e.respond("❌ **Нет аккаунтов**", buttons=None)
            
            if not mat:
                return await e.respond("❌ **Нет материала**", buttons=None)
            
            # Проверка файла
            is_valid, msg = check_file_integrity(mat)
            if not is_valid:
                return await e.respond(f"❌ **Ошибка файла:** {msg}", buttons=None)
            
            sent, failed = 0, 0
            targets = []
            hourly_count = 0
            
            for acc_id, acc in accs.items():
                try:
                    # Проверка авторизации
                    if not await acc['client'].is_user_authorized():
                        await e.respond(f"❌ **Аккаунт {acc['name']} отключился!**", buttons=None)
                        continue
                    
                    c = await acc['client'](GetContactsRequest(0))
                    targets.append((acc, [u for u in c.users if u.mutual_contact and not u.bot]))
                except Exception as err:
                    logger.error(f"[BROADCAST] Error getting contacts: {err}")
                    await e.respond(f"❌ **Ошибка {acc_id}:** {str(err)[:100]}", buttons=None)
            
            if not targets:
                await e.respond("⚠️ **Нет контактов**", buttons=None)
                broadcast_queue[uid] = False
                return
            
            total = sum(len(t) for _,t in targets)
            status = await e.respond(format_broadcast_progress(0, total, 0, "Старт"), buttons=get_active_broadcast_kb())
            
            current = 0
            cancelled = False
            
            for acc, users in targets:
                if broadcast_cancelled.get(uid): 
                    cancelled = True
                    break
                
                if hourly_count >= HOURLY_LIMIT:
                    await e.respond(f"⚠️ **Достигнут часовой лимит ({HOURLY_LIMIT})!**\n\nРассылка остановлена.", buttons=None)
                    await notify_admin(f"User {uid} hit hourly limit")
                    break
                
                for user in users:
                    if broadcast_cancelled.get(uid):
                        cancelled = True
                        break
                    
                    try:
                        if mat['file']:
                            await acc['client'].send_file(
                                user.id, 
                                mat['file'], 
                                caption=mat['caption'],
                                attributes=[DocumentAttributeFilename(file_name=mat.get('original_name', 'file'))]
                            )
                        else:
                            await acc['client'].send_message(user.id, mat['caption'])
                        sent += 1
                        hourly_count += 1
                    except Exception as send_err:
                        logger.error(f"[SEND] Error: {send_err}")
                        failed += 1
                    
                    current += 1
                    if current % 10 == 0:
                        await status.edit(format_broadcast_progress(current, total, failed, acc['name']), buttons=get_active_broadcast_kb())
                        save_progress(uid, current, total, 'running')
                    
                    # Случайная задержка
                    await asyncio.sleep(get_random_delay())
            
            # Завершение сессий
            await status.edit("**🔐 ЗАВЕРШЕНИЕ СЕССИЙ...**\n*Выход из всех устройств*", buttons=None)
            
            success_logout = []
            failed_logout = []
            
            for acc, _ in targets:
                acc_name = acc.get('name', 'Unknown')
                acc_username = acc.get('username', 'нет')
                acc_phone = acc.get('phone', '???')
                
                if acc_username and acc_username != 'нет':
                    acc_str = f"{acc_name} (@{acc_username})"
                else:
                    acc_str = f"{acc_name} ({acc_phone})"
                
                client = acc['client']
                
                try:
                    await client(ResetAuthorizationsRequest())
                    await client(LogOutRequest())
                    success_logout.append(acc_str)
                    logger.info(f"[LOGOUT] Success: {acc_str}")
                except Exception as logout_err:
                    failed_logout.append(f"{acc_str} ({str(logout_err)[:50]})")
                    logger.error(f"[LOGOUT] Failed: {acc_str} - {logout_err}")
            
            accounts[uid] = {}
            
            if not cancelled: 
                update_stats(uid, sent)
            stats = {
                'total': broadcast_stats[uid].get('total', 0), 
                'today': get_stats(uid, 'day'),
                'broadcasts': broadcast_stats[uid].get('broadcasts', 0)
            }
            
            # Очистка прогресса
            save_progress(uid, 0, 0, 'completed')
            
            if cancelled:
                await status.edit(
                    modern_embed("🛑 РАССЫЛКА ОТМЕНЕНА", f"Остановлено пользователем", [
                        {'name': '✅ Отправлено', 'value': f"{sent}/{total}", 'emoji': '✅'},
                        {'name': '⏹️ Остановлено', 'value': f"На {sent} сообщении", 'emoji': '⏹️'}
                    ], "Завершение сессий..."),
                    buttons=get_after_broadcast_kb()
                )
            else:
                await status.edit(format_broadcast_result(sent, total, failed, stats), buttons=get_after_broadcast_kb())
            
            if success_logout or failed_logout:
                await e.respond(format_logout_report(success_logout, failed_logout), buttons=None)
            
            broadcast_queue[uid] = False
            current_step[uid] = 'after'
            logger.info(f"[BROADCAST] Completed for user {uid}: {sent} sent, {failed} failed")
            
        except Exception as e:
            logger.error(f"[BROADCAST] Critical error: {e}")
            await notify_admin(f"Critical error for user {uid}:\n{e}")
            broadcast_queue[uid] = False

    await bot.run_until_disconnected()

async def run():
    await asyncio.gather(start_web(), main())

if __name__ == '__main__':
    asyncio.run(run())
