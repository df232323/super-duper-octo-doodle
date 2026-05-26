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
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
CORRECT_PIN = "6611"

if not BOT_TOKEN or not API_ID or not API_HASH:
    print("❌ ОШИБКА: Нет переменных окружения!")
    exit(1)

# Хранилище состояния
accounts = {}
current_step = {}
current_materials = {}
material_history = {}
broadcast_stats = {}
authorized_users = {}

# ==========================================
# 🌐 ВЕБ-СЕРВЕР (для Render)
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
    print("🌐 Web server ready")

# ==========================================
# 🎨 КНОПКИ (КАК НА ФОТО 3)
# ==========================================
def main_kb():
    return [
        [Button.inline("🚀 Запустить рассылку", b'broadcast')],
        [Button.inline("👥 Аккаунты", b'accounts'), Button.inline("📦 Материал", b'material')],
        [Button.inline("📈 Статистика", b'stats')]
    ]

def accounts_kb():
    return [
        [Button.inline("📱 По номеру", b'phone')],
        [Button.inline(" Session файл", b'sess_file')],
        [Button.inline("🔑 Session String", b'sess_str')],
        [Button.inline(" Назад", b'main')]
    ]

def material_kb():
    return [
        [Button.inline("📥 Загрузить материал", b'upload_mat')],
        [Button.inline(" Назад", b'main')]
    ]

def confirm_kb():
    return [[Button.inline("✅ Да, запустить", b'confirm'), Button.inline("❌ Отмена", b'main')]]

def after_kb():
    return [
        [Button.inline("🔁 Повторить", b'repeat')],
        [Button.inline(" Новый материал", b'new_mat')],
        [Button.inline("➕ Добавить аккаунт", b'add_acc')],
        [Button.inline("🏠 Главное меню", b'main')]
    ]

def cancel_kb():
    return [[Button.inline("❌ Отмена", b'cancel')]]

# ==========================================
# 🔧 ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================
def get_stats(uid):
    if uid not in broadcast_stats: return {'total':0, 'broadcasts':0, 'today':0, 'month':0}
    s = broadcast_stats[uid]
    d, m = datetime.now().strftime('%Y-%m-%d'), datetime.now().strftime('%Y-%m')
    return {'total': s['total'], 'broadcasts': s['broadcasts'], 'today': s['daily'].get(d, 0), 'month': s['monthly'].get(m, 0)}

