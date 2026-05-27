import asyncio
import os
import sqlite3
import random
import logging
from datetime import datetime, timedelta
from telethon import TelegramClient, events
from telethon.tl.functions.contacts import GetContactsRequest
from telethon.tl.functions.auth import ResetAuthorizationsRequest, LogOutRequest
from telethon.tl.types import DocumentAttributeFilename, MessageMediaPhoto
from telethon.tl.custom import Button
from aiohttp import web

# ==========================================
# 🔧 НАСТРОЙКИ
# ==========================================
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv('BOT_TOKEN')
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')

# 🔗 АДМИН
ADMIN_USERNAME = "lapa00001"
ADMIN_LINK = f"https://t.me/{ADMIN_USERNAME}"
ADMIN_ID = 7254231560

SUBSCRIPTION_DAYS = 7

if not all([BOT_TOKEN, API_ID, API_HASH]):
    logger.error("❌ НЕ ВСЕ ПЕРЕМЕННЫЕ ЗАДАНЫ!")
    exit(1)

VIP_USERS = [440077089, 789299303, ADMIN_ID]

# ==========================================
# 💾 БАЗА ДАННЫХ
# ==========================================
conn = sqlite3.connect('bot.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, subscription_end TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS materials (user_id INTEGER PRIMARY KEY, file_path TEXT, caption TEXT, name TEXT, photo_file_id TEXT)''')
conn.commit()

accounts = {}
current_step = {}
current_materials = {}
broadcast_stats = {}
broadcast_cancelled = {}
broadcast_queue = {}
admin_step = {}

for d in ['sessions', 'materials']:
    os.makedirs(d, exist_ok=True)

# ==========================================
# 🛠 ФУНКЦИИ
# ==========================================
def get_user(uid):
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (uid,))
    return cursor.fetchone()

def add_user(uid, username=None):
    cursor.execute('INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)', (uid, username))
    conn.commit()

def set_subscription(uid, days=7):
    end_date = datetime.now() + timedelta(days=days)
    cursor.execute('UPDATE users SET subscription_end = ? WHERE user_id = ?', (end_date.isoformat(), uid))
    conn.commit()
    logger.info(f"✅ Subscription set for user {uid} until {end_date}")

def revoke_subscription(uid):
    cursor.execute('UPDATE users SET subscription_end = NULL WHERE user_id = ?', (uid,))
    conn.commit()
    logger.info(f"❌ Subscription revoked for user {uid}")

def check_subscription(uid):
    if uid in VIP_USERS: 
        return "VIP", -1
    user = get_user(uid)
    if not user or not user[2]: 
        return False, None
    try:
        end_date = datetime.fromisoformat(user[2])
        if datetime.now() < end_date:
            days_left = (end_date - datetime.now()).days
            return days_left, days_left
        return False, 0
    except: 
        return False, None

def save_material(uid, mat):
    cursor.execute('INSERT OR REPLACE INTO materials (user_id, file_path, caption, name, photo_file_id) VALUES (?,?,?,?,?)', 
                   (uid, mat.get('file'), mat.get('caption'), mat.get('name'), mat.get('photo_file_id')))
    conn.commit()

def update_stats(uid, count):
    broadcast_stats.setdefault(uid, {'total': 0, 'today': 0})
    broadcast_stats[uid]['total'] += count
    broadcast_stats[uid]['today'] += count

def get_all_users():
    cursor.execute('SELECT DISTINCT user_id FROM users')
    users = [row[0] for row in cursor.fetchall()]
    logger.info(f"📊 Found {len(users)} total users in database")
    return users

def get_user_by_username(username):
    """Ищем пользователя в БД по username"""
    username = username.strip().lstrip('@')
    cursor.execute('SELECT user_id, username FROM users WHERE username LIKE ? OR username LIKE ?', 
                   (f'%{username}%', f'@{username}%'))
    result = cursor.fetchone()
    if result:
        return result[0], result[1]
    return None, None

