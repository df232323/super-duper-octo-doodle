import asyncio
import os
from datetime import datetime, timedelta
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.functions.contacts import GetContactsRequest
from telethon.tl.types import DocumentAttributeFilename
from telethon.tl.custom import Button
from aiohttp import web

# ==========================================
# 🔑 НАСТРОЙКИ
# ==========================================
BOT_TOKEN = os.getenv('BOT_TOKEN')
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
CORRECT_PIN = "6611"

if not BOT_TOKEN or not API_ID or not API_HASH:
    print("❌ ОШИБКА: Нет переменных окружения!")
    exit(1)

# Хранилище
accounts = {}
current_step = {}
current_materials = {}
material_history = {}
broadcast_stats = {}
authorized_users = {}

SESSIONS_DIR = 'sessions'
MATERIALS_DIR = 'materials'
for d in [SESSIONS_DIR, MATERIALS_DIR]:
    os.makedirs(d, exist_ok=True)

# ==========================================
# 🎨 ТОЛЬКО INLINE КНОПКИ (НИКАКОЙ НИЖНЕЙ ПАНЕЛИ!)
# ==========================================
def main_menu_kb():
    return [
        [Button.inline("🚀 Запустить рассылку", b'broadcast')],
        [Button.inline("👥 Аккаунты", b'accounts'), Button.inline("📦 Материал", b'material')],
        [Button.inline("📈 Статистика", b'stats')]
    ]

def accounts_kb():
    return [
        [Button.inline("📱 По номеру", b'phone_login')],
        [Button.inline("💾 Session файл", b'sess_file')],
        [Button.inline("🔑 Session String", b'sess_str')],
        [Button.inline("🔙 Назад", b'main')]
    ]

def material_kb():
    return [
        [Button.inline("📥 Загрузить материал", b'upload_mat')],
        [Button.inline("🔙 Назад", b'main')]
    ]

def stats_kb():
    return [
        [Button.inline("📅 За сегодня", b'stats_today')],
        [Button.inline("📅 За неделю", b'stats_week')],
        [Button.inline("📅 За месяц", b'stats_month')],
        [Button.inline("🔙 Назад", b'main')]
    ]

def confirm_broadcast_kb():
    return [
        [Button.inline("✅ Да, запустить", b'confirm_broadcast')],
        [Button.inline("❌ Отмена", b'main')]
    ]

def after_broadcast_kb():
    return [
        [Button.inline("🔁 Повторить", b'repeat_broadcast')],
        [Button.inline("📥 Новый материал", b'upload_mat')],
        [Button.inline("➕ Добавить аккаунт", b'accounts')],
        [Button.inline("🏠 Главное меню", b'main')]
    ]

def cancel_kb():
    return [[Button.inline("❌ Отмена", b'cancel')]]

def account_info_kb(acc_id):
    """Кнопки после загрузки аккаунта"""
    return [
        [Button.inline("🚀 Запустить рассылку", b'broadcast')],
        [Button.inline("➕ Добавить ещё", b'accounts')],
        [Button.inline("🔙 Назад", b'main')]
    ]

# ==========================================
# 🔧 ФУНКЦИИ
# ==========================================
def get_stats_data(user_id, period='today'):
    if user_id not in broadcast_stats:
        return 0
    stats = broadcast_stats[user_id]
    today = datetime.now().date()
    
    if period == 'today':
        return stats['daily'].get(str(today), 0)
    elif period == 'week':
        total = 0
        for i in range(7):
            date = str(today - timedelta(days=i))
            total += stats['daily'].get(date, 0)
        return total
    elif period == 'month':
        total = 0
        for i in range(30):
            date = str(today - timedelta(days=i))
            total += stats['daily'].get(date, 0)
        return total
    return stats.get('total', 0)

def update_stats(user_id, count):
    if user_id not in broadcast_stats:
        broadcast_stats[user_id] = {'total': 0, 'daily': {}}
    today = str(datetime.now().date())
    broadcast_stats[user_id]['total'] += count
    broadcast_stats[user_id]['daily'][today] = broadcast_stats[user_id]['daily'].get(today, 0) + count

