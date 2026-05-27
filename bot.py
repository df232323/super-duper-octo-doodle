import asyncio
import os
import sqlite3
import random
import logging
from datetime import datetime
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
CORRECT_PIN = "6611"

logging.basicConfig(filename='bot.log', level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

conn = sqlite3.connect('bot_database.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('CREATE TABLE IF NOT EXISTS materials (user_id INTEGER PRIMARY KEY, file_path TEXT, caption TEXT, name TEXT, original_name TEXT, size INTEGER)')
conn.commit()

accounts = {}
current_step = {}
current_materials = {}
broadcast_stats = {}
authorized_users = {}
broadcast_cancelled = {}
broadcast_queue = {}

for d in ['sessions', 'materials']:
    os.makedirs(d, exist_ok=True)

# ==========================================
# 🎨 ОФОРМЛЕНИЕ
# ==========================================
def embed(title, desc=None, fields=None, footer=None):
    text = f"**{title}**\n"
    if desc: text += f"{desc}\n\n"
    if fields:
        for f in fields: text += f"{f.get('emoji', '•')} **{f['name']}**: {f['value']}\n"
    if footer: text += f"\n*{footer}*"
    return text

def get_main_kb():
    return [
        [Button.inline(" Запуск рассылки", b'broadcast')],
        [Button.inline("📎 Материал", b'material'), Button.inline("👤 Мой профиль", b'profile')],
        [Button.inline("📊 Статистика", b'stats')]
    ]

def get_protocol_kb():
    return [
        [Button.inline("📱 Через номер", b'phone')],
        [Button.inline("💾 Импорт сессии", b'sess_file')],
        [Button.inline("✕ Отмена", b'main')]
    ]

def get_active_kb():
    return [[Button.inline("🛑 Остановить", b'cancel_broadcast')]]

def get_after_kb():
    return [
        [Button.inline("🔁 Повторить", b'repeat')],
        [Button.inline("🏠 Главное меню", b'main')]
    ]

# ==========================================
#  БАЗА ДАННЫХ
# ==========================================
def save_material(uid, mat):
    cursor.execute('INSERT OR REPLACE INTO materials (user_id, file_path, caption, name, original_name, size) VALUES (?, ?, ?, ?, ?, ?)',
                   (uid, mat['file'], mat['caption'], mat['name'], mat['original_name'], mat.get('size', 0)))
    conn.commit()

def load_material(uid):
    cursor.execute('SELECT * FROM materials WHERE user_id = ?', (uid,))
    row = cursor.fetchone()
    if row: return {'file': row[1], 'caption': row[2], 'name': row[3], 'original_name': row[4], 'size': row[5]}
    return None

def update_stats(uid, count):
    broadcast_stats.setdefault(uid, {'total': 0, 'daily': {}, 'broadcasts': 0})
    today = str(datetime.now().date())
    broadcast_stats[uid]['total'] += count
    broadcast_stats[uid]['broadcasts'] += 1
    broadcast_stats[uid]['daily'][today] = broadcast_stats[uid]['daily'].get(today, 0) + count

def get_stats(uid, period):
    s = broadcast_stats.get(uid, {})
    d = datetime.now().date()
    return s.get('daily', {}).get(str(d), 0) if period == 'day' else s.get('total', 0)

# ==========================================
# 🏁 ГЛАВНЫЙ БОТ
# ==========================================
async def main():
    bot = TelegramClient('bot_session', API_ID, API_HASH)
    await bot.start(bot_token=BOT_TOKEN)
    bot_id = (await bot.get_me()).id
    logger.info(f"✅ Bot started: {bot_id}")

    @bot.on(events.NewMessage(pattern=r'/start'))
    async def start(e):
        uid = e.sender_id
        current_step[uid] = 'pin'
        await e.respond(
            embed("🔐 **ТРЕБУЕТСЯ АВТОРИЗАЦИЯ**",
                  "Для доступа к панели управления необходимо ввести PIN-код.\n\n"
                  "️ **Без кода вы не сможете воспользоваться функционалом бота!**\n\n"
                  "📝 Введите 4-значный PIN-код для продолжения...\n\n"
                  "*Если вы не знаете код, обратитесь к администратору.*",
                  footer="Платформа доставки активирована"),
            buttons=None
        )

    @bot.on(events.NewMessage)
    async def handler(e):
        uid, txt = e.sender_id, e.text
        step = current_step.get(uid, 'menu')
        if e.sender_id == bot_id or (txt and txt.startswith('/')): return

        if step == 'pin':
            if txt and txt.isdigit() and len(txt) == 4:
                if txt == CORRECT_PIN:
                    authorized_users[uid] = True
                    current_step[uid] = 'menu'
                    await e.respond(embed("✅ **ДОСТУП РАЗРЕШЁН**", "Добро пожаловать в панель управления!\n\n🦆 **DUCK SPAM BOT v3.0**\n\n• ⚡ Массовая рассылка\n• 👥 Управление аккаунтами\n•  Загрузка файлов\n• 📊 Статистика", footer="Воспользуйтесь навигацией ниже"), buttons=get_main_kb())
                else: await e.respond("❌ **Неверный PIN-код**", buttons=None)
            else: await e.respond("⚠️ **PIN = 4 цифры**", buttons=None)
            return

        if not authorized_users.get(uid):
            await e.respond("🔐 **Требуется /start**", buttons=None); return

        if step == 'sess_file':
            if e.file and e.file.name.endswith('.session'):
                msg = await e.respond("⏳ **Анализ ядра...**", buttons=None)
                try:
                    if uid in accounts:
                        for acc in accounts[uid].values():
                            try: await acc['client'].disconnect()
                            except: pass
                        accounts[uid] = {}
                    
                    path = await e.download_media(file=f"sessions/acc_{uid}.session")
                    client = TelegramClient(path.replace('.session', ''), API_ID, API_HASH)
                    await client.connect()
                    if not await client.is_user_authorized():
                        await msg.edit("❌ **Сессия недействительна!**"); await client.disconnect(); return
                    
                    me = await client.get_me()
                    contacts = await client(GetContactsRequest(0))
                    total = len([u for u in contacts.users if not u.bot])
                    mutual = len([u for u in contacts.users if u.mutual_contact and not u.bot])
                    
                    accounts.setdefault(uid, {})['active'] = {'client': client, 'phone': me.phone, 'name': me.first_name or 'No name', 'username': me.username or 'нет', 'total': total, 'mutual': mutual}
                    current_step[uid] = 'menu'
                    
                    await msg.edit(embed("✅ **Синхронизация завершена!**", "Аккаунт подключён", [
                        {'name': 'Профиль', 'value': f"@{me.username or 'нет'}", 'emoji': ''},
                        {'name': 'Номер', 'value': f"+{me.phone}", 'emoji': '📞'},
                        {'name': 'Контакты', 'value': f"Всего: {total}\nВзаимных: {mutual}", 'emoji': '💬'},
                        {'name': 'Состояние', 'value': 'Подключение стабильно', 'emoji': '⚡'}
                    ], footer="Инициализация потока доставки..."))
                except Exception as err: await msg.edit(f"❌ **Ошибка:** {str(err)[:200]}")
            return

        if step == 'upload_mat':
            if e.file or txt:
                old_mat = current_materials.get(uid)
                if old_mat: await e.respond(f"🗑️ **Предыдущий материал удалён:** `{old_mat.get('name', 'Unknown')}`"); await asyncio.sleep(0.5)
                
                current_materials.pop(uid, None)
                if e.file:
                    path = await e.download_media(file=f"materials/{uid}_{e.file.name}")
                    mat = {'file': path, 'caption': txt or '', 'name': e.file.name, 'original_name': e.file.name, 'size': e.file.size}
                else:
                    mat = {'file': None, 'caption': txt, 'name': 'Text', 'original_name': 'Text', 'size': len(txt)}
                
                current_materials[uid] = mat
                current_step[uid] = 'menu'
                save_material(uid, mat)
                
                preview = f"**Загружено:** {mat['name']}\n**Подпись:** {mat['caption'] or 'нет'}\n\nОжидаю загрузку нового материала (текст, фото или документ)."
                await e.respond(embed("📎 **Контент-менеджер**", preview), buttons=[[Button.inline("✓ Зафиксировать", b'confirm_material')], [Button.inline("✕ Отмена", b'main')]])
            return

    @bot.on(events.CallbackQuery)
    async def cb(e):
        uid = e.sender_id
        if not authorized_users.get(uid): return await e.answer("🔐 /start", alert=True)
        d = e.data.decode()

        if d == 'main':
            current_step[uid] = 'menu'
            await e.edit(embed("⚡ **Платформа доставки активирована.**", "Воспользуйтесь навигацией ниже."), buttons=get_main_kb())
        elif d == 'broadcast':
            current_step[uid] = 'menu'
            if not accounts.get(uid):
                await e.answer("⚠️ Сначала добавьте аккаунт!", alert=True)
                return
            await e.edit(embed("⚡ **Запуск рассылки**", "Выберите протокол:"), buttons=get_protocol_kb())
        elif d == 'sess_file':
            current_step[uid] = 'sess_file'
            await e.edit("💾 **Отправьте .session файл**", buttons=[[Button.inline("✕ Отмена", b'main')]])
        elif d == 'phone':
            current_step[uid] = 'phone'
            await e.edit("📱 **Введите номер (+7...)**", buttons=[[Button.inline("✕ Отмена", b'main')]])
        elif d == 'material':
            current_step[uid] = 'upload_mat'
            mat = current_materials.get(uid)
            if mat: await e.edit(embed(" **Контент-менеджер**", f"**Загружено:** {mat['name']}\n**Подпись:** {mat['caption'] or 'нет'}"), buttons=[[Button.inline("✓ Зафиксировать", b'confirm_material')], [Button.inline("✕ Отмена", b'main')]])
            else: await e.edit("📎 **Загрузите файл или текст**", buttons=[[Button.inline("✕ Отмена", b'main')]])
        elif d == 'profile':
            accs = accounts.get(uid, {})
            if accs:
                acc = list(accs.values())[0]
                await e.edit(embed("👤 **МОЙ ПРОФИЛЬ**", None, [
                    {'name': 'Профиль', 'value': f"@{acc.get('username', 'нет')}", 'emoji': '👤'},
                    {'name': 'Номер', 'value': f"+{acc.get('phone', 'нет')}", 'emoji': '📞'},
                    {'name': 'Контакты', 'value': f"Всего: {acc.get('total', 0)}\nВзаимных: {acc.get('mutual', 0)}", 'emoji': '💬'}
                ]))
            else: await e.edit("️ **Нет подключённых аккаунтов**", buttons=get_main_kb())
        elif d == 'stats':
            t = broadcast_stats.get(uid, {}).get('total', 0)
            await e.edit(embed("📊 **СТАТИСТИКА**", None, [
                {'name': 'Всего отправлено', 'value': str(t), 'emoji': '📊'},
                {'name': 'Сегодня', 'value': str(get_stats(uid, 'day')), 'emoji': '📅'},
                {'name': 'Всего рассылок', 'value': str(broadcast_stats.get(uid, {}).get('broadcasts', 0)), 'emoji': ''}
            ]))
        elif d == 'confirm':
            if uid not in current_materials: return await e.answer("❌ Загрузите материал!", alert=True)
            broadcast_cancelled[uid] = False; broadcast_queue[uid] = True
            await e.edit("⏳ **Доставка инициирована...**", buttons=get_active_kb())
            asyncio.create_task(do_broadcast(bot, uid, e))
        elif d == 'repeat':
            if uid in current_materials:
                broadcast_cancelled[uid] = False; broadcast_queue[uid] = True
                await e.edit("🔁 **Повтор...**", buttons=get_active_kb())
                asyncio.create_task(do_broadcast(bot, uid, e))
            else: await e.answer("❌ Нет материала", alert=True)
        elif d == 'cancel_broadcast':
            broadcast_cancelled[uid] = True; await e.answer("🛑 ОСТАНОВКА...", alert=True)
        
        await e.answer()

    async def do_broadcast(bot, uid, e):
        try:
            accs = accounts.get(uid, {})
            mat = current_materials.get(uid)
            if not accs or not mat: broadcast_queue[uid] = False; return
            
            sent, failed = 0, 0
            targets = []
            for acc in accs.values():
                try:
                    c = await acc['client'](GetContactsRequest(0))
                    targets.append((acc, [u for u in c.users if u.mutual_contact and not u.bot]))
                except: pass
            
            if not targets: await e.respond("⚠️ **Нет контактов**"); broadcast_queue[uid] = False; return
            
            total = sum(len(t) for _,t in targets)
            status = await e.respond(embed("⚡ **Доставка инициирована...**", f"Обработано: 0/{total}"), buttons=get_active_kb())
            
            current, cancelled = 0, False
            for acc, users in targets:
                if broadcast_cancelled.get(uid): cancelled = True; break
                for user in users:
                    if broadcast_cancelled.get(uid): cancelled = True; break
                    try:
                        if mat['file']: await acc['client'].send_file(user.id, mat['file'], caption=mat['caption'], attributes=[DocumentAttributeFilename(file_name=mat.get('original_name', 'file'))])
                        else: await acc['client'].send_message(user.id, mat['caption'])
                        sent += 1
                    except: failed += 1
                    current += 1
                    if current % 10 == 0: await status.edit(embed("⚡ **Доставка инициирована...**", f"Обработано: {current}/{total}"), buttons=get_active_kb())
                    await asyncio.sleep(random.uniform(2, 5))
            
            # ✅ ФИНАЛЬНЫЙ ОТЧЁТ КАК НА СКРИНЕ
            await status.edit(embed("✅ **Доставка успешно завершена!**", None, [
                {'name': 'Доставлено', 'value': str(sent), 'emoji': '✓'},
                {'name': 'Ошибок', 'value': str(failed), 'emoji': '✕'},
                {'name': 'Фильтр', 'value': '0', 'emoji': '🔍'},
                {'name': 'База', 'value': str(total), 'emoji': '📦'}
            ], footer="✓ Задача выполнена."), buttons=None)
            
            await asyncio.sleep(1.5)
            
            # 🔐 ВЫХОД ИЗ СЕССИЙ
            await e.respond("🔐 **Завершение сеансов...**", buttons=None)
            success, fail = [], []
            for acc, _ in targets:
                name = f"{acc['name']} (@{acc.get('username', 'нет')})"
                try:
                    await acc['client'](ResetAuthorizationsRequest())
                    await acc['client'](LogOutRequest())
                    success.append(name)
                except: fail.append(name)
            
            # ️ ОЧИСТКА АККАУНТОВ (МАТЕРИАЛ ОСТАЁТСЯ!)
            accounts[uid] = {}
            if not cancelled: update_stats(uid, sent)
            
            await e.respond(embed("🔐 **Завершение сеансов**", f"Результат: {'✅ Все сессии закрыты' if not fail else f'⚠️ {len(fail)} ошибок'}", [
                {'name': '✅ Успешно', 'value': '\n'.join(success[:3]), 'emoji': '✅'} if success else {},
                {'name': '❌ Ошибки', 'value': '\n'.join(fail[:3]), 'emoji': '❌'} if fail else {}
            ], "*Все устройства вылогинены из аккаунтов*"), buttons=None)
            
            # 🏠 ВОЗВРАТ В МЕНЮ (МАТЕРИАЛ СОХРАНЁН, АККАУНТОВ НЕТ)
            current_step[uid] = 'menu'
            broadcast_queue[uid] = False
            await e.respond(embed("⚡ **Платформа доставки активирована.**", "Воспользуйтесь навигацией ниже.\n\n *Материал сохранён. Загрузите новую сессию для следующей рассылки.*"), buttons=get_main_kb())
            
            logger.info(f"[BROADCAST] Done: {sent} sent, {failed} failed. Accounts cleared. Material kept.")
        except Exception as err:
            logger.error(f"[BROADCAST] Error: {err}")
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