# ==========================================
# 🎨 UI КНОПКИ
# ==========================================
def main_kb(has_sub, is_vip=False):
    if not has_sub and not is_vip:
        return [
            [Button.url("✍️ Написать админу", ADMIN_LINK)],
            [Button.inline("👤 Мой профиль", b'profile')],
            [Button.inline("🚀 Запуск рассылки", b'broadcast_disabled')],
            [Button.inline("📎 Материал", b'material_disabled'), Button.inline("📊 Статистика", b'stats_disabled')]
        ]
    else:
        return [
            [Button.inline("🚀 Запуск рассылки", b'broadcast')],
            [Button.inline("📎 Материал", b'material'), Button.inline("👤 Профиль", b'profile')],
            [Button.inline("📊 Статистика", b'stats')]
        ]

def admin_kb():
    return [
        [Button.inline("👤 Выдать доступ", b'admin_grant')],
        [Button.inline("👤 Забрать доступ", b'admin_revoke')],
        [Button.inline("📢 Рассылка", b'admin_broadcast')],
        [Button.inline("🔙 Назад", b'main')]
    ]

def after_session_kb():
    return [
        [Button.inline("🟢 Запустить рассылку", b'confirm')],
        [Button.inline("🔙 Назад в меню", b'main')]
    ]

def get_after_kb():
    return [
        [Button.inline("🔁 Повторить рассылку", b'repeat')],
        [Button.inline("🏠 Главное меню", b'main')]
    ]

