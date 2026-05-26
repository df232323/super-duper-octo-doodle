import asyncio
import os
from datetime import datetime
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.functions.contacts import GetContactsRequest
from telethon.tl.types import DocumentAttributeFilename
from telethon.errors import PhoneNumberInvalidError, PhoneCodeInvalidError
from telethon.tl.custom import Button

# ==========================================
# 🔑 НАСТРОЙКИ
# ==========================================
BOT_TOKEN = os.getenv('BOT_TOKEN')
API_ID = os.getenv('API_ID')
API_HASH = os.getenv('API_HASH')

if not BOT_TOKEN or not API_ID or not API_HASH:
    print("❌ ОШИБКА: Не установлены переменные окружения!")
    print("Добавь в Render Environment Variables:")
    print("  BOT_TOKEN, API_ID, API_HASH")
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

# ==========================================
# 🎨 КНОПКИ
# ==========================================
def get_main_kb():
    return [
        [Button.text('🚀 ЗАПУСТИТЬ РАССЫЛКУ')],
        [Button.text('👥 АККАУНТЫ'), Button.text('📦 МАТЕРИАЛ')],
        [Button.text('📈 СТАТИСТИКА')]
    ]

def get_login_kb():
    return [
        [Button.text('➕ ДОБАВИТЬ АККАУНТ')],
        [Button.text('🔙 НАЗАД')]
    ]

def get_material_kb():
    return [
        [Button.text('📥 ЗАГРУЗИТЬ МАТЕРИАЛ')],
        [Button.text('🔙 НАЗАД')]
    ]

def get_after_broadcast_kb():
    return [
        [Button.text('🔁 ПОВТОРИТЬ')],
        [Button.text('📥 НОВЫЙ МАТЕРИАЛ')],
        [Button.text('➕ ДОБАВИТЬ АККАУНТ')],
        [Button.text('🏠 ГЛАВНОЕ МЕНЮ')]
    ]

CANCEL_KB = [[Button.text('❌ ОТМЕНА')]]

# ==========================================
# 🔧 ФУНКЦИИ СТАТИСТИКИ
# ==========================================
def get_today_key():
    return datetime.now().strftime('%Y-%m-%d')

def get_month_key():
    return datetime.now().strftime('%Y-%m')

def update_stats(user_id, sent_count):
    if user_id not in broadcast_stats:
        broadcast_stats[user_id] = {
            'total': 0,
            'broadcasts': 0,
            'daily': {},
            'monthly': {}
        }
    
    today = get_today_key()
    month = get_month_key()
    
    broadcast_stats[user_id]['total'] += sent_count
    broadcast_stats[user_id]['broadcasts'] += 1
    
    if today not in broadcast_stats[user_id]['daily']:
        broadcast_stats[user_id]['daily'][today] = 0
    broadcast_stats[user_id]['daily'][today] += sent_count
    
    if month not in broadcast_stats[user_id]['monthly']:
        broadcast_stats[user_id]['monthly'][month] = 0
    broadcast_stats[user_id]['monthly'][month] += sent_count

