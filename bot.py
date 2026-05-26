import asyncio
import os
from datetime import datetime
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
API_ID = os.getenv('API_ID')
API_HASH = os.getenv('API_HASH')
CORRECT_PIN = "6611"  # PIN код для доступа

if not BOT_TOKEN or not API_ID or not API_HASH:
    print("❌ ОШИБКА: Нет переменных окружения!")
    exit(1)

API_ID = int(API_ID)

SESSIONS_DIR = 'sessions'
MATERIALS_DIR = 'materials'
for d in [SESSIONS_DIR, MATERIALS_DIR]:
    if not os.path.exists(d):
        os.makedirs(d)

accounts = {}
current_step = {}
current_materials = {}
material_history = {}
broadcast_stats = {}
authorized_users = {}  # Кто ввёл правильный PIN

# ==========================================
# 🎨 DISCORD СТИЛИ И КНОПКИ
# ==========================================
def get_welcome_kb():
    """Кнопки для приветствия"""
    return [
        [Button.inline("🚀 Запустить рассылку", b'broadcast')],
        [Button.inline("👥 Аккаунты", b'accounts'), Button.inline("📦 Материал", b'material')],
        [Button.inline("📈 Статистика", b'stats'), Button.inline("⚙️ Настройки", b'settings')]
    ]

def get_accounts_kb():
    """Кнопки аккаунтов"""
    return [
        [Button.inline("➕ Добавить аккаунт", b'add_account')],
        [Button.inline("📱 По номеру", b'phone_login')],
        [Button.inline("💾 Session файл", b'session_file')],
        [Button.inline("🔑 Session String", b'session_string')],
        [Button.inline("🔙 Назад", b'main_menu')]
    ]

def get_material_kb():
    """Кнопки материалов"""
    return [
        [Button.inline("📥 Загрузить материал", b'upload_material')],
        [Button.inline("📚 История материалов", b'material_history')],
        [Button.inline("🔙 Назад", b'main_menu')]
    ]

def get_broadcast_confirm_kb():
    """Подтверждение рассылки"""
    return [
        [Button.inline("✅ Да, запустить", b'confirm_broadcast')],
        [Button.inline("❌ Отмена", b'main_menu')]
    ]

def get_after_broadcast_kb():
    """После рассылки"""
    return [
        [Button.inline("🔁 Повторить", b'repeat_broadcast')],
        [Button.inline("📥 Новый материал", b'new_material')],
        [Button.inline("➕ Добавить аккаунт", b'add_account')],
        [Button.inline("🏠 Главное меню", b'main_menu')]
    ]

def get_cancel_kb():
    """Кнопка отмены"""
    return [[Button.inline("❌ Отмена", b'cancel')]]

# ==========================================
# 🔧 ФУНКЦИИ
# ==========================================
def get_today_key():
    return datetime.now().strftime('%Y-%m-%d')

def get_month_key():
    return datetime.now().strftime('%Y-%m')

def update_stats(user_id, sent_count):
    if user_id not in broadcast_stats:
        broadcast_stats[user_id] = {'total': 0, 'broadcasts': 0, 'daily': {}, 'monthly': {}}
    today, month = get_today_key(), get_month_key()
    broadcast_stats[user_id]['total'] += sent_count
    broadcast_stats[user_id]['broadcasts'] += 1
    broadcast_stats[user_id]['daily'][today] = broadcast_stats[user_id]['daily'].get(today, 0) + sent_count
    broadcast_stats[user_id]['monthly'][month] = broadcast_stats[user_id]['monthly'].get(month, 0) + sent_count

def get_stats(user_id):
    if user_id not in broadcast_stats:
        return {'total': 0, 'broadcasts': 0, 'today': 0, 'month': 0}
    s = broadcast_stats[user_id]
    return {
        'total': s['total'],
        'broadcasts': s['broadcasts'],
        'today': s['daily'].get(get_today_key(), 0),
        'month': s['monthly'].get(get_month_key(), 0)
    }

def get_user_accounts(user_id):
    return accounts.get(user_id, {})

def save_material(user_id, file_path, caption, name=None):
    if user_id not in material_history:
        material_history[user_id] = []
    material = {
        'file': file_path,
        'caption': caption,
        'name': name or f"Материал {len(material_history[user_id]) + 1}"
    }
    material_history[user_id].append(material)
    current_materials[user_id] = material
    return material