# ==========================================
# 🌐 ВЕБ-СЕРВЕР
# ==========================================
async def start_web_server():
    app = web.Application()
    async def handle(r): return web.Response(text="🦆 OK")
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get('PORT', 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print("🌐 Web server running")

# ==========================================
# 🏁 ГЛАВНЫЙ БОТ
# ==========================================
async def main():
    bot = TelegramClient('bot_session', API_ID, API_HASH)
    await bot.start(bot_token=BOT_TOKEN)
    me = await bot.get_me()
    print(f"✅ {me.first_name} запущен!")

    # 1️⃣ /start
    @bot.on(events.NewMessage(pattern=r'/start'))
    async def start_cmd(event):
        uid = event.sender_id
        current_step[uid] = 'wait_pin'
        await event.respond(
            "**🔐 DUCK SPAM BOT**\n\n"
            "Для доступа введите PIN-код.\n"
            "*Введите 4 цифры...*\n\n"
            "*Не знаете код? Обратитесь к админу.*",
            buttons=None
        )

    # 2️⃣ ОБРАБОТКА СООБЩЕНИЙ
    @bot.on(events.NewMessage)
    async def handle_message(event):
        uid = event.sender_id
        text = event.message.text
        step = current_step.get(uid, 'menu')
        
        if (text and text.startswith('/')) or event.sender_id == me.id:
            return

        # 🔐 PIN
        if step == 'wait_pin':
            if text and text.isdigit() and len(text) == 4:
                if text == CORRECT_PIN:
                    authorized_users[uid] = True
                    current_step[uid] = 'menu'
                    await event.respond(
                        "**✅ ДОСТУП РАЗРЕШЁН**\n\n"
                        "**🦆 DUCK SPAM BOT**\n\n"
                        "*Панель управления*\n\n"
                        "**Выберите действие:**",
                        buttons=main_menu_kb()
                    )
                else:
                    await event.respond("❌ **Неверный PIN**")
            return

        if not authorized_users.get(uid):
            current_step[uid] = 'wait_pin'
            await event.respond("🔐 Введите PIN-код.", buttons=None)
            return

        # 📱 НОМЕР
        if step == 'wait_phone':
            if text and text.startswith('+') and text[1:].isdigit():
                current_step[uid] = 'wait_code'
                client = TelegramClient(f'acc_{uid}_{len(accounts.get(uid,{}))}', API_ID, API_HASH)
                await client.connect()
                await client.send_code_request(text)
                if uid not in accounts: accounts[uid] = {}
                acc_id = f'acc_{len(accounts[uid])+1}'
                accounts[uid][acc_id] = {'client': client, 'phone': text}
                await event.edit_message(event.message, 
                    f"📨 **Код отправлен** на `{text}`\n\nВведите код:", 
                    buttons=cancel_kb()
                )
            return

        # 🔢 КОД
        if step == 'wait_code':
            if text and text.isdigit() and 4 <= len(text) <= 6:
                accs = accounts.get(uid, {})
                if not accs: return
                last_acc = list(accs.values())[-1]
                try:
                    await last_acc['client'].sign_in(last_acc['phone'], text)
                    me_acc = await last_acc['client'].get_me()
                    
                    # Получаем контакты
                    try:
                        contacts = await last_acc['client'](GetContactsRequest(0))
                        all_contacts = len([u for u in contacts.users if not u.bot])
                        mutual_contacts = len([u for u in contacts.users if u.mutual_contact and not u.bot])
                    except:
                        all_contacts = 0
                        mutual_contacts = 0
                    
                    last_acc.update({
                        'client': last_acc['client'], 
                        'phone': me_acc.phone, 
                        'name': me_acc.first_name,
                        'username': me_acc.username or 'нет',
                        'mutual': mutual_contacts
                    })
                    current_step[uid] = 'menu'
                    
                    await event.respond(
                        f"**✅ Синхронизация завершена!**\n\n"
                        f"**👤 Профиль:** @{me_acc.username or 'нет'}\n"
                        f"**📞 Номер:** {me_acc.phone}\n"
                        f"**💬 Доступно контактов:** {all_contacts}\n"
                        f"**✅ Взаимных контактов:** {mutual_contacts}\n"
                        f"**⚡️ Состояние:** Подключение стабильно",
                        buttons=account_info_kb(last_acc['phone'])
                    )
                except Exception as e:
                    await event.respond(f"❌ Ошибка: {e}")
            return

        # 💾 SESSION ФАЙЛ
        if step == 'wait_sess_file':
            if event.message.file and event.message.file.name.endswith('.session'):
                msg = await event.respond("⏳ Загружаю session...")
                try:
                    path = await event.message.download_media(
                        file=os.path.join(SESSIONS_DIR, f'acc_{uid}_{len(accounts.get(uid,{}))}.session')
                    )
                    client = TelegramClient(path.replace('.session',''), API_ID, API_HASH)
                    await client.connect()
                    me_acc = await client.get_me()
                    
                    # Контакты
                    try:
                        contacts = await client(GetContactsRequest(0))
                        all_contacts = len([u for u in contacts.users if not u.bot])
                        mutual_contacts = len([u for u in contacts.users if u.mutual_contact and not u.bot])
                    except:
                        all_contacts = 0
                        mutual_contacts = 0
                    
                    if uid not in accounts: accounts[uid] = {}
                    acc_id = f'acc_{len(accounts[uid])+1}'
                    accounts[uid][acc_id] = {
                        'client': client, 
                        'phone': me_acc.phone, 
                        'name': me_acc.first_name,
                        'username': me_acc.username or 'нет',
                        'mutual': mutual_contacts
                    }
                    current_step[uid] = 'menu'
                    
                    await msg.edit(
                        f"**✅ Синхронизация завершена!**\n\n"
                        f"**👤 Профиль:** @{me_acc.username or 'нет'}\n"
                        f"**📞 Номер:** {me_acc.phone}\n"
                        f"**💬 Доступно контактов:** {all_contacts}\n"
                        f"**✅ Взаимных контактов:** {mutual_contacts}\n"
                        f"**⚡️ Состояние:** Подключение стабильно",
                        buttons=account_info_kb(me_acc.phone)
                    )
                except Exception as e:
                    await msg.edit(f"❌ Ошибка: {e}")
            return

        # 🔑 STRING
        if step == 'wait_sess_str':
            if text and text.startswith('1') and len(text) > 100:
                client = TelegramClient(StringSession(text), API_ID, API_HASH)
                await client.connect()
                me_acc = await client.get_me()
                
                try:
                    contacts = await client(GetContactsRequest(0))
                    all_contacts = len([u for u in contacts.users if not u.bot])
                    mutual_contacts = len([u for u in contacts.users if u.mutual_contact and not u.bot])
                except:
                    all_contacts = 0
                    mutual_contacts = 0
                
                if uid not in accounts: accounts[uid] = {}
                acc_id = f'acc_{len(accounts[uid])+1}'
                accounts[uid][acc_id] = {
                    'client': client, 
                    'phone': me_acc.phone, 
                    'name': me_acc.first_name,
                    'username': me_acc.username or 'нет',
                    'mutual': mutual_contacts
                }
                current_step[uid] = 'menu'
                
                await event.respond(
                    f"**✅ Синхронизация завершена!**\n\n"
                    f"**👤 Профиль:** @{me_acc.username or 'нет'}\n"
                    f"**📞 Номер:** {me_acc.phone}\n"
                    f"**💬 Доступно контактов:** {all_contacts}\n"
                    f"**✅ Взаимных контактов:** {mutual_contacts}\n"
                    f"**⚡️ Состояние:** Подключение стабильно",
                    buttons=account_info_kb(me_acc.phone)
                )
            return

        # 📥 МАТЕРИАЛ
        if step == 'wait_material':
            if event.message.file:
                name = event.message.file.name or 'file'
                path = await event.message.download_media(
                    file=os.path.join(MATERIALS_DIR, f'mat_{uid}_{name}')
                )
                caption = event.message.message or ''
                if uid not in material_history: material_history[uid] = []
                mat = {'file': path, 'caption': caption, 'name': name}
                material_history[uid].append(mat)
                current_materials[uid] = mat
                current_step[uid] = 'menu'
                await event.respond(
                    f"**✅ Файл сохранён!**\n\n"
                    f"**📁 {name}**\n"
                    f"**📝 Текст:** {caption[:100] if caption else 'нет'}",
                    buttons=material_kb()
                )
            elif text:
                if uid not in material_history: material_history[uid] = []
                mat = {'file': None, 'caption': text, 'name': 'Text'}
                material_history[uid].append(mat)
                current_materials[uid] = mat
                current_step[uid] = 'menu'
                await event.respond(
                    f"**✅ Текст сохранён!**\n\n"
                    f"**📝 {text[:100]}{'...' if len(text)>100 else ''}**",
                    buttons=material_kb()
                )
            return

    # 3️⃣ INLINE КНОПКИ
    @bot.on(events.CallbackQuery)
    async def callback_handler(event):
        uid = event.sender_id
        if not authorized_users.get(uid):
            return await event.answer("🔐 Введите /start", alert=True)
        
        data = event.data.decode()
        
        if data == 'main':
            current_step[uid] = 'menu'
            await event.edit("**🦆 DUCK SPAM BOT**\n\n*Панель*\n\n**Выберите:**", buttons=main_menu_kb())
        
        elif data == 'accounts':
            accs = accounts.get(uid, {})
            text = f"**👥 АККАУНТЫ**\n\nВсего: {len(accs)}\n\n"
            if accs:
                for i, (aid, acc) in enumerate(accs.items(), 1):
                    text += f"{i}. **{acc['name']}** - {acc.get('mutual', 0)} взаимных\n"
            else:
                text += "*Нет аккаунтов*"
            await event.edit(text, buttons=accounts_kb())
        
        elif data == 'material':
            mats = material_history.get(uid, [])
            text = f"**📦 МАТЕРИАЛЫ**\n\nВсего: {len(mats)}\n\n"
            if current_materials.get(uid):
                text += f"📎 Текущий: **{current_materials[uid]['name']}**"
            else:
                text += "*Нет материала*"
            await event.edit(text, buttons=material_kb())
        
        elif data == 'stats':
            total = broadcast_stats.get(uid, {}).get('total', 0)
            today = get_stats_data(uid, 'today')
            week = get_stats_data(uid, 'week')
            month = get_stats_data(uid, 'month')
            await event.edit(
                f"**📈 СТАТИСТИКА**\n\n"
                f"**📊 Всего:** {total}\n"
                f"**📅 Сегодня:** {today}\n"
                f"**📅 За неделю:** {week}\n"
                f"**📅 За месяц:** {month}\n"
                f"**👥 Аккаунтов:** {len(accounts.get(uid, {}))}\n"
                f"**📦 Материалов:** {len(material_history.get(uid, []))}",
                buttons=stats_kb()
            )
        elif data == 'stats_today':
            await event.answer(f"📅 Сегодня: {get_stats_data(uid, 'today')}", alert=True)
        elif data == 'stats_week':
            await event.answer(f"📅 За неделю: {get_stats_data(uid, 'week')}", alert=True)
        elif data == 'stats_month':
            await event.answer(f"📅 За месяц: {get_stats_data(uid, 'month')}", alert=True)
        
        elif data == 'phone_login':
            current_step[uid] = 'wait_phone'
            await event.edit("**📱 ВХОД ПО НОМЕРУ**\n\nВведите (+7...)", buttons=cancel_kb())
        elif data == 'sess_file':
            current_step[uid] = 'wait_sess_file'
            await event.edit("**💾 SESSION**\n\nОтправьте .session файл", buttons=cancel_kb())
        elif data == 'sess_str':
            current_step[uid] = 'wait_sess_str'
            await event.edit("**🔑 STRING**\n\nВведите строку", buttons=cancel_kb())
        elif data == 'upload_mat':
            current_step[uid] = 'wait_material'
            await event.edit("**📥 МАТЕРИАЛ**\n\nОтправьте файл/текст", buttons=cancel_kb())
        
        elif data == 'broadcast':
            accs = accounts.get(uid, {})
            if not accs:
                return await event.answer("❌ Добавьте аккаунты!", alert=True)
            if uid not in current_materials:
                return await event.answer("❌ Загрузите материал!", alert=True)
            
            total_mutual = sum(acc.get('mutual', 0) for acc in accs.values())
            await event.edit(
                f"**🚀 РАССЫЛКА**\n\n"
                f"**👥 Аккаунтов:** {len(accs)}\n"
                f"**📞 Взаимных контактов:** {total_mutual}\n"
                f"**📦 Материал:** {current_materials[uid]['name']}\n\n"
                f"**Запустить?**",
                buttons=confirm_broadcast_kb()
            )
        
        elif data == 'confirm_broadcast':
            await event.edit("⏳ **Запускаю...**")
            await do_broadcast(bot, uid, event)
        
        elif data == 'repeat_broadcast':
            if uid in current_materials:
                await event.edit("🔁 **Повтор...**")
                await do_broadcast(bot, uid, event)
            else:
                await event.answer("❌ Нет материала", alert=True)
        
        elif data == 'cancel':
            current_step[uid] = 'menu'
            await event.edit("**❌ Отмена**", buttons=main_menu_kb())
        
        await event.answer()

    # 🚀 РАССЫЛКА
    async def do_broadcast(bot, user_id, event):
        accs = accounts.get(user_id, {})
        mat = current_materials.get(user_id)
        
        if not accs or not mat:
            return
        
        sent = 0
        failed = 0
        
        for acc_id, acc_data in accs.items():
            try:
                contacts = await acc_data['client'](GetContactsRequest(0))
                targets = [u for u in contacts.users if u.mutual_contact and not u.bot]
                
                await event.respond(f"📱 **{acc_data['name']}** - {len(targets)} контактов")
                
                for user in targets:
                    try:
                        if mat['file']:
                            await acc_data['client'].send_file(
                                user.id, mat['file'], caption=mat['caption'],
                                attributes=[DocumentAttributeFilename(file_name=mat['name'])]
                            )
                        else:
                            await acc_data['client'].send_message(user.id, mat['caption'])
                        sent += 1
                        await asyncio.sleep(2)
                    except:
                        failed += 1
            except Exception as e:
                await event.respond(f"❌ {acc_id}: {e}")
        
        update_stats(user_id, sent)
        
        await event.respond(
            f"**✅ ГОТОВО!**\n\n"
            f"**✅ Отправлено:** {sent}\n"
            f"**❌ Ошибок:** {failed}\n"
            f"**📊 Всего:** {broadcast_stats[user_id]['total']}",
            buttons=after_broadcast_kb()
        )
        current_step[user_id] = 'after'

    await bot.run_until_disconnected()

# ==========================================
async def run_all():
    await asyncio.gather(start_web_server(), main())

if __name__ == '__main__':
    asyncio.run(run_all())