def get_stats(user_id):
    if user_id not in broadcast_stats:
        return {'total': 0, 'broadcasts': 0, 'today': 0, 'month': 0}
    
    stats = broadcast_stats[user_id]
    today = get_today_key()
    month = get_month_key()
    
    return {
        'total': stats['total'],
        'broadcasts': stats['broadcasts'],
        'today': stats['daily'].get(today, 0),
        'month': stats['monthly'].get(month, 0)
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
        for acc_data in accounts[user_id].values():
            try:
                client = acc_data.get('client')
                if client:
                    asyncio.create_task(client.disconnect())
            except:
                pass
        accounts[user_id] = {}

# ==========================================
# 🏁 ГЛАВНАЯ ФУНКЦИЯ
# ==========================================
async def main():
    bot = TelegramClient('bot_session', API_ID, API_HASH)
    await bot.start(bot_token=BOT_TOKEN)
    bot_me = await bot.get_me()
    bot_id = bot_me.id
    
    print("🟢 DUCK BOT запущен!")
    print(f"👤 Бот: @{bot_me.username or 'нет username'}")
    
    # ==========================================
    # 📨 ОБРАБОТЧИКИ СОБЫТИЙ
    # ==========================================
    
    @bot.on(events.NewMessage(pattern='/start'))
    async def start_handler(event):
        if event.sender_id == bot_id:
            return
        user_id = event.sender_id
        current_step[user_id] = 'menu'
        await event.respond(
            "🤖 **ДОБРО ПОЖАЛОВАТЬ В DUCK** 🤖\n\n"
            "🔐 **СПАМИК БОТ**\n\n"
            "💎 Многопользовательский бот для рассылки\n\n"
            "📋 **Выберите действие:**\n\n"
            "⬇️️",
            buttons=get_main_kb()
        )

    @bot.on(events.NewMessage(pattern='🚀 ЗАПУСТИТЬ РАССЫЛКУ'))
    async def start_broadcast(event):
        if event.sender_id == bot_id:
            return
        user_id = event.sender_id
        
        user_accounts = get_user_accounts(user_id)
        if not user_accounts:
            await event.respond("❌ **Нет аккаунтов!**\n\nДобавьте аккаунты через '👥 АККАУНТЫ'")
            return
        
        if user_id not in current_materials:
            await event.respond("❌ **Нет материала!**\n\nЗагрузите материал через '📦 МАТЕРИАЛ'")
            return
        
        total_contacts = 0
        for acc_data in user_accounts.values():
            try:
                contacts = await acc_data['client'](GetContactsRequest(0))
                mutual = len([u for u in contacts.users if u.mutual_contact and not u.bot])
                total_contacts += mutual
            except:
                pass
        
        material = current_materials[user_id]
        await event.respond(
            f"🚀 **ЗАПУСК РАССЫЛКИ**\n\n"
            f"👥 Аккаунтов: {len(user_accounts)}\n"
            f"👥 Контактов: {total_contacts}\n"
            f"📦 Материал: {material.get('name', 'Без названия')}\n\n"
            f"▶️ **Начинаю?**",
            buttons=[
                [Button.text('✅ ДА, ЗАПУСТИТЬ')],
                [Button.text('❌ ОТМЕНА')]
            ]
        )
        current_step[user_id] = 'confirm_broadcast'

    @bot.on(events.NewMessage(pattern='✅ ДА, ЗАПУСТИТЬ'))
    async def confirm_broadcast(event):
        if event.sender_id == bot_id:
            return
        user_id = event.sender_id
        
        if current_step.get(user_id) != 'confirm_broadcast':
            return
        
        await event.respond("⏳ **Запускаю рассылку...**")
        await do_broadcast(bot, user_id, event)

    @bot.on(events.NewMessage(pattern='👥 АККАУНТЫ'))
    async def accounts_menu(event):
        if event.sender_id == bot_id:
            return
        user_id = event.sender_id
        current_step[user_id] = 'login_choice'
        
        await event.respond(
            "🔐 **ВЫБЕРИТЕ СПОСОБ ВХОДА**\n\n"
            "📱 **По номеру** — введите номер и код\n"
            "💾 **Session файл** — загрузите .session\n"
            "🔑 **Session String** — строка авторизации",
            buttons=[
                [Button.text('📱 ПО НОМЕРУ')],
                [Button.text('💾 SESSION ФАЙЛ')],
                [Button.text('🔑 SESSION STRING')],
                [Button.text('🔙 НАЗАД')]
            ]
        )

    @bot.on(events.NewMessage(pattern='➕ ДОБАВИТЬ АККАУНТ'))
    async def add_account(event):
        if event.sender_id == bot_id:
            return
        user_id = event.sender_id
        current_step[user_id] = 'login_choice'
        
        await event.respond(
            "➕ **ДОБАВИТЬ АККАУНТ**\n\nВыберите способ:",
            buttons=[
                [Button.text('📱 ПО НОМЕРУ')],
                [Button.text('💾 SESSION ФАЙЛ')],
                [Button.text('🔑 SESSION STRING')],
                [Button.text('🔙 НАЗАД')]
            ]
        )

    @bot.on(events.NewMessage(pattern='📱 ПО НОМЕРУ'))
    async def phone_login(event):
        if event.sender_id == bot_id:
            return
        user_id = event.sender_id
        current_step[user_id] = 'wait_phone'
        await event.respond(
            "📱 **ВВЕДИТЕ НОМЕР**\n\nФормат: +79991234567",
            buttons=CANCEL_KB
        )

    @bot.on(events.NewMessage(pattern='💾 SESSION ФАЙЛ'))
    async def session_file_login(event):
        if event.sender_id == bot_id:
            return
        user_id = event.sender_id
        current_step[user_id] = 'wait_session_file'
        await event.respond(
            "💾 **ЗАГРУЗИТЕ SESSION ФАЙЛ**\n\nОтправьте файл .session",
            buttons=CANCEL_KB
        )

    @bot.on(events.NewMessage(pattern='🔑 SESSION STRING'))
    async def session_string_login(event):
        if event.sender_id == bot_id:
            return
        user_id = event.sender_id
        current_step[user_id] = 'wait_session_string'
        await event.respond(
            "🔑 **ВВЕДИТЕ SESSION STRING**\n\nДлинная строка (начинается на 1)",
            buttons=CANCEL_KB
        )

    @bot.on(events.NewMessage(pattern='📦 МАТЕРИАЛ'))
    async def material_menu(event):
        if event.sender_id == bot_id:
            return
        user_id = event.sender_id
        current_step[user_id] = 'wait_material'
        
        await event.respond(
            "📦 **УПРАВЛЕНИЕ МАТЕРИАЛАМИ**",
            buttons=get_material_kb()
        )

    @bot.on(events.NewMessage(pattern='📥 ЗАГРУЗИТЬ МАТЕРИАЛ'))
    async def upload_material(event):
        if event.sender_id == bot_id:
            return
        user_id = event.sender_id
        current_step[user_id] = 'wait_material'
        
        await event.respond(
            "📥 **ОТПРАВЬТЕ МАТЕРИАЛ**\n\n"
            "📎 Отправьте файл ИЛИ текст\n\n"
            "Это будет содержимое рассылки",
            buttons=CANCEL_KB
        )

    @bot.on(events.NewMessage(pattern='📈 СТАТИСТИКА'))
    async def show_stats(event):
        if event.sender_id == bot_id:
            return
        user_id = event.sender_id
        
        stats = get_stats(user_id)
        user_accounts = get_user_accounts(user_id)
        materials_count = len(material_history.get(user_id, []))
        
        await event.respond(
            f"📈 **СТАТИСТИКА**\n\n"
            f"👥 Аккаунтов: {len(user_accounts)}\n"
            f"📦 Материалов: {materials_count}\n"
            f"✉️ Всего отправлено: {stats['total']}\n"
            f"🔄 Рассылок: {stats['broadcasts']}\n\n"
            f"📅 За сегодня: {stats['today']}\n"
            f"📅 За месяц: {stats['month']}",
            buttons=get_main_kb()
        )

    @bot.on(events.NewMessage(pattern='🔙 НАЗАД'))
    async def go_back(event):
        if event.sender_id == bot_id:
            return
        user_id = event.sender_id
        current_step[user_id] = 'menu'
        await event.respond("🔙 Возврат в главное меню", buttons=get_main_kb())

    @bot.on(events.NewMessage(pattern='🏠 ГЛАВНОЕ МЕНЮ'))
    async def to_main(event):
        if event.sender_id == bot_id:
            return
        user_id = event.sender_id
        current_step[user_id] = 'menu'
        await event.respond("🏠 Главное меню", buttons=get_main_kb())

    @bot.on(events.NewMessage(pattern='❌ ОТМЕНА'))
    async def cancel(event):
        if event.sender_id == bot_id:
            return
        user_id = event.sender_id
        current_step[user_id] = 'menu'
        await event.respond("❌ Отменено", buttons=get_main_kb())

    @bot.on(events.NewMessage)
    async def handler(event):
        if event.sender_id == bot_id:
            return
            
        user_id = event.sender_id
        text = event.message.text
        step = current_step.get(user_id, 'menu')
        
        button_texts = [
            '🚀 ЗАПУСТИТЬ РАССЫЛКУ', '👥 АККАУНТЫ', '📦 МАТЕРИАЛ',
            '📈 СТАТИСТИКА', '➕ ДОБАВИТЬ АККАУНТ',
            '📥 ЗАГРУЗИТЬ МАТЕРИАЛ', '🔁 ПОВТОРИТЬ', '📥 НОВЫЙ МАТЕРИАЛ',
            '📱 ПО НОМЕРУ', '💾 SESSION ФАЙЛ', '🔑 SESSION STRING',
            '✅ ДА, ЗАПУСТИТЬ', '❌ ОТМЕНА', '🔙 НАЗАД', '🏠 ГЛАВНОЕ МЕНЮ'
        ]
        
        if text and (text.startswith('/') or text in button_texts):
            return
        
        # Вход по номеру
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
                        f"📨 **Код отправлен на {text}**\n\nВведите код из Telegram:",
                        buttons=CANCEL_KB
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
                        f"✅ **АККАУНТ ДОБАВЛЕН!**\n\n"
                        f"👤 {me.first_name}\n"
                        f"📱 +{me.phone}",
                        buttons=get_main_kb()
                    )
                except:
                    await event.respond("❌ **Неверный код**\n\nПопробуйте снова")
            return
        
        # Session файл
        if step == 'wait_session_file':
            if event.message.file and event.message.file.name.lower().endswith('.session'):
                await event.respond("⏳ Загружаю session...")
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
                        f"✅ **SESSION ЗАГРУЖЕН!**\n\n"
                        f"👤 {me.first_name}\n"
                        f"📱 +{me.phone}",
                        buttons=get_main_kb()
                    )
                except Exception as e:
                    await event.respond(f"❌ Ошибка: {e}")
            current_step[user_id] = 'menu'
            return
        
        # Session String
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
                        f"✅ **STRING ПРИНЯТ!**\n\n"
                        f"👤 {me.first_name}\n"
                        f"📱 +{me.phone}",
                        buttons=get_main_kb()
                    )
                except Exception as e:
                    await event.respond(f"❌ Ошибка: {e}")
            else:
                await event.respond("❌ **Неверный формат**\n\nString должен начинаться на 1")
            current_step[user_id] = 'menu'
            return
        
        # Материал
        if step == 'wait_material':
            if event.message.file:
                original_name = event.message.file.name
                if not original_name:
                    original_name = "file"
                
                save_name = f"mat_{user_id}_{original_name}"
                path = await event.message.download_media(
                    file=os.path.join(MATERIALS_DIR, save_name)
                )
                
                caption = event.message.message if event.message.message else ""
                save_material(user_id, path, caption)
                
                current_step[user_id] = 'menu'
                await event.respond(
                    f"✅ **ФАЙЛ СОХРАНЕН!**\n\n"
                    f"📁 {original_name}\n"
                    f"📝 {caption[:30] if caption else 'Без текста'}",
                    buttons=get_main_kb()
                )
            elif text:
                save_material(user_id, None, text)
                current_step[user_id] = 'menu'
                await event.respond(
                    f"✅ **ТЕКСТ СОХРАНЕН!**\n\n"
                    f"📝 {text[:50]}",
                    buttons=get_main_kb()
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
            await event.respond("⚠️ **Нет контактов!**")
            return
        
        total = len(all_targets)
        sent = 0
        failed = 0
        last_progress = 0
        
        progress_msg = await event.respond(
            f"🚀 **РАССЫЛКА**\n\n"
            f"📊 Всего: {total}\n\n"
            f"⏳ Прогресс: 0/{total}"
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
                    f"🚀 **РАССЫЛКА**\n\n"
                    f"✅ Отправлено: {sent}/{total}\n"
                    f"❌ Ошибок: {failed}"
                )
                last_progress = sent
            
            await asyncio.sleep(2)
        
        clear_user_sessions(user_id)
        update_stats(user_id, sent)
        stats = get_stats(user_id)
        
        await event.respond(
            f"✅ **ГОТОВО!**\n\n"
            f"✅ Отправлено: {sent}\n"
            f"❌ Ошибок: {failed}\n"
            f"📊 Всего: {stats['total']}\n\n"
            f"💾 Сессии удалены.\n\n"
            f"Что дальше?",
            buttons=get_after_broadcast_kb()
        )
        
        current_step[user_id] = 'after_broadcast'

    await bot.run_until_disconnected()

# ==========================================
# 🏁 ЗАПУСК БОТА
# ==========================================
if __name__ == '__main__':
    print("🦆 Запуск DUCK BOT...")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    finally:
        loop.close()