def clear_user_sessions(user_id):
    if user_id in accounts:
        for acc in accounts[user_id].values():
            try:
                asyncio.create_task(acc['client'].disconnect())
            except:
                pass
        accounts[user_id] = {}

# ==========================================
# 🌐 ВЕБ-СЕРВЕР ДЛЯ RENDER
# ==========================================
async def start_web_server():
    app = web.Application()
    async def handle(request):
        return web.Response(text="🦆 DUCK BOT is alive!")
    app.router.add_get('/', handle)
    app.router.add_get('/health', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get('PORT', 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print("🌐 Web server running")

# ==========================================
# 🏁 ГЛАВНАЯ ФУНКЦИЯ
# ==========================================
async def main():
    bot = TelegramClient('bot_session', API_ID, API_HASH)
    await bot.start(bot_token=BOT_TOKEN)
    bot_me = await bot.get_me()
    bot_id = bot_me.id
    
    print("🟢 DUCK BOT запущен!")
    
    # ==========================================
    # 📱 PIN КОД ПРИ СТАРТЕ
    # ==========================================
    @bot.on(events.NewMessage(pattern='/start'))
    async def start_handler(event):
        if event.sender_id == bot_id:
            return
        user_id = event.sender_id
        current_step[user_id] = 'wait_pin'
        
        await event.respond(
            "**🎮 DISCORD SPAM BOT**\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "**🔐 ТРЕБУЕТСЯ АВТОРИЗАЦИЯ**\n\n"
            "Для доступа к панели управления необходимо ввести PIN-код.\n\n"
            "📝 *Введите 4-значный PIN-код для продолжения...*\n\n"
            "*Если вы не знаете код, обратитесь к администратору.*\n"
            "━━━━━━━━━━━━━━━━━━━━",
            buttons=None
        )

    @bot.on(events.NewMessage)
    async def pin_handler(event):
        if event.sender_id == bot_id:
            return
        
        user_id = event.sender_id
        text = event.message.text
        
        # Если ждём PIN код
        if current_step.get(user_id) == 'wait_pin':
            if text and text.isdigit() and len(text) == 4:
                if text == CORRECT_PIN:
                    authorized_users[user_id] = True
                    current_step[user_id] = 'menu'
                    
                    await event.respond(
                        "**✅ ДОСТУП РАЗРЕШЁН**\n\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "**🎮 DISCORD SPAM BOT v2.0**\n\n"
                        "👋 *Добро пожаловать в панель управления!*\n\n"
                        "**📊 Возможности бота:**\n"
                        "• 📤 Массовая рассылка сообщений\n"
                        "• 👥 Управление несколькими аккаунтами\n"
                        "• 📦 Загрузка файлов и медиа\n"
                        "• 📈 Подробная статистика\n\n"
                        "**⚡️ Выберите действие ниже:**\n"
                        "━━━━━━━━━━━━━━━━━━━━",
                        buttons=get_welcome_kb()
                    )
                else:
                    await event.respond(
                        "**❌ НЕВЕРНЫЙ PIN-КОД**\n\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "Введённый код не совпадает.\n\n"
                        "🔁 *Попробуйте ещё раз или обратитесь к администратору.*\n"
                        "━━━━━━━━━━━━━━━━━━━━"
                    )
                    current_step[user_id] = 'menu'
            else:
                await event.respond(
                    "**⚠️ ОШИБКА**\n\n"
                    "PIN-код должен состоять из 4 цифр.\n"
                    "🔁 Попробуйте ещё раз."
                )
            return
        
        # Если пользователь не авторизован
        if not authorized_users.get(user_id):
            current_step[user_id] = 'wait_pin'
            await event.respond(
                "**🔐 ТРЕБУЕТСЯ АВТОРИЗАЦИЯ**\n\n"
                "Введите PIN-код для доступа."
            )
            return
        
        # ==========================================
        # 🎮 ОСНОВНЫЕ КОМАНДЫ (INLINE КНОПКИ)
        # ==========================================
        
        # Запустить рассылку
        if text == "🚀 Запустить рассылку":
            user_accounts = get_user_accounts(user_id)
            if not user_accounts:
                await event.respond(
                    "**⚠️ НЕТ АККАУНТОВ**\n\n"
                    "Сначала добавьте аккаунты Telegram.\n\n"
                    " Нажмите 'Аккаунты' для добавления.",
                    buttons=get_accounts_kb()
                )
                return
            
            if user_id not in current_materials:
                await event.respond(
                    "**⚠️ НЕТ МАТЕРИАЛА**\n\n"
                    "Загрузите материал для рассылки.\n\n"
                    "📦 Нажмите 'Материал' для загрузки.",
                    buttons=get_material_kb()
                )
                return
            
            # Считаем контакты
            total = 0
            for acc in user_accounts.values():
                try:
                    contacts = await acc['client'](GetContactsRequest(0))
                    total += len([u for u in contacts.users if u.mutual_contact and not u.bot])
                except:
                    pass
            
            await event.respond(
                f"**🚀 ЗАПУСК РАССЫЛКИ**\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"**📊 Параметры:**\n"
                f"• 👥 Аккаунтов: {len(user_accounts)}\n"
                f"• 📞 Контактов: {total}\n"
                f"• 📦 Материал: {current_materials[user_id].get('name', 'Без названия')}\n\n"
                "**⚡️ Готовы к запуску?**\n"
                "━━━━━━━━━━━━━━━━━━━━",
                buttons=get_broadcast_confirm_kb()
            )
        
        # Аккаунты
        elif text == "👥 Аккаунты":
            user_accounts = get_user_accounts(user_id)
            count = len(user_accounts)
            
            acc_list = ""
            if user_accounts:
                for i, (acc_id, acc_data) in enumerate(user_accounts.items(), 1):
                    name = acc_data.get('name', 'Unknown')
                    phone = acc_data.get('phone', 'Unknown')
                    acc_list += f"{i}. **{name}** (`{phone}`)\n"
            else:
                acc_list = "*Аккаунты не добавлены*"
            
            await event.respond(
                f"**👥 УПРАВЛЕНИЕ АККАУНТАМИ**\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"**📊 Всего аккаунтов: {count}**\n\n"
                f"{acc_list}\n"
                "**🔧 Выберите действие:**\n"
                "━━━━━━━━━━━━━━━━━━━━",
                buttons=get_accounts_kb()
            )
        
        # Материал
        elif text == "📦 Материал":
            materials = material_history.get(user_id, [])
            count = len(materials)
            
            mat_info = ""
            if current_materials.get(user_id):
                mat = current_materials[user_id]
                mat_info = f"**📎 Текущий:** {mat.get('name', 'Без названия')}\n"
            else:
                mat_info = "*Материал не загружен*"
            
            await event.respond(
                f"**📦 УПРАВЛЕНИЕ МАТЕРИАЛАМИ**\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"**📊 Статистика:**\n"
                f"• Всего материалов: {count}\n"
                f"{mat_info}\n"
                "** Выберите действие:**\n"
                "━━━━━━━━━━━━━━━━━━━━",
                buttons=get_material_kb()
            )
        
        # Статистика
        elif text == "📈 Статистика":
            stats = get_stats(user_id)
            user_accounts = get_user_accounts(user_id)
            materials = len(material_history.get(user_id, []))
            
            await event.respond(
                f"**📈 СТАТИСТИКА**\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"**📊 Общая информация:**\n"
                f"• 👥 Аккаунтов: {len(user_accounts)}\n"
                f"• 📦 Материалов: {materials}\n"
                f"• 📤 Всего отправлено: {stats['total']}\n"
                f"• 🔄 Всего рассылок: {stats['broadcasts']}\n\n"
                f"**📅 За сегодня:** {stats['today']}\n"
                f"**📅 За месяц:** {stats['month']}\n"
                "━━━━━━━━━━━━━━━━━━━━",
                buttons=get_welcome_kb()
            )
        
        # Настройки
        elif text == "⚙️ Настройки":
            await event.respond(
                "**⚙️ НАСТРОЙКИ**\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "**🔧 Доступные опции:**\n"
                "• Изменить PIN-код\n"
                "• Настройки уведомлений\n"
                "• Экспорт данных\n\n"
                "*В разработке...*\n"
                "━━━━━━━━━━━━━━━━━━━━",
                buttons=[[Button.inline("🔙 Назад", b'main_menu')]]
            )
        
        # ==========================================
        # 🎮 ОБРАБОТКА INLINE КНОПОК
        # ==========================================
    
    @bot.on(events.CallbackQuery)
    async def callback_handler(event):
        user_id = event.sender_id
        
        if not authorized_users.get(user_id):
            await event.answer("🔐 Сначала введите PIN-код (отправьте /start)", alert=True)
            return
        
        data = event.data.decode('utf-8')
        
        # Главное меню
        if data == 'main_menu':
            current_step[user_id] = 'menu'
            await event.edit(
                "**🎮 DISCORD SPAM BOT**\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "**⚡️ Выберите действие:**\n"
                "━━━━━━━━━━━━━━━━━━━━",
                buttons=get_welcome_kb()
            )
        
        # Добавить аккаунт
        elif data == 'add_account':
            await event.edit(
                "**➕ ДОБАВИТЬ АККАУНТ**\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "**🔐 Выберите способ входа:**\n\n"
                "📱 **По номеру** — введите номер телефона\n"
                "💾 **Session файл** — загрузите .session\n"
                "🔑 **Session String** — введите строку\n"
                "━━━━━━━━━━━━━━━━━━━━",
                buttons=get_accounts_kb()
            )
        
        # Вход по номеру
        elif data == 'phone_login':
            current_step[user_id] = 'wait_phone'
            await event.edit(
                "**📱 ВХОД ПО НОМЕРУ**\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "**📝 Введите номер телефона:**\n\n"
                "Формат: `+79991234567`\n\n"
                "━━━━━━━━━━━━━━━━━━━━",
                buttons=get_cancel_kb()
            )
        
        # Session файл
        elif data == 'session_file':
            current_step[user_id] = 'wait_session_file'
            await event.edit(
                "**💾 ЗАГРУЗКА SESSION**\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "**📎 Отправьте файл .session**\n\n"
                "Перетащите файл в чат или выберите из галереи.\n"
                "━━━━━━━━━━━━━━━━━━━━",
                buttons=get_cancel_kb()
            )
        
        # Session String
        elif data == 'session_string':
            current_step[user_id] = 'wait_session_string'
            await event.edit(
                "**🔑 SESSION STRING**\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "**📝 Введите Session String:**\n\n"
                "Длинная строка (начинается на `1`)\n"
                "━━━━━━━━━━━━━━━━━━━━",
                buttons=get_cancel_kb()
            )
        
        # Загрузить материал
        elif data == 'upload_material':
            current_step[user_id] = 'wait_material'
            await event.edit(
                "**📥 ЗАГРУЗКА МАТЕРИАЛА**\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "**📎 Отправьте файл или текст:**\n\n"
                "📁 Файл: фото, видео, документы\n"
                "📝 Текст: просто напишите сообщение\n\n"
                "*Материал будет использован в рассылке*\n"
                "━━━━━━━━━━━━━━━━━━━━",
                buttons=get_cancel_kb()
            )
        
        # История материалов
        elif data == 'material_history':
            materials = material_history.get(user_id, [])
            if not materials:
                await event.answer("📭 История пуста", alert=True)
            else:
                text = "**📚 ИСТОРИЯ МАТЕРИАЛОВ**\n\n"
                for i, mat in enumerate(materials[-5:], 1):
                    name = mat.get('name', f'Материал {i}')
                    text += f"{i}. **{name}**\n"
                await event.edit(text, buttons=[[Button.inline("🔙 Назад", b'material')]])
        
        # Подтверждение рассылки
        elif data == 'confirm_broadcast':
            await event.edit("⏳ **Запускаю рассылку...**")
            await do_broadcast(bot, user_id, event)
        
        # Повторить рассылку
        elif data == 'repeat_broadcast':
            if user_id in current_materials:
                await event.edit("⏳ **Повторяю рассылку...**")
                await do_broadcast(bot, user_id, event)
            else:
                await event.answer("❌ Сначала загрузите материал", alert=True)
        
        # Новый материал
        elif data == 'new_material':
            current_step[user_id] = 'wait_material'
            await event.edit(
                "**📥 НОВЫЙ МАТЕРИАЛ**\n\n"
                "Отправьте файл или текст",
                buttons=get_cancel_kb()
            )
        
        # Отмена
        elif data == 'cancel':
            current_step[user_id] = 'menu'
            await event.edit(
                "**❌ ОТМЕНЕНО**\n\n"
                "Возврат в главное меню.",
                buttons=get_welcome_kb()
            )
        
        await event.answer()

    # ==========================================
    # 📨 ОБРАБОТКА ВВОДА (ТЕЛЕФОН, КОД, ФАЙЛЫ)
    # ==========================================
    @bot.on(events.NewMessage)
    async def input_handler(event):
        if event.sender_id == bot_id:
            return
        
        user_id = event.sender_id
        text = event.message.text
        step = current_step.get(user_id, 'menu')
        
        # Пропускаем команды и кнопки
        if text and (text.startswith('/') or text in [
            "🚀 Запустить рассылку", "👥 Аккаунты", "📦 Материал",
            "📈 Статистика", "⚙️ Настройки"
        ]):
            return
        
        # Ввод номера телефона
        if step == 'wait_phone':
            if text and text.startswith('+') and text[1:].isdigit():
                current_step[user_id] = 'wait_code'
                client = TelegramClient(f'acc_{user_id}_{len(get_user_accounts(user_id))}', API_ID, API_HASH)
                await client.connect()
                try:
                    await client.send_code_request(text)
                    if user_id not in accounts:
                        accounts[user_id] = {}
                    acc_id = f'acc_{len(accounts[user_id]) + 1}'
                    accounts[user_id][acc_id] = {'client': client, 'phone': text}
                    
                    await event.respond(
                        f"**📨 КОД ОТПРАВЛЕН**\n\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"Код отправлен на **{text}**\n\n"
                        "**📝 Введите код из Telegram:**\n"
                        "━━━━━━━━━━━━━━━━━━━━",
                        buttons=get_cancel_kb()
                    )
                except Exception as e:
                    await event.respond(f"❌ Ошибка: {e}")
                    current_step[user_id] = 'menu'
            return
        
        # Ввод кода
        if step == 'wait_code':
            if text and text.isdigit() and 4 <= len(text) <= 6:
                user_accounts = get_user_accounts(user_id)
                if not user_accounts:
                    return
                
                last_acc = list(user_accounts.values())[-1]
                client = last_acc.get('client')
                phone = last_acc.get('phone')
                
                try:
                    await client.sign_in(phone, text)
                    me = await client.get_me()
                    last_acc.update({'client': client, 'phone': me.phone, 'name': me.first_name})
                    
                    current_step[user_id] = 'menu'
                    await event.respond(
                        f"**✅ АККАУНТ ДОБАВЛЕН**\n\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"**👤 {me.first_name}**\n"
                        f"**📱 +{me.phone}**\n"
                        f"**✉️ @{me.username or 'нет username'}**\n"
                        "━━━━━━━━━━━━━━━━━━━━",
                        buttons=get_accounts_kb()
                    )
                except:
                    await event.respond("❌ **Неверный код**. Попробуйте ещё раз.")
            return
        
        # Загрузка Session файла
        if step == 'wait_session_file':
            if event.message.file and event.message.file.name.lower().endswith('.session'):
                await event.respond("⏳ **Загружаю session...**")
                try:
                    session_path = await event.message.download_media(
                        file=os.path.join(SESSIONS_DIR, f'acc_{user_id}_{len(get_user_accounts(user_id))}.session')
                    )
                    client = TelegramClient(session_path.replace('.session', ''), API_ID, API_HASH)
                    await client.connect()
                    me = await client.get_me()
                    
                    if user_id not in accounts:
                        accounts[user_id] = {}
                    acc_id = f'acc_{len(accounts[user_id]) + 1}'
                    accounts[user_id][acc_id] = {'client': client, 'phone': me.phone, 'name': me.first_name}
                    
                    current_step[user_id] = 'menu'
                    await event.respond(
                        f"**✅ SESSION ЗАГРУЖЕН**\n\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"**👤 {me.first_name}**\n"
                        f"**📱 +{me.phone}**\n"
                        "━━━━━━━━━━━━━━━━━━━━",
                        buttons=get_accounts_kb()
                    )
                except Exception as e:
                    await event.respond(f"❌ Ошибка: {e}")
            current_step[user_id] = 'menu'
            return
        
        # Ввод Session String
        if step == 'wait_session_string':
            if text and text.startswith('1') and len(text) > 100:
                try:
                    client = TelegramClient(StringSession(text), API_ID, API_HASH)
                    await client.connect()
                    me = await client.get_me()
                    
                    if user_id not in accounts:
                        accounts[user_id] = {}
                    acc_id = f'acc_{len(accounts[user_id]) + 1}'
                    accounts[user_id][acc_id] = {'client': client, 'phone': me.phone, 'name': me.first_name}
                    
                    current_step[user_id] = 'menu'
                    await event.respond(
                        f"**✅ STRING ПРИНЯТ**\n\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"**👤 {me.first_name}**\n"
                        f"**📱 +{me.phone}**\n"
                        "━━━━━━━━━━━━━━━━━━━━",
                        buttons=get_accounts_kb()
                    )
                except Exception as e:
                    await event.respond(f"❌ Ошибка: {e}")
            else:
                await event.respond("❌ **Неверный формат**. String должен начинаться на `1`.")
            current_step[user_id] = 'menu'
            return
        
        # Загрузка материала
        if step == 'wait_material':
            if event.message.file:
                original_name = event.message.file.name or 'file'
                save_name = f"mat_{user_id}_{original_name}"
                path = await event.message.download_media(
                    file=os.path.join(MATERIALS_DIR, save_name)
                )
                caption = event.message.message if event.message.message else ""
                save_material(user_id, path, caption)
                
                current_step[user_id] = 'menu'
                await event.respond(
                    f"**✅ ФАЙЛ СОХРАНЁН**\n\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"**📁 {original_name}**\n"
                    f"**📝 Текст:** {caption[:50] if caption else 'без текста'}\n"
                    "━━━━━━━━━━━━━━━━━━━━",
                    buttons=get_material_kb()
                )
            elif text:
                save_material(user_id, None, text)
                current_step[user_id] = 'menu'
                await event.respond(
                    f"**✅ ТЕКСТ СОХРАНЁН**\n\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"**📝 {text[:100]}{'...' if len(text) > 100 else ''}**\n"
                    "━━━━━━━━━━━━━━━━━━━━",
                    buttons=get_material_kb()
                )
            return

    # ==========================================
    # 🚀 ФУНКЦИЯ РАССЫЛКИ
    # ==========================================
    async def do_broadcast(bot, user_id, event):
        user_accounts = get_user_accounts(user_id)
        material = current_materials.get(user_id)
        
        if not user_accounts or not material:
            return
        
        all_targets = []
        for acc_id, acc_data in user_accounts.items():
            try:
                contacts = await acc_data['client'](GetContactsRequest(0))
                targets = [u for u in contacts.users if u.mutual_contact and not u.bot]
                all_targets.extend([(acc_id, acc_data, target) for target in targets])
            except Exception as e:
                await event.respond(f"❌ Ошибка {acc_id}: {e}")
        
        if not all_targets:
            await event.respond("⚠️ **Нет контактов для рассылки!**")
            return
        
        total = len(all_targets)
        sent = 0
        failed = 0
        last_progress = 0
        
        progress_msg = await event.respond(
            f"**🚀 РАССЫЛКА ЗАПУЩЕНА**\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"**📊 Прогресс:** 0/{total}\n"
            "━━━━━━━━━━━━━━━━━━━━"
        )
        
        for acc_id, acc_data, target in all_targets:
            try:
                if material['file']:
                    original_filename = os.path.basename(material['file'])
                    if original_filename.startswith(f'mat_{user_id}_'):
                        original_filename = original_filename[len(f'mat_{user_id}_'):]
                    
                    await acc_data['client'].send_file(
                        target.id,
                        material['file'],
                        caption=material['caption'],
                        attributes=[DocumentAttributeFilename(file_name=original_filename)]
                    )
                else:
                    await acc_data['client'].send_message(target.id, material['caption'])
                sent += 1
            except:
                failed += 1
            
            if sent % 10 == 0 and sent != last_progress:
                await progress_msg.edit(
                    f"**🚀 РАССЫЛКА**\n\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"**✅ Отправлено:** {sent}/{total}\n"
                    f"**❌ Ошибок:** {failed}\n"
                    "━━━━━━━━━━━━━━━━━━━━"
                )
                last_progress = sent
            
            await asyncio.sleep(2)
        
        clear_user_sessions(user_id)
        update_stats(user_id, sent)
        stats = get_stats(user_id)
        
        await event.respond(
            f"**✅ РАССЫЛКА ЗАВЕРШЕНА**\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"**📊 Результаты:**\n"
            f"• ✅ Успешно: {sent}\n"
            f"• ❌ Ошибок: {failed}\n"
            f"• 📊 Всего: {stats['total']}\n\n"
            f"**💾 Сессии удалены.**\n"
            "━━━━━━━━━━━━━━━━━━━━",
            buttons=get_after_broadcast_kb()
        )
        
        current_step[user_id] = 'after_broadcast'

    await bot.run_until_disconnected()

# ==========================================
# 🏁 ЗАПУСК
# ==========================================
async def run_all():
    await asyncio.gather(
        start_web_server(),
        main()
    )

if __name__ == '__main__':
    asyncio.run(run_all())