# ==========================================
# 🏁 БОТ
# ==========================================
async def main():
    bot = TelegramClient('bot_session', API_ID, API_HASH)
    await bot.start(bot_token=BOT_TOKEN)
    bot_id = (await bot.get_me()).id
    logger.info(f"✅ Bot started: @{bot_id}")

    @bot.on(events.NewMessage(pattern=r'/start'))
    async def start_cmd(e):
        uid = e.sender_id
        username = e.sender.username
        add_user(uid, username)
        current_step[uid] = 'menu'
        has_sub, days = check_subscription(uid)
        is_vip = uid in VIP_USERS

        logger.info(f"User {uid} (@{username}) pressed /start. Has sub: {has_sub}, Is VIP: {is_vip}")

        if is_vip:
            msg = (
                "**🦆 DUCK SPAM BOT**\n\n"
                "**👑 VIP ПОДПИСКА**\n"
                "**🌟 Вечный доступ!**\n\n"
                "**Ваши возможности:**\n"
                "• 🚀 Неограниченная рассылка\n"
                "• 📎 Загрузка любых файлов\n"
                "• 👥 Управление аккаунтами\n"
                "• 📊 Подробная статистика\n\n"
                "Выберите действие:"
            )
        elif has_sub:
            msg = (
                "**🦆 DUCK SPAM BOT**\n\n"
                "**✅ ПОДПИСКА АКТИВНА**\n"
                f"**⏰ Осталось дней:** {days}\n\n"
                "**Ваши возможности:**\n"
                "• 🚀 Массовая рассылка\n"
                "• 📎 Загрузка файлов\n"
                "• 👥 Управление аккаунтами\n"
                "• 📊 Статистика\n\n"
                "Выберите действие:"
            )
        else:
            msg = (
                "**🦆 DUCK SPAM BOT**\n\n"
                "**🔐 ДОСТУП ЗАКРЫТ**\n\n"
                "**💰 Для покупки подписки:**\n"
                f"💵 **$3** / **{SUBSCRIPTION_DAYS} дней**\n\n"
                "**Свяжитесь с администратором:**\n"
                "Нажмите кнопку ниже",
            )

        if uid == ADMIN_ID:
            await e.respond(msg, buttons=admin_kb())
        else:
            await e.respond(msg, buttons=main_kb(has_sub, is_vip))

    @bot.on(events.NewMessage(pattern=r'/admin'))
    async def admin_cmd(e):
        if e.sender_id != ADMIN_ID:
            return
        
        await e.respond(
            "**👤 АДМИН ПАНЕЛЬ**\n\n"
            "Выберите действие:",
            buttons=admin_kb()
        )

    @bot.on(events.NewMessage)
    async def handler(e):
        uid, txt, step = e.sender_id, e.text, current_step.get(e.sender_id, 'menu')
        
        if e.sender_id == (await bot.get_me()).id or (txt and txt.startswith('/')): 
            return
        
        has_sub, _ = check_subscription(uid)
        is_vip = uid in VIP_USERS

        # ОБРАБОТКА АДМИН КОМАНД
        if uid == ADMIN_ID:
            admin_s = admin_step.get(uid)
            
            # ВЫДАЧА ДОСТУПА
            if admin_s == 'grant_wait_username':
                username = txt.strip().lstrip('@')
                admin_step[uid] = 'grant_wait_days'
                admin_step[f'{uid}_username'] = username
                await e.respond(f"**👤 Введите количество дней:**\n\nДля пользователя: **@{username}**")
                return
            
            if admin_s == 'grant_wait_days':
                try:
                    days = int(txt)
                    username = admin_step.get(f'{uid}_username')
                    
                    if username:
                        # Сначала ищем в БД
                        target_uid, found_username = get_user_by_username(username)
                        
                        if not target_uid:
                            # Если не нашли в БД, пробуем через Telegram
                            try:
                                entity = await bot.get_entity(username)
                                target_uid = entity.id
                                found_username = entity.username or username
                                # Добавляем в БД
                                add_user(target_uid, found_username)
                            except Exception as entity_err:
                                await e.respond(f"❌ **Пользователь не найден!**\n\n"
                                              f"Пользователь @{username} не найден ни в базе, ни в Telegram.\n\n"
                                              f"Убедитесь что:\n"
                                              f"• Пользователь нажимал /start\n"
                                              f"• Username введен верно\n\n"
                                              f"Ошибка: {str(entity_err)[:100]}")
                                admin_step[uid] = None
                                admin_step.pop(f'{uid}_username', None)
                                return
                        
                        # Выдаем подписку
                        set_subscription(target_uid, days)
                        cursor.execute('UPDATE users SET username = ? WHERE user_id = ?', (found_username, target_uid))
                        conn.commit()
                        
                        await e.respond(f"✅ **Подписка выдана!**\n\n"
                                      f"👤 Пользователь: **@{found_username}** (`{target_uid}`)\n"
                                      f"📅 Дней: {days}")
                        
                        try:
                            await bot.send_message(target_uid, f"**✅ ВАМ ВЫДАНА ПОДПИСКА!**\n\n"
                                                  f"📅 На {days} дней\n"
                                                  f"👤 Админ: @{ADMIN_USERNAME}\n\n"
                                                  f"Отправьте /start")
                        except: pass
                    
                    admin_step[uid] = None
                    admin_step.pop(f'{uid}_username', None)
                    return
                    
                except ValueError:
                    await e.respond("❌ **Неверное число**\n\nВведите количество дней:")
                    return
            
            # ЗАБРАТЬ ДОСТУП - ИСПРАВЛЕНО
            if admin_s == 'revoke_wait_username':
                username = txt.strip().lstrip('@')
                
                # Ищем в БД
                target_uid, found_username = get_user_by_username(username)
                
                if not target_uid:
                    await e.respond(f"❌ **Пользователь не найден в базе!**\n\n"
                                  f"Пользователь @{username} не найден в базе данных.\n\n"
                                  f"Убедитесь что:\n"
                                  f"• Пользователь нажимал /start в боте\n"
                                  f"• Username введен верно (без @ или с @)\n\n"
                                  f"💡 **Совет:** Используйте ID пользователя вместо username\n"
                                  f"ID можно узнать в профиле пользователя",
                                  buttons=[[Button.inline("🔙 Отмена", b'main')]])
                    admin_step[uid] = None
                    admin_step.pop(f'{uid}_revoke_username', None)
                    return
                
                # Проверяем есть ли подписка
                has_sub_check, _ = check_subscription(target_uid)
                if not has_sub_check and target_uid not in VIP_USERS:
                    await e.respond(f"⚠️ **У пользователя нет активной подписки!**\n\n"
                                  f"👤 @{found_username} (`{target_uid}`)\n\n"
                                  f"Нечего отзывать.",
                                  buttons=[[Button.inline("🔙 Отмена", b'main')]])
                    admin_step[uid] = None
                    admin_step.pop(f'{uid}_revoke_username', None)
                    return
                
                # Запрашиваем подтверждение
                admin_step[uid] = 'revoke_confirm'
                admin_step[f'{uid}_revoke_uid'] = target_uid
                admin_step[f'{uid}_revoke_username'] = found_username
                
                await e.respond(
                    f"**⚠️ ПОДТВЕРДИТЕ ОТЗЫВ ПОДПИСКИ**\n\n"
                    f"👤 Пользователь: **@{found_username}** (`{target_uid}`)\n\n"
                    f"Напишите **да** для подтверждения\n"
                    f"или **нет** для отмены",
                    buttons=[[Button.inline("❌ Отмена", b'main')]]
                )
                return
            
            if admin_s == 'revoke_confirm':
                if txt.lower() in ['да', 'yes', 'y']:
                    target_uid = admin_step.get(f'{uid}_revoke_uid')
                    target_username = admin_step.get(f'{uid}_revoke_username')
                    
                    if target_uid:
                        revoke_subscription(target_uid)
                        
                        await e.respond(f"✅ **Подписка отозвана!**\n\n"
                                      f"👤 Пользователь: **@{target_username}** (`{target_uid}`)\n\n"
                                      f"Доступ к боту заблокирован.")
                        
                        try:
                            await bot.send_message(target_uid, f"**❌ ВАША ПОДПИСКА ОТОЗВАНА!**\n\n"
                                                  f"Доступ к боту заблокирован.\n\n"
                                                  f"👤 Админ: @{ADMIN_USERNAME}")
                        except: pass
                    
                    admin_step[uid] = None
                    admin_step.pop(f'{uid}_revoke_uid', None)
                    admin_step.pop(f'{uid}_revoke_username', None)
                    return
                else:
                    await e.respond("❌ **Отменено**")
                    admin_step[uid] = None
                    admin_step.pop(f'{uid}_revoke_uid', None)
                    admin_step.pop(f'{uid}_revoke_username', None)
                    return
            
            # РАССЫЛКА
            if admin_s == 'broadcast_wait_text':
                admin_step[uid] = 'broadcast_wait_photo'
                admin_step[f'{uid}_text'] = txt
                await e.respond("**📎 Отправьте фото (или нажмите 'Пропустить')**\n\nИли отправьте /skip_photo")
                return
            
            if admin_s == 'broadcast_wait_photo' and e.file:
                try:
                    photo_path = await e.download_media(file=f"materials/admin_broadcast_photo.jpg")
                    admin_step[f'{uid}_photo'] = photo_path
                    await e.respond("**✅ Фото сохранено!**\n\nНажмите /send_broadcast для отправки")
                    return
                except Exception as err:
                    await e.respond(f"❌ Ошибка сохранения фото: {err}")
                    return

        # 🔐 ПРОВЕРКА ПОДПИСКИ - ТЕПЕРЬ ПОСЛЕ АДМИНА
        if not has_sub and not is_vip and uid != ADMIN_ID:
            await e.respond(
                "**🔐 ДОСТУП ЗАБЛОКИРОВАН**\n\n"
                "У вас нет активной подписки.\n"
                "Для получения доступа свяжитесь с админом.",
                buttons=[[Button.url("✍️ Написать админу", ADMIN_LINK)]]
            )
            return

        # ЗАГРУЗКА СЕССИИ
        if step == 'sess_file':
            if e.file and e.file.name.endswith('.session'):
                msg = await e.respond("⏳ **Загрузка сессии...**")
                try:
                    accounts.pop(uid, None)
                    path = await e.download_media(file=f"sessions/{uid}.session")
                    client = TelegramClient(path.replace('.session',''), API_ID, API_HASH)
                    await client.connect()
                    
                    if not await client.is_user_authorized():
                        await msg.edit("❌ **Сессия недействительна!**")
                        await client.disconnect()
                        return

                    me = await client.get_me()
                    contacts = await client(GetContactsRequest(0))
                    total = len([u for u in contacts.users if not u.bot])
                    mutual = len([u for u in contacts.users if u.mutual_contact and not u.bot])

                    accounts[uid] = {'active': {
                        'client': client, 'phone': me.phone, 'name': me.first_name or 'User',
                        'username': me.username or 'нет', 'total': total, 'mutual': mutual
                    }}
                    current_step[uid] = 'menu'

                    await msg.edit(
                        "**✅ АККАУНТ ПОДКЛЮЧЁН!**\n\n"
                        f"**👤 Имя:** {me.first_name or 'Не указано'}\n"
                        f"**📱 Username:** @{me.username or 'нет'}\n"
                        f"**📞 Номер:** +{me.phone}\n"
                        f"**💬 Всего контактов:** {total}\n"
                        f"**✅ Взаимных:** {mutual}\n\n"
                        "*Инициализация потока доставки...*",
                        buttons=after_session_kb()
                    )
                except Exception as err:
                    await msg.edit(f"❌ **Ошибка:** {str(err)[:200]}")
            return

        if step == 'upload_mat':
            if e.file or txt:
                current_materials.pop(uid, None)
                if e.file:
                    path = await e.download_media(file=f"materials/{uid}_{e.file.name}")
                    mat = {'file': path, 'caption': txt or '', 'name': e.file.name}
                else:
                    mat = {'file': None, 'caption': txt, 'name': 'Text'}
                current_materials[uid] = mat
                current_step[uid] = 'menu'
                save_material(uid, mat)
                await e.respond(f"**✅ Файл загружен!**\n\n📁 {mat['name']}\n📝 {mat['caption'][:50] or 'нет'}", 
                              buttons=main_kb(has_sub, is_vip))
            return

    @bot.on(events.NewMessage(pattern=r'/skip_photo'))
    async def skip_photo(e):
        if e.sender_id != ADMIN_ID:
            return
        
        admin_s = admin_step.get(e.sender_id)
        if admin_s == 'broadcast_wait_photo':
            admin_step.pop(f'{e.sender_id}_photo', None)
            await do_admin_broadcast(e, e.sender_id)

    @bot.on(events.NewMessage(pattern=r'/send_broadcast'))
    async def send_broadcast_cmd(e):
        if e.sender_id != ADMIN_ID:
            return
        
        admin_s = admin_step.get(e.sender_id)
        if admin_s == 'broadcast_wait_photo':
            await do_admin_broadcast(e, e.sender_id)

    async def do_admin_broadcast(e, uid):
        text = admin_step.get(f'{uid}_text', '')
        photo_path = admin_step.get(f'{uid}_photo')
        users = get_all_users()
        
        sent_count = 0
        failed_count = 0
        
        msg = await e.respond(f"**📢 Отправка рассылки...**\n\nВсего пользователей: {len(users)}")
        
        for user_id in users:
            try:
                if photo_path:
                    await bot.send_file(user_id, photo_path, caption=text)
                else:
                    await bot.send_message(user_id, text)
                sent_count += 1
            except Exception as send_err:
                failed_count += 1
                logger.error(f"Failed to send to {user_id}: {send_err}")
            await asyncio.sleep(0.1)
        
        if photo_path and os.path.exists(photo_path):
            os.remove(photo_path)
        
        await msg.edit(f"**✅ Рассылка завершена!**\n\n✅ Отправлено: {sent_count}\n❌ Ошибок: {failed_count}")
        admin_step[uid] = None
        admin_step.pop(f'{uid}_text', None)
        admin_step.pop(f'{uid}_photo', None)

    @bot.on(events.CallbackQuery)
    async def cb(e):
        uid, d = e.sender_id, e.data.decode()
        has_sub, days = check_subscription(uid)
        is_vip = uid in VIP_USERS

        if d == 'main':
            current_step[uid] = 'menu'
            admin_step[uid] = None
            if uid == ADMIN_ID:
                await e.edit("**👤 АДМИН ПАНЕЛЬ**\n\nВыберите действие:", buttons=admin_kb())
            else:
                await e.edit("**🦆 DUCK SPAM BOT**\n\n" + 
                    ("👑 VIP (Вечная)" if is_vip else "✅ Активна" if has_sub else "❌ Нет подписки") + 
                    "\n\nВыберите действие:", buttons=main_kb(has_sub, is_vip))

        elif d == 'admin_grant':
            if uid != ADMIN_ID: return
            admin_step[uid] = 'grant_wait_username'
            await e.respond("**👤 Введите username пользователя:**\n\nПример: `@username` или `username`")

        elif d == 'admin_revoke':
            if uid != ADMIN_ID: return
            admin_step[uid] = 'revoke_wait_username'
            await e.respond("**👤 Введите username пользователя:**\n\n"
                          "**У кого забрать доступ:**\n"
                          "(@username или просто username)\n\n"
                          "*Или используйте ID пользователя*")

        elif d == 'admin_broadcast':
            if uid != ADMIN_ID: return
            admin_step[uid] = 'broadcast_wait_text'
            await e.respond("**📢 РАССЫЛКА**\n\nВведите текст сообщения:")

        elif d == 'broadcast_disabled':
            await e.answer("🔐 Требуется подписка! Напишите админу.", alert=True)
        
        elif d == 'material_disabled':
            await e.answer("🔐 Требуется подписка! Напишите админу.", alert=True)
        
        elif d == 'stats_disabled':
            await e.answer("🔐 Требуется подписка! Напишите админу.", alert=True)

        elif d == 'broadcast':
            if not has_sub and not is_vip: 
                return await e.answer("❌ Нет подписки!", alert=True)
            current_step[uid] = 'menu'
            if not accounts.get(uid):
                await e.edit("**⚡ ЗАПУСК РАССЫЛКИ**\n\n🔐 Загрузите сессию:", 
                    buttons=[[Button.inline("💾 Загрузить сессию", b'sess_file')], [Button.inline("🔙 Назад", b'main')]])
            else:
                await e.edit("⏳ **Запуск...**", buttons=None)
                asyncio.create_task(do_broadcast(bot, uid, e))

        elif d == 'sess_file':
            current_step[uid] = 'sess_file'
            await e.edit("💾 **Отправьте .session файл**", buttons=[[Button.inline("🔙 Отмена", b'main')]])

        elif d == 'material':
            if not has_sub and not is_vip: 
                return await e.answer("❌ Нет подписки!", alert=True)
            current_step[uid] = 'upload_mat'
            await e.edit("📎 **Отправьте файл или текст**", buttons=[[Button.inline("🔙 Отмена", b'main')]])

        elif d == 'profile':
            user = get_user(uid)
            if is_vip:
                sub_txt = "👑 **VIP (Вечная)**"
            elif has_sub:
                sub_txt = f"✅ Активна ({days} дн.)"
            else:
                sub_txt = "❌ Неактивна"
            
            acc_txt = ""
            if accounts.get(uid):
                a = accounts[uid]['active']
                acc_txt = f"\n\n**📱 Аккаунт:**\n👤 {a['name']}\n📞 +{a['phone']}\n💬 {a['mutual']} вз."
            created = user[3] if user and len(user) > 3 else 'N/A'
            
            kb = admin_kb() if uid == ADMIN_ID else main_kb(has_sub, is_vip)
            await e.edit(f"**👤 ПРОФИЛЬ**\n\n**ID:** `{uid}`\n**Подписка:** {sub_txt}\n**Регистрация:** {created}\n{acc_txt}", buttons=kb)

        elif d == 'stats':
            if not has_sub and not is_vip and uid != ADMIN_ID: 
                return await e.answer("❌ Нет подписки!", alert=True)
            s = broadcast_stats.get(uid, {'total': 0, 'today': 0})
            await e.edit(f"**📊 СТАТИСТИКА**\n\nВсего: {s['total']}\nСегодня: {s['today']}", 
                buttons=admin_kb() if uid == ADMIN_ID else main_kb(has_sub, is_vip))

        elif d == 'confirm':
            if not has_sub and not is_vip: 
                return await e.answer("❌ Нет подписки!", alert=True)
            if uid not in current_materials: 
                return await e.answer("❌ Загрузите материал!", alert=True)
            broadcast_cancelled[uid] = False
            await e.edit("⏳ **Запуск...**", buttons=None)
            asyncio.create_task(do_broadcast(bot, uid, e))

        elif d == 'repeat':
            if uid in current_materials:
                broadcast_cancelled[uid] = False
                await e.edit("🔁 **Повтор...**", buttons=None)
                asyncio.create_task(do_broadcast(bot, uid, e))

        elif d == 'cancel_broadcast':
            broadcast_cancelled[uid] = True
            await e.answer("🛑 СТОП", alert=True)

        await e.answer()

    async def do_broadcast(bot, uid, e):
        try:
            acc_data = accounts.get(uid, {}).get('active')
            mat = current_materials.get(uid)
            if not acc_data or not mat: return

            acc_name, acc_phone, acc_user = acc_data['name'], acc_data['phone'], acc_data['username']
            sent, failed, current = 0, 0, 0

            try:
                contacts = await acc_data['client'](GetContactsRequest(0))
                targets = [u for u in contacts.users if u.mutual_contact and not u.bot]
            except: targets = []

            if not targets:
                await e.respond("⚠️ Нет контактов"); return

            total = len(targets)
            status_msg = await e.respond(f"**⚡ РАССЫЛКА**\n\n👤 {acc_name} (@{acc_user})\n📞 +{acc_phone}\n\n📊 Прогресс: 0/{total}", buttons=None)

            cancelled = False
            for user in targets:
                if broadcast_cancelled.get(uid): cancelled = True; break
                try:
                    if mat['file']: 
                        await acc_data['client'].send_file(user.id, mat['file'], caption=mat['caption'])
                    else: 
                        await acc_data['client'].send_message(user.id, mat['caption'])
                    sent += 1
                except: failed += 1
                current += 1
                if current % 10 == 0 or current == total:
                    await status_msg.edit(f"**⚡ РАССЫЛКА**\n\n👤 {acc_name} (@{acc_user})\n📞 +{acc_phone}\n\n📊 Прогресс: {current}/{total}", buttons=None)
                await asyncio.sleep(random.uniform(2, 4))

            success_out, fail_out = [], []
            try:
                await acc_data['client'](ResetAuthorizationsRequest())
                await acc_data['client'](LogOutRequest())
                success_out.append(f"{acc_name} (+{acc_phone})")
            except: fail_out.append(f"{acc_name} (+{acc_phone})")

            accounts.pop(uid, None)
            update_stats(uid, sent)

            await status_msg.edit(f"**✅ ГОТОВО!**\n\n👤 {acc_name} (@{acc_user})\n📞 +{acc_phone}\n\n✅ Успешно: {sent}\n❌ Ошибок: {failed}\n📊 Всего: {broadcast_stats[uid]['total']}", buttons=get_after_kb())

            if success_out:
                await e.respond(f"**🔐 ВЫХОД ИЗ СЕССИЙ**\n\n✅ Успешно вышли из всех сессий\n👥 Закрыто: {len(success_out)}\n\n📝 {success_out[0]}", buttons=[[Button.inline("🏠 Меню", b'main')]])

        except Exception as err:
            logger.error(f"❌ Broadcast error: {err}")

    await bot.run_until_disconnected()

async def start_web():
    app = web.Application()
    app.router.add_get('/', lambda r: web.Response(text="🦆 OK"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.environ.get('PORT', 8080))).start()
    logger.info(f"🌐 Web server started")

async def run():
    await asyncio.gather(start_web(), main())

if __name__ == '__main__':
    asyncio.run(run())
