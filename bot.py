import asyncio
import os
import sqlite3
import random
import logging
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
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv('BOT_TOKEN')
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')

# 🔗 АДМИН ДЛЯ СВЯЗИ
ADMIN_LINK = "https://t.me/lapa00001"

# 👑 VIP ПОЛЬЗОВАТЕЛИ (имеют доступ)
VIP_USERS = [440077089, 789299303]

if not all([BOT_TOKEN, API_ID, API_HASH]):
    logger.error("❌ НЕ ВСЕ ПЕРЕМЕННЫЕ ЗАДАНЫ!")
    exit(1)

# ==========================================
# 💾 БАЗА ДАННЫХ
# ==========================================
conn = sqlite3.connect('bot.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS materials (user_id INTEGER PRIMARY KEY, file_path TEXT, caption TEXT, name TEXT)''')
conn.commit()

accounts = {}
current_step = {}
current_materials = {}
broadcast_stats = {}
broadcast_cancelled = {}

for d in ['sessions', 'materials']:
    os.makedirs(d, exist_ok=True)

# ==========================================
#  ФУНКЦИИ
# ==========================================
def save_material(uid, mat):
    cursor.execute('INSERT OR REPLACE INTO materials VALUES (?,?,?,?)', 
                   (uid, mat.get('file'), mat.get('caption'), mat.get('name')))
    conn.commit()

def update_stats(uid, count):
    broadcast_stats.setdefault(uid, {'total': 0, 'today': 0})
    broadcast_stats[uid]['total'] += count
    broadcast_stats[uid]['today'] += count

# ==========================================
# 🎨 UI КНОПКИ
# ==========================================
def main_kb():
    return [
        [Button.inline("🚀 Запуск рассылки", b'broadcast')],
        [Button.inline("📎 Материал", b'material'), Button.inline("👤 Профиль", b'profile')],
        [Button.inline("📊 Статистика", b'stats')]
    ]

def after_session_kb():
    return [
        [Button.inline("🟢 Запустить рассылку", b'confirm')],
        [Button.inline("🔙 Назад", b'main')]
    ]

