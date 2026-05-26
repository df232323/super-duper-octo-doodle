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
accounts = {}  # {user_id: {acc_id: {'client': ..., 'phone': ..., 'name': ...}}}
current_step = {}  # {user_id: 'step_name'}
current_materials = {}  # {user_id: {'file': path, 'caption': text}}
material_history = {}  # {user_id: [materials]}
broadcast_stats = {}  # {user_id: {'total': int, 'daily': {date: count}}}
authorized_users = {}  # {user_id: True}

SESSIONS_DIR = 'sessions'
MATERIALS_DIR = 'materials'
for d in [SESSIONS_DIR, MATERIALS_DIR]:
    os.makedirs(d, exist_ok=True)

# ==========================================
# 🎨 КНОПКИ (INLINE - ВНУТРИ СООБЩЕНИЯ)
# ==========================================
def main_menu_kb():
    return [
        [Button.inline("🚀 Запустить рассылку", b'broadcast')],
        [Button.inline("👥 Аккаунты", b'accounts'), Button.inline("📦 Материал", b'material')],
        [Button.inline("📈 Статистика", b'stats')],
        [Button.inline("🔙 Назад в меню", b'main')]  # Кнопка назад в главном меню
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
        [Button.inline("➕ Добавить аккаунт", b'phone_login')],
        [Button.inline("🏠 Главное меню", b'main')]
    ]

