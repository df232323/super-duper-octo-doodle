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
# 🔧 ФУНКЦИИ
# ==========================================
def get_today_key():
    return datetime.now().strftime('%Y-%m-%d')

def get_month_key():
    return datetime.now().strftime('%Y-%m')

def update_stats(user_id, sent_count):
    if user_id not in broadcast_stats:
        broadcast_stats[user_id] = {'total': 0, 'broadcasts': 0, 'daily': {}, 'monthly': {}}
    today = get_today_key()
    month = get_month_key()
    broadcast_stats[user_id]['total'] += sent_count
    broadcast_stats[user_id]['broadcasts'] += 1
    broadcast_stats[user_id]['daily'][today] = broadcast_stats[user_id]['daily'].get(today, 0) + sent_count
    broadcast_stats[user_id]['monthly'][month] = broadcast_stats[user_id]['monthly'].get(month, 0) + sent_count

def get_stats(user_id):
    if user_id not in broadcast_stats:
        return {'total': 0, 'broadcasts': 0, 'today': 0, 'month': 0}
    stats = broadcast_stats[user_id]
    return {
        'total': stats['total'],
        'broadcasts': stats['broadcasts'],
        'today': stats['daily'].get(get_today_key(), 0),
        'month': stats['monthly'].get(get_month_key(), 0)
    }

def get_user_accounts(user_id):
    return accounts.get(user_id, {})

def save_material(user_id, file_path, caption, name=None):
    if user_id not in material_history:
        material_history[user_id] = []
    material = {'file': file_path, 'caption': caption, 'name': name or f"Материал {len(material_history[user_id]) + 1}"}
    material_history[user_id].append(material)
    current_materials[user_id] = material
    return material