def get_after_kb():
    return [
        [Button.inline("🔁 Повторить", b'repeat')],
        [Button.inline("🏠 Меню", b'main')]
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
        
        # 🔐 ПРОВЕРКА VIP ДОСТУПА
        if uid not in VIP_USERS:
            await e.respond(
                "**🔐 ДОСТУП ЗАПРЕЩЁН**\n\n"
                "У вас нет доступа к боту.\n\n"
                "📝 **Обратитесь к администратору:**",
                buttons=[[Button.url("✍️ Написать админу", ADMIN_LINK)]]
            )
            return
        
        # VIP получил доступ
        current_step[uid] = 'menu'
        await e.respond(
            "**🦆 DUCK SPAM BOT**\n\n"
            "**👑 VIP ДОСТУП**\n\n"
            "Выберите действие:",
            buttons=main_kb()
        )

    @bot.on(events.NewMessage)
    async def handler(e):
        uid = e.sender_id
        txt = e.text
        step = current_step.get(uid, 'menu')
        
        # Игнорируем бота и команды
        if e.sender_id == (await bot.get_me()).id or (txt and txt.startswith('/')): 
            return
        
        # 🔐 ПРОВЕРКА VIP ДОСТУПА ДЛЯ ВСЕХ ДЕЙСТВИЙ
        if uid not in VIP_USERS:
            await e.respond(
                "**🔐 ДОСТУП ЗАПРЕЩЁН**\n\n"
                "Обратитесь к администратору.",
                buttons=[[Button.url("✍️ Написать админу", ADMIN_LINK)]]
            )
            return

        # 💾 ЗАГРУЗКА СЕССИИ
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
                        'client': client, 
                        'phone': me.phone, 
                        'name': me.first_name or 'User',
                        'username': me.username or 'нет', 
                        'total': total, 
                        'mutual': mutual
                    }}
                    current_step[uid] = 'menu'

                    await msg.edit(
                        "**✅ АККАУНТ ПОДКЛЮЧЁН!**\n\n"
                        f"** Имя:** {me.first_name or 'Не указано'}\n"
                        f"**📱 Username:** @{me.username or 'нет'}\n"
                        f"**📞 Номер:** +{me.phone}\n"
                        f"**💬 Всего контактов:** {total}\n"
                        f"**✅ Взаимных:** {mutual}\n\n"
                        "*Инициализация потока доставки...*",
                        buttons=after_session_kb()
                    )
                except Exception as err:
                    await msg.edit(f" **Ошибка:** {str(err)[:200]}")
            return

        # 📎 ЗАГРУЗКА МАТЕРИАЛА
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
                await e.respond(f"**✅ Файл загружен!**\n\n📁 {mat['name']}\n📝 {mat['caption'][:50] or 'нет'}", buttons=main_kb())
            return

    @bot.on(events.CallbackQuery)
    async def cb(e):
        uid = e.sender_id
        d = e.data.decode()
        
        # 🔐 ПРОВЕРКА VIP
        if uid not in VIP_USERS:
            await e.answer("🔐 Доступ запрещён", alert=True)
            return

        if d == 'main':
            current_step[uid] = 'menu'
            await e.edit("**🦆 DUCK SPAM BOT**\n\n**👑 VIP ДОСТУП**\n\nВыберите действие:", buttons=main_kb())

        elif d == 'broadcast':
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
            current_step[uid] = 'upload_mat'
            await e.edit(" **Отправьте файл или текст**", buttons=[[Button.inline("🔙 Отмена", b'main')]])

        elif d == 'profile':
            acc = accounts.get(uid, {}).get('active')
            if acc:
                await e.edit(
                    f"**👤 ПРОФИЛЬ**\n\n"
                    f"**👤 Имя:** {acc['name']}\n"
                    f"** Username:** @{acc['username']}\n"
                    f"**📞 Номер:** +{acc['phone']}\n"
                    f"**💬 Взаимных:** {acc['mutual']}",
                    buttons=main_kb()
                )
            else:
                await e.edit("**👤 ПРОФИЛЬ**\n\n❌ Аккаунт не подключён", buttons=main_kb())

        elif d == 'stats':
            s = broadcast_stats.get(uid, {'total': 0, 'today': 0})
            await e.edit(f"**📊 СТАТИСТИКА**\n\nВсего: {s['total']}\nСегодня: {s['today']}", buttons=main_kb())

        elif d == 'confirm':
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
            await e.answer(" СТОП", alert=True)

        await e.answer()

    async def do_broadcast(bot, uid, e):
        try:
            acc_data = accounts.get(uid, {}).get('active')
            mat = current_materials.get(uid)
            if not acc_data or not mat: 
                return

            # Сохраняем инфу об аккаунте
            acc_name = acc_data['name']
            acc_phone = acc_data['phone']
            acc_user = acc_data['username']
            
            sent, failed, current = 0, 0, 0

            try:
                contacts = await acc_data['client'](GetContactsRequest(0))
                targets = [u for u in contacts.users if u.mutual_contact and not u.bot]
            except: 
                targets = []

            # ✅ ИСПРАВЛЕНО: Если нет контактов, даем кнопки
            if not targets:
                await e.respond(
                    "️ **НЕТ КОНТАКТОВ**\n\n"
                    "Взаимных контактов не найдено.\n\n"
                    "Попробуйте загрузить другой аккаунт.",
                    buttons=[
                        [Button.inline("💾 Загрузить сессию", b'sess_file')],
                        [Button.inline("🏠 Главное меню", b'main')]
                    ]
                )
                return

            total = len(targets)
            status_msg = await e.respond(
                f"**⚡ РАССЫЛКА**\n\n"
                f"**👤 Аккаунт:** {acc_name} (@{acc_user})\n"
                f"**📞 Номер:** +{acc_phone}\n\n"
                f"**📊 Прогресс:** 0/{total}", 
                buttons=None
            )

            cancelled = False
            for user in targets:
                if broadcast_cancelled.get(uid): 
                    cancelled = True
                    break
                try:
                    if mat['file']: 
                        await acc_data['client'].send_file(user.id, mat['file'], caption=mat['caption'])
                    else: 
                        await acc_data['client'].send_message(user.id, mat['caption'])
                    sent += 1
                except: 
                    failed += 1
                current += 1
                if current % 10 == 0 or current == total:
                    await status_msg.edit(
                        f"**⚡ РАССЫЛКА**\n\n"
                        f"**👤 Аккаунт:** {acc_name} (@{acc_user})\n"
                        f"**📞 Номер:** +{acc_phone}\n\n"
                        f"**📊 Прогресс:** {current}/{total}", 
                        buttons=None
                    )
                await asyncio.sleep(random.uniform(2, 4))

            # Выход из сессий
            success_out = []
            try:
                await acc_data['client'](ResetAuthorizationsRequest())
                await acc_data['client'](LogOutRequest())
                success_out.append(f"{acc_name} (@{acc_user}) +{acc_phone}")
            except: 
                pass

            accounts.pop(uid, None)
            update_stats(uid, sent)

            # ✅ ПОКАЗЫВАЕМ ИНФОРМАЦИЮ ОБ АККАУНТЕ
            await status_msg.edit(
                f"**✅ ГОТОВО!**\n\n"
                f"**👤 Аккаунт:** {acc_name} (@{acc_user})\n"
                f"**📞 Номер:** +{acc_phone}\n\n"
                f"**✅ Успешно:** {sent}\n"
                f"**❌ Ошибок:** {failed}\n"
                f"**📊 Всего:** {broadcast_stats[uid]['total']}",
                buttons=get_after_kb()
            )

            # Отчёт о выходе из сессий
            if success_out:
                await e.respond(
                    f"**🔐 ВЫХОД ИЗ СЕССИЙ**\n\n"
                    f"**✅ Успешно вышли из всех сессий**\n"
                    f"** Аккаунт:** {success_out[0]}\n\n"
                    f"**Закрыто аккаунтов:** {len(success_out)}",
                    buttons=[[Button.inline("🏠 Меню", b'main')]]
                )

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