def cancel_kb():
    return [[Button.inline("❌ Отмена", b'cancel')]]

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
# 🌐 ВЕБ-СЕРВЕР (для Render)
# ==========================================
async def start_web_server():
    app = web.Application()
    async def handle(r): return web.Response(text="🦆 DUCK BOT OK")
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

    # 1️⃣ ОБРАБОТКА /start
    @bot.on(events.NewMessage(pattern=r'/start'))
    async def start_cmd(event):
        uid = event.sender_id
        current_step[uid] = 'wait_pin'
        await event.respond(
            "**🔐 DUCK SPAM BOT**\n\n"
            "Для доступа введите PIN-код.\n"
            "*Введите 4 цифры для продолжения...*\n\n"
            "*Не знаете код? Обратитесь к админу.*",
            buttons=None
        )

    # 2️⃣ ОБРАБОТКА ТЕКСТА И ФАЙЛОВ
    @bot.on(events.NewMessage)
    async def handle_message(event):
        uid = event.sender_id
        text = event.message.text
        step = current_step.get(uid, 'menu')
        
        # Игнорируем команды и себя
        if (text and text.startswith('/')) or event.sender_id == (await bot.get_me()).id:
            return

        # 🔐 ПРОВЕРКА PIN
        if step == 'wait_pin':
            if text and text.isdigit() and len(text) == 4:
                if text == CORRECT_PIN:
                    authorized_users[uid] = True
                    current_step[uid] = 'menu'
                    await event.respond(
                        "**✅ ДОСТУП РАЗРЕШЁН**\n\n"
                        "**🦆 DUCK SPAM BOT**\n\n"
                        "*Добро пожаловать в панель!*\n\n"
                        "**Возможности:**\n"
                        "• Массовая рассылка\n"
                        "• Управление аккаунтами\n"
                        "• Загрузка файлов\n"
                        "• Статистика\n\n"
                        "**Выберите действие:**",
                        buttons=main_menu_kb()
                    )
                else:
                    await event.respond("❌ **Неверный PIN**. Попробуйте снова.")
            return

        if not authorized_users.get(uid):
            current_step[uid] = 'wait_pin'
            await event.respond("🔐 Введите PIN-код.", buttons=None)
            return

        # 📱 ВХОД ПО НОМЕРУ
        if step == 'wait_phone':
            if text and text.startswith('+') and text[1:].isdigit():
                current_step[uid] = 'wait_code'
                client = TelegramClient(f'acc_{uid}_{len(accounts.get(uid,{}))}', API_ID, API_HASH)
                await client.connect()
                await client.send_code_request(text)
                if uid not in accounts: accounts[uid] = {}
                acc_id = f'acc_{len(accounts[uid])+1}'
                accounts[uid][acc_id] = {'client': client, 'phone': text}
                await event.respond(f"📨 **Код отправлен** на `{text}`\n\nВведите код:", buttons=cancel_kb())
            return

        # 🔢 ВВОД КОДА
        if step == 'wait_code':
            if text and text.isdigit() and 4 <= len(text) <= 6:
                accs = accounts.get(uid, {})
                if not accs: return
                last_acc = list(accs.values())[-1]
                try:
                    await last_acc['client'].sign_in(last_acc['phone'], text)
                    me_acc = await last_acc['client'].get_me()
                    last_acc.update({'client': last_acc['client'], 'phone': me_acc.phone, 'name': me_acc.first_name})
                    current_step[uid] = 'menu'
                    await event.respond(f"✅ **{me_acc.first_name}** добавлен!\n📱 +{me_acc.phone}", buttons=accounts_kb())
                except:
                    await event.respond("❌ Неверный код. Попробуйте снова.")
            return

        # 💾 ЗАГРУЗКА SESSION ФАЙЛА
        if step == 'wait_sess_file':
            if event.message.file and event.message.file.name.endswith('.session'):
                await event.respond("⏳ Загружаю session...")
                path = await event.message.download_media(file=os.path.join(SESSIONS_DIR, f'acc_{uid}_{len(accounts.get(uid,{}))}.session'))
                client = TelegramClient(path.replace('.session',''), API_ID, API_HASH)
                await client.connect()
                me_acc = await client.get_me()
                if uid not in accounts: accounts[uid] = {}
                acc_id = f'acc_{len(accounts[uid])+1}'
                accounts[uid][acc_id] = {'client': client, 'phone': me_acc.phone, 'name': me_acc.first_name}
                current_step[uid] = 'menu'
                await event.respond(f"✅ **Session загружен**: {me_acc.first_name}", buttons=accounts_kb())
            return

        # 🔑 SESSION STRING
        if step == 'wait_sess_str':
            if text and text.startswith('1') and len(text) > 100:
                client = TelegramClient(StringSession(text), API_ID, API_HASH)
                await client.connect()
                me_acc = await client.get_me()
                if uid not in accounts: accounts[uid] = {}
                acc_id = f'acc_{len(accounts[uid])+1}'
                accounts[uid][acc_id] = {'client': client, 'phone': me_acc.phone, 'name': me_acc.first_name}
                current_step[uid] = 'menu'
                await event.respond(f"✅ **String принят**: {me_acc.first_name}", buttons=accounts_kb())
            return

        # 📥 ЗАГРУЗКА МАТЕРИАЛА
        if step == 'wait_material':
            if event.message.file:
                name = event.message.file.name or 'file'
                path = await event.message.download_media(file=os.path.join(MATERIALS_DIR, f'mat_{uid}_{name}'))
                caption = event.message.message or ''
                if uid not in material_history: material_history[uid] = []
                mat = {'file': path, 'caption': caption, 'name': name}
                material_history[uid].append(mat)
                current_materials[uid] = mat
                current_step[uid] = 'menu'
                await event.respond(f"✅ **Файл сохранён**: {name}\n📝 Текст: {caption[:50] if caption else 'нет'}", buttons=material_kb())
            elif text:
                if uid not in material_history: material_history[uid] = []
                mat = {'file': None, 'caption': text, 'name': 'Text'}
                material_history[uid].append(mat)
                current_materials[uid] = mat
                current_step[uid] = 'menu'
                await event.respond(f"✅ **Текст сохранён**\n📝 {text[:100]}", buttons=material_kb())
            return

    # 3️⃣ ОБРАБОТКА INLINE КНОПОК
    @bot.on(events.CallbackQuery)
    async def callback_handler(event):
        uid = event.sender_id
        if not authorized_users.get(uid):
            return await event.answer("🔐 Введите /start и PIN", alert=True)
        
        data = event.data.decode()
        
        # ГЛАВНОЕ МЕНЮ
        if data == 'main':
            current_step[uid] = 'menu'
            await event.edit(
                "**🦆 DUCK SPAM BOT**\n\n*Панель управления*\n\n**Выберите действие:**",
                buttons=main_menu_kb()
            )
        
        # АККАУНТЫ
        elif data == 'accounts':
            accs = accounts.get(uid, {})
            text = f"**👥 АККАУНТЫ**\n\nВсего: {len(accs)}\n\n"
            if accs:
                for i, (aid, acc) in enumerate(accs.items(), 1):
                    text += f"{i}. **{acc['name']}** (`{acc['phone']}`)\n"
            else:
                text += "*Аккаунтов нет*"
            await event.edit(text, buttons=accounts_kb())
        
        # МАТЕРИАЛ
        elif data == 'material':
            mats = material_history.get(uid, [])
            text = f"**📦 МАТЕРИАЛЫ**\n\nВсего: {len(mats)}\n\n"
            if current_materials.get(uid):
                text += f"📎 Текущий: **{current_materials[uid]['name']}**\n"
            else:
                text += "*Материал не загружен*"
            await event.edit(text, buttons=material_kb())
        
        # СТАТИСТИКА
        elif data == 'stats':
            total = broadcast_stats.get(uid, {}).get('total', 0)
            today = get_stats_data(uid, 'today')
            await event.edit(
                f"**📈 СТАТИСТИКА**\n\n"
                f"**Всего отправлено:** {total}\n"
                f"**Сегодня:** {today}\n"
                f"**Аккаунтов:** {len(accounts.get(uid, {}))}\n"
                f"**Материалов:** {len(material_history.get(uid, []))}\n\n"
                f"**Выберите период:**",
                buttons=stats_kb()
            )
        elif data == 'stats_today':
            val = get_stats_data(uid, 'today')
            await event.answer(f"📅 За сегодня: {val}", alert=True)
        elif data == 'stats_week':
            val = get_stats_data(uid, 'week')
            await event.answer(f"📅 За неделю: {val}", alert=True)
        elif data == 'stats_month':
            val = get_stats_data(uid, 'month')
            await event.answer(f"📅 За месяц: {val}", alert=True)
        
        # ДЕЙСТВИЯ
        elif data == 'phone_login':
            current_step[uid] = 'wait_phone'
            await event.edit("**📱 ВХОД ПО НОМЕРУ**\n\nВведите номер (+7999...)", buttons=cancel_kb())
        elif data == 'sess_file':
            current_step[uid] = 'wait_sess_file'
            await event.edit("**💾 SESSION ФАЙЛ**\n\nОтправьте файл .session", buttons=cancel_kb())
        elif data == 'sess_str':
            current_step[uid] = 'wait_sess_str'
            await event.edit("**🔑 SESSION STRING**\n\nВведите строку (начинается на 1)", buttons=cancel_kb())
        elif data == 'upload_mat':
            current_step[uid] = 'wait_material'
            await event.edit("**📥 ЗАГРУЗКА МАТЕРИАЛА**\n\nОтправьте файл или текст", buttons=cancel_kb())
        
        # РАССЫЛКА
        elif data == 'broadcast':
            accs = accounts.get(uid, {})
            if not accs:
                return await event.answer("❌ Сначала добавьте аккаунты!", alert=True)
            if uid not in current_materials:
                return await event.answer("❌ Сначала загрузите материал!", alert=True)
            
            # Считаем контакты
            total_contacts = 0
            for acc in accs.values():
                try:
                    contacts = await acc['client'](GetContactsRequest(0))
                    mutual = [u for u in contacts.users if u.mutual_contact and not u.bot]
                    total_contacts += len(mutual)
                except: pass
            
            await event.edit(
                f"**🚀 ЗАПУСК РАССЫЛКИ**\n\n"
                f"**Аккаунтов:** {len(accs)}\n"
                f"**Контактов:** {total_contacts}\n"
                f"**Материал:** {current_materials[uid]['name']}\n\n"
                f"**Запустить?**",
                buttons=confirm_broadcast_kb()
            )
        
        elif data == 'confirm_broadcast':
            await event.edit("⏳ **Запускаю рассылку...**\n\n*Не закрывайте бота*")
            await do_broadcast(bot, uid, event)
        
        elif data == 'repeat_broadcast':
            if uid in current_materials:
                await event.edit("🔁 **Повторяю рассылку...**")
                await do_broadcast(bot, uid, event)
            else:
                await event.answer("❌ Нет материала", alert=True)
        
        elif data == 'cancel':
            current_step[uid] = 'menu'
            await event.edit("**❌ Отменено**", buttons=main_menu_kb())
        
        await event.answer()

    # ==========================================
    # 🚀 ФУНКЦИЯ РАССЫЛКИ
    # ==========================================
    async def do_broadcast(bot, user_id, event):
        accs = accounts.get(user_id, {})
        mat = current_materials.get(user_id)
        
        if not accs or not mat:
            return
        
        sent = 0
        failed = 0
        total_sent = 0
        
        for acc_id, acc_data in accs.items():
            try:
                contacts = await acc_data['client'](GetContactsRequest(0))
                targets = [u for u in contacts.users if u.mutual_contact and not u.bot]
                
                await event.respond(f"📱 **Аккаунт {acc_data['name']}**\nНайдено {len(targets)} контактов")
                
                for user in targets:
                    try:
                        if mat['file']:
                            await acc_data['client'].send_file(
                                user.id, 
                                mat['file'], 
                                caption=mat['caption'],
                                attributes=[DocumentAttributeFilename(file_name=mat['name'])]
                            )
                        else:
                            await acc_data['client'].send_message(user.id, mat['caption'])
                        sent += 1
                        total_sent += 1
                        await asyncio.sleep(2)  # Пауза
                    except Exception as e:
                        failed += 1
            except Exception as e:
                await event.respond(f"❌ Ошибка аккаунта {acc_id}: {e}")
        
        update_stats(user_id, total_sent)
        
        # Очищаем сессии (опционально)
        for acc in accs.values():
            try: await acc['client'].disconnect()
            except: pass
        
        await event.respond(
            f"**✅ РАССЫЛКА ЗАВЕРШЕНА**\n\n"
            f"**Отправлено:** {sent}\n"
            f"**Ошибок:** {failed}\n"
            f"**Всего:** {broadcast_stats[user_id]['total']}",
            buttons=after_broadcast_kb()
        )
        current_step[user_id] = 'after_broadcast'

    await bot.run_until_disconnected()

# ==========================================
# 🏁 ЗАПУСК
# ==========================================
async def run_all():
    await asyncio.gather(start_web_server(), main())

if __name__ == '__main__':
    asyncio.run(run_all())