def clear_user_sessions(user_id):
    if user_id in accounts:
        for acc_data in accounts[user_id].values():
            try:
                if acc_data.get('client'):
                    asyncio.create_task(acc_data['client'].disconnect())
            except: pass
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
    
    # Простой веб-сервер для Render (чтобы не убивал бота)
    app = web.Application()
    
    async def handle(request):
        return web.Response(text="🦆 DUCK BOT is alive!")
    
    app.router.add_get('/', handle)
    app.router.add_get('/health', handle)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    print("🌐 Web server running on port 8080")
    
    @bot.on(events.NewMessage(pattern='/start'))
    async def start_handler(event):
        if event.sender_id == bot_id: return
        user_id = event.sender_id
        current_step[user_id] = 'menu'
        await event.respond("🤖 **ДОБРО ПОЖАЛОВАТЬ В DUCK** 🤖\n\n🔐 **СПАМИК БОТ**\n\n💎 Многопользовательский бот для рассылки\n\n📋 **Выберите действие:**\n\n⬇️️", buttons=get_main_kb())

    @bot.on(events.NewMessage(pattern='🚀 ЗАПУСТИТЬ РАССЫЛКУ'))
    async def start_broadcast(event):
        if event.sender_id == bot_id: return
        user_id = event.sender_id
        if not get_user_accounts(user_id):
            await event.respond("❌ **Нет аккаунтов!**"); return
        if user_id not in current_materials:
            await event.respond("❌ **Нет материала!**"); return
        
        total = 0
        for acc in get_user_accounts(user_id).values():
            try:
                c = await acc['client'](GetContactsRequest(0))
                total += len([u for u in c.users if u.mutual_contact and not u.bot])
            except: pass
        
        await event.respond(f"🚀 **ЗАПУСК**\n👥 Аккаунтов: {len(get_user_accounts(user_id))}\n👥 Контактов: {total}\n\n▶️ Начать?", buttons=[[Button.text('✅ ДА')],[Button.text('❌ НЕТ')]])
        current_step[user_id] = 'confirm'

    @bot.on(events.NewMessage(pattern='✅ ДА'))
    async def confirm(event):
        if event.sender_id == bot_id: return
        uid = event.sender_id
        if current_step.get(uid) != 'confirm': return
        await event.respond("⏳ Запускаю...")
        await do_broadcast(bot, uid, event)

    @bot.on(events.NewMessage(pattern='👥 АККАУНТЫ'))
    async def acc_menu(event):
        if event.sender_id == bot_id: return
        current_step[event.sender_id] = 'login'
        await event.respond("🔐 **Вход**\n📱 Номер | 💾 Session | 🔑 String", buttons=[[Button.text('📱 НОМЕР')],[Button.text('💾 SESSION')],[Button.text('🔑 STRING')],[Button.text('🔙 НАЗАД')]])

    @bot.on(events.NewMessage(pattern='➕ ДОБАВИТЬ АККАУНТ'))
    async def add_acc(event):
        if event.sender_id == bot_id: return
        current_step[event.sender_id] = 'login'
        await event.respond("➕ **Добавить**", buttons=[[Button.text('📱 НОМЕР')],[Button.text('💾 SESSION')],[Button.text('🔑 STRING')],[Button.text('🔙 НАЗАД')]])

    @bot.on(events.NewMessage(pattern='📱 НОМЕР'))
    async def phone(event):
        if event.sender_id == bot_id: return
        current_step[event.sender_id] = 'wait_phone'
        await event.respond("📱 Введите номер (+7...)", buttons=CANCEL_KB)

    @bot.on(events.NewMessage(pattern='💾 SESSION'))
    async def sess_file(event):
        if event.sender_id == bot_id: return
        current_step[event.sender_id] = 'wait_sess'
        await event.respond("💾 Отправьте .session файл", buttons=CANCEL_KB)

    @bot.on(events.NewMessage(pattern='🔑 STRING'))
    async def sess_str(event):
        if event.sender_id == bot_id: return
        current_step[event.sender_id] = 'wait_str'
        await event.respond("🔑 Введите Session String", buttons=CANCEL_KB)

    @bot.on(events.NewMessage(pattern='📦 МАТЕРИАЛ'))
    async def mat_menu(event):
        if event.sender_id == bot_id: return
        current_step[event.sender_id] = 'wait_mat'
        await event.respond("📦 **Материалы**", buttons=get_material_kb())

    @bot.on(events.NewMessage(pattern='📥 ЗАГРУЗИТЬ МАТЕРИАЛ'))
    async def upload_mat(event):
        if event.sender_id == bot_id: return
        current_step[event.sender_id] = 'wait_mat'
        await event.respond("📥 Отправьте файл или текст", buttons=CANCEL_KB)

    @bot.on(events.NewMessage(pattern='📈 СТАТИСТИКА'))
    async def stats(event):
        if event.sender_id == bot_id: return
        uid = event.sender_id
        s = get_stats(uid)
        await event.respond(f"📈 **СТАТ**\n👥 Аккаунтов: {len(get_user_accounts(uid))}\n✉️ Всего: {s['total']}\n📅 Сегодня: {s['today']}", buttons=get_main_kb())

    @bot.on(events.NewMessage(pattern='🔙 НАЗАД'))
    async def back(event):
        if event.sender_id == bot_id: return
        current_step[event.sender_id] = 'menu'
        await event.respond("🔙 Назад", buttons=get_main_kb())

    @bot.on(events.NewMessage(pattern='🏠 ГЛАВНОЕ МЕНЮ'))
    async def home(event):
        if event.sender_id == bot_id: return
        current_step[event.sender_id] = 'menu'
        await event.respond("🏠 Меню", buttons=get_main_kb())

    @bot.on(events.NewMessage(pattern='❌ ОТМЕНА'))
    async def cancel(event):
        if event.sender_id == bot_id: return
        current_step[event.sender_id] = 'menu'
        await event.respond("❌ Отмена", buttons=get_main_kb())

    @bot.on(events.NewMessage)
    async def handler(event):
        if event.sender_id == bot_id: return
        uid = event.sender_id
        txt = event.message.text
        step = current_step.get(uid, 'menu')
        btns = ['🚀 ЗАПУСТИТЬ РАССЫЛКУ','👥 АККАУНТЫ','📦 МАТЕРИАЛ','📈 СТАТИСТИКА','➕ ДОБАВИТЬ АККАУНТ','📥 ЗАГРУЗИТЬ МАТЕРИАЛ','🔁 ПОВТОРИТЬ','📥 НОВЫЙ МАТЕРИАЛ','📱 НОМЕР','💾 SESSION',' STRING','✅ ДА','❌ НЕТ','❌ ОТМЕНА','🔙 НАЗАД','🏠 ГЛАВНОЕ МЕНЮ']
        if txt and (txt.startswith('/') or txt in btns): return
        
        if step == 'wait_phone' and txt and txt.startswith('+') and txt[1:].isdigit():
            current_step[uid] = 'wait_code'
            client = TelegramClient(f'acc_{uid}_{len(get_user_accounts(uid))}', API_ID, API_HASH)
            await client.connect()
            try:
                await client.send_code_request(txt)
                if uid not in accounts: accounts[uid] = {}
                accounts[uid][f'acc_{len(accounts[uid])+1}'] = {'client': client, 'phone': txt}
                await event.respond(f"📨 Код на {txt}", buttons=CANCEL_KB)
            except Exception as e: await event.respond(f"❌ {e}"); current_step[uid] = 'menu'
            return
        
        if step == 'wait_code' and txt and txt.isdigit() and 4 <= len(txt) <= 6:
            accs = get_user_accounts(uid)
            if not accs: return
            last = list(accs.values())[-1]
            try:
                await last['client'].sign_in(last['phone'], txt)
                me = await last['client'].get_me()
                last.update({'client': last['client'], 'phone': me.phone, 'name': me.first_name})
                current_step[uid] = 'menu'
                await event.respond(f"✅ {me.first_name}\n📱 +{me.phone}", buttons=get_main_kb())
            except: await event.respond("❌ Неверный код")
            return
        
        if step == 'wait_sess' and event.message.file and event.message.file.name.lower().endswith('.session'):
            await event.respond("⏳ Загрузка...")
            try:
                path = await event.message.download_media(file=os.path.join(SESSIONS_DIR, f'acc_{uid}.session'))
                client = TelegramClient(path.replace('.session',''), API_ID, API_HASH)
                await client.connect()
                me = await client.get_me()
                if uid not in accounts: accounts[uid] = {}
                accounts[uid][f'acc_{len(accounts[uid])+1}'] = {'client': client, 'phone': me.phone, 'name': me.first_name}
                current_step[uid] = 'menu'
                await event.respond(f"✅ {me.first_name}", buttons=get_main_kb())
            except Exception as e: await event.respond(f"❌ {e}")
            return
        
        if step == 'wait_str' and txt and txt.startswith('1') and len(txt) > 100:
            try:
                client = TelegramClient(StringSession(txt), API_ID, API_HASH)
                await client.connect()
                me = await client.get_me()
                if uid not in accounts: accounts[uid] = {}
                accounts[uid][f'acc_{len(accounts[uid])+1}'] = {'client': client, 'phone': me.phone, 'name': me.first_name}
                current_step[uid] = 'menu'
                await event.respond(f"✅ {me.first_name}", buttons=get_main_kb())
            except Exception as e: await event.respond(f"❌ {e}")
            return
        
        if step == 'wait_mat':
            if event.message.file:
                name = event.message.file.name or 'file'
                path = await event.message.download_media(file=os.path.join(MATERIALS_DIR, f'mat_{uid}_{name}'))
                cap = event.message.message or ''
                save_material(uid, path, cap)
                current_step[uid] = 'menu'
                await event.respond(f"✅ {name}", buttons=get_main_kb())
            elif txt:
                save_material(uid, None, txt)
                current_step[uid] = 'menu'
                await event.respond(f"✅ Текст сохранен", buttons=get_main_kb())
            return

    async def do_broadcast(bot, uid, event):
        accs = get_user_accounts(uid)
        mat = current_materials.get(uid)
        if not accs or not mat: return
        targets = []
        for aid, acc in accs.items():
            try:
                c = await acc['client'](GetContactsRequest(0))
                targets.extend([(aid, acc, u) for u in c.users if u.mutual_contact and not u.bot])
            except Exception as e: await event.respond(f"❌ {aid}: {e}")
        if not targets: await event.respond("⚠️ Нет контактов"); return
        total, sent, failed, last_p = len(targets), 0, 0, 0
        msg = await event.respond(f"🚀 Всего: {total}\n⏳ 0/{total}")
        for aid, acc, tgt in targets:
            try:
                if mat['file']:
                    fn = os.path.basename(mat['file'])
                    if fn.startswith(f'mat_{uid}_'): fn = fn[len(f'mat_{uid}_'):]
                    await acc['client'].send_file(tgt.id, mat['file'], caption=mat['caption'], attributes=[DocumentAttributeFilename(file_name=fn)])
                else:
                    await acc['client'].send_message(tgt.id, mat['caption'])
                sent += 1
            except: failed += 1
            if sent % 10 == 0 and sent != last_p:
                await msg.edit(f"🚀 ✅ {sent}/{total}\n❌ {failed}"); last_p = sent
            await asyncio.sleep(2)
        clear_user_sessions(uid)
        update_stats(uid, sent)
        s = get_stats(uid)
        await event.respond(f"✅ Готово!\n📤 {sent}\n❌ {failed}\n📊 Всего: {s['total']}", buttons=get_after_broadcast_kb())
        current_step[uid] = 'after'

    # Запускаем бота
    await bot.run_until_disconnected()

if __name__ == '__main__':
    # Запускаем оба процесса параллельно
    loop = asyncio.get_event_loop()
    loop.create_task(main())
    # Веб-сервер на порту 8080
    web.run_app(web.Application(), host='0.0.0.0', port=8080, print=None)