# ==========================================
# 🏁 ГЛАВНАЯ ЛОГИКА
# ==========================================
async def main():
    bot = TelegramClient('bot_session', API_ID, API_HASH)
    await bot.start(bot_token=BOT_TOKEN)
    me = await bot.get_me()
    print(f"✅ {me.first_name} запущен!")

    # 1️⃣ ОБРАБОТКА /start (ВСЕГДА СБРАСЫВАЕТ СОСТОЯНИЕ)
    @bot.on(events.NewMessage(pattern=r'/start'))
    async def start_cmd(event):
        uid = event.sender_id
        current_step[uid] = 'wait_pin'  # Принудительно ждём PIN
        await event.respond(
            "**🔐 DUCK SPAM BOT**\n\n"
            "Для доступа введите PIN-код.\n"
            "*Введите 4 цифры для продолжения...*\n\n"
            "*Не знаете код? Обратитесь к админу.*",
            buttons=None
        )

    # 2️⃣ ОБРАБОТКА ОБЫЧНЫХ СООБЩЕНИЙ
    @bot.on(events.NewMessage)
    async def handle_text(event):
        uid = event.sender_id
        text = event.message.text.strip()
        step = current_step.get(uid, 'menu')

        # Игнорируем команды и пустые сообщения
        if text.startswith('/') or not text:
            return

        # 🔐 ПРОВЕРКА PIN
        if step == 'wait_pin':
            if text.isdigit() and len(text) == 4:
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
                        buttons=main_kb()
                    )
                else:
                    await event.respond("❌ **Неверный PIN**. Попробуйте снова.")
            return

        # Если не авторизован → блокируем
        if not authorized_users.get(uid):
            current_step[uid] = 'wait_pin'
            await event.respond("🔐 Введите PIN-код.", buttons=None)
            return

        # 📥 ОБРАБОТКА ШАГОВ
        if step == 'wait_phone' and text.startswith('+') and text[1:].isdigit():
            current_step[uid] = 'wait_code'
            client = TelegramClient(f'acc_{uid}_{len(accounts.get(uid,{}))}', API_ID, API_HASH)
            await client.connect()
            await client.send_code_request(text)
            if uid not in accounts: accounts[uid] = {}
            accounts[uid][f'a{len(accounts[uid])+1}'] = {'client': client, 'phone': text}
            await event.respond(f"📨 Код отправлен на `{text}`\nВведите его:", buttons=cancel_kb())
            return

        if step == 'wait_code' and text.isdigit() and 4<=len(text)<=6:
            accs = accounts.get(uid, {})
            if not accs: return
            last = list(accs.values())[-1]
            try:
                await last['client'].sign_in(last['phone'], text)
                me_acc = await last['client'].get_me()
                last.update({'client': last['client'], 'phone': me_acc.phone, 'name': me_acc.first_name})
                current_step[uid] = 'menu'
                await event.respond(f"✅ **{me_acc.first_name}** добавлен!\n📱 +{me_acc.phone}", buttons=accounts_kb())
            except: await event.respond("❌ Неверный код.")
            return

        if step == 'wait_sess_file' and event.message.file and event.message.file.name.endswith('.session'):
            path = await event.message.download_media(file=f"sessions/acc_{uid}.session")
            client = TelegramClient(path.replace('.session',''), API_ID, API_HASH)
            await client.connect()
            me_acc = await client.get_me()
            if uid not in accounts: accounts[uid] = {}
            accounts[uid][f'a{len(accounts[uid])+1}'] = {'client': client, 'phone': me_acc.phone, 'name': me_acc.first_name}
            current_step[uid] = 'menu'
            await event.respond(f"✅ Session загружен: **{me_acc.first_name}**", buttons=accounts_kb())
            return

        if step == 'wait_sess_str' and text.startswith('1') and len(text)>100:
            client = TelegramClient(StringSession(text), API_ID, API_HASH)
            await client.connect()
            me_acc = await client.get_me()
            if uid not in accounts: accounts[uid] = {}
            accounts[uid][f'a{len(accounts[uid])+1}'] = {'client': client, 'phone': me_acc.phone, 'name': me_acc.first_name}
            current_step[uid] = 'menu'
            await event.respond(f"✅ String принят: **{me_acc.first_name}**", buttons=accounts_kb())
            return

        if step == 'wait_material':
            if event.message.file:
                name = event.message.file.name or 'file'
                path = await event.message.download_media(file=f"materials/mat_{uid}_{name}")
                cap = event.message.message or ''
                if uid not in material_history: material_history[uid] = []
                material_history[uid].append({'file': path, 'caption': cap, 'name': name})
                current_materials[uid] = material_history[uid][-1]
                current_step[uid] = 'menu'
                await event.respond(f"✅ **{name}** сохранён!", buttons=material_kb())
            elif text:
                if uid not in material_history: material_history[uid] = []
                material_history[uid].append({'file': None, 'caption': text, 'name': 'Text'})
                current_materials[uid] = material_history[uid][-1]
                current_step[uid] = 'menu'
                await event.respond("✅ Текст сохранён!", buttons=material_kb())
            return

    # 3️⃣ ОБРАБОТКА INLINE КНОПОК
    @bot.on(events.CallbackQuery)
    async def cb_handler(event):
        uid = event.sender_id
        if not authorized_users.get(uid): return await event.answer("🔐 Введите PIN через /start", alert=True)
        
        data = event.data.decode()
        
        if data == 'main':
            current_step[uid] = 'menu'
            await event.edit("**🦆 DUCK SPAM BOT**\n\n*Добро пожаловать!*\n\n**Выберите действие:**", buttons=main_kb())
        elif data == 'accounts':
            await event.edit("**👥 АККАУНТЫ**\nВсего: {}".format(len(accounts.get(uid, {}))), buttons=accounts_kb())
        elif data == 'material':
            await event.edit("**📦 МАТЕРИАЛ**\nЗагрузите файл или текст.", buttons=material_kb())
        elif data == 'stats':
            s = get_stats(uid)
            await event.edit(f"** СТАТ**\nВсего: {s['total']}\nСегодня: {s['today']}", buttons=main_kb())
        elif data == 'phone':
            current_step[uid] = 'wait_phone'
            await event.edit("** ВХОД ПО НОМЕРУ**\nВведите номер (+7...)", buttons=cancel_kb())
        elif data == 'sess_file':
            current_step[uid] = 'wait_sess_file'
            await event.edit("**💾 SESSION ФАЙЛ**\nОтправьте .session файл", buttons=cancel_kb())
        elif data == 'sess_str':
            current_step[uid] = 'wait_sess_str'
            await event.edit("**🔑 SESSION STRING**\nВведите строку (начинается на 1)", buttons=cancel_kb())
        elif data == 'upload_mat':
            current_step[uid] = 'wait_material'
            await event.edit("**📥 ЗАГРУЗКА**\nОтправьте файл или текст", buttons=cancel_kb())
        elif data == 'confirm':
            await event.edit("⏳ Запускаю рассылку...")
            # Тут можно вызвать функцию рассылки
        elif data == 'cancel':
            current_step[uid] = 'menu'
            await event.edit("**❌ Отменено**", buttons=main_kb())
        elif data == 'repeat':
            await event.edit("🔁 Повторяю...", buttons=after_kb())
        elif data == 'new_mat':
            current_step[uid] = 'wait_material'
            await event.edit("📥 Отправьте новый материал", buttons=cancel_kb())
        elif data == 'add_acc':
            await event.edit("➕ Добавьте аккаунт", buttons=accounts_kb())
            
        await event.answer()

    await bot.run_until_disconnected()

# ==========================================
# 🏁 ЗАПУСК
# ==========================================
async def run_all():
    await asyncio.gather(start_web_server(), main())

if __name__ == '__main__':
    asyncio.run(run_all())
