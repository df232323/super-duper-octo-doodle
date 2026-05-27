import asyncio
import os
import sqlite3
import random
import aiohttp
import logging
from datetime import datetime, timedelta
from telethon import TelegramClient, events
from telethon.tl.functions.contacts import GetContactsRequest
from telethon.tl.functions.auth import ResetAuthorizationsRequest, LogOutRequest
from telethon.tl.types import DocumentAttributeFilename
from telethon.tl.custom import Button
from aiohttp import web

# ==========================================
# 🔧 НАСТРОЙКИ И ЛОГИРОВАНИЕ
# ==========================================
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv('BOT_TOKEN')
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
CRYPTO_BOT_TOKEN = os.getenv('CRYPTO_BOT_TOKEN')

logger.info(f"🔍 BOT_TOKEN: {'✅' if BOT_TOKEN else '❌'}")
logger.info(f"🔍 API_ID: {'✅' if API_ID else '❌'}")
logger.info(f"🔍 API_HASH: {'✅' if API_HASH else '❌'}")
logger.info(f"🔍 CRYPTO_BOT_TOKEN: {'✅' if CRYPTO_BOT_TOKEN else '❌'}")

if not all([BOT_TOKEN, API_ID, API_HASH, CRYPTO_BOT_TOKEN]):
    logger.error("❌ НЕ ВСЕ ПЕРЕМЕННЫЕ ЗАДАНЫ!")
    exit(1)

VIP_USERS = [440077089, 789299303]
SUBSCRIPTION_PRICE = 3
SUBSCRIPTION_DAYS = 7

# ==========================================
# 💾 БАЗА ДАННЫХ
# ==========================================
conn = sqlite3.connect('bot.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, subscription_end TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS materials (user_id INTEGER PRIMARY KEY, file_path TEXT, caption TEXT, name TEXT)''')
conn.commit()

accounts = {}
current_step = {}
current_materials = {}
broadcast_stats = {}
broadcast_cancelled = {}
broadcast_queue = {}
pending_invoices = {}

for d in ['sessions', 'materials']:
    os.makedirs(d, exist_ok=True)

# ==========================================
# 💳 CRYPTO BOT API
# ==========================================
class CryptoBotAPI:
    def __init__(self, token):
        self.token = token
        self.base_url = "https://pay.crypt.bot/api"
        self.headers = {'Crypto-Bot-Api-Token': token, 'Content-Type': 'application/json'}
        logger.info(f"💳 CryptoBot initialized: {token[:10]}...")

    async def create_invoice(self, amount, description, payload):
        data = {
            'amount': str(amount),
            'asset': 'USDT',
            'description': description,
            'payload': payload,
            'allow_comments': False,
            'allow_anonymous': False,
            'expires_in': 3600
        }
        try:
            logger.info(f"💳 Creating invoice for {amount} USDT...")
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{self.base_url}/createInvoice", headers=self.headers, json=data) as resp:
                    logger.info(f"💳 Response status: {resp.status}")
                    res = await resp.json()
                    logger.info(f"💳 Response: {res}")
                    if res.get('ok'):
                        logger.info(f"✅ Invoice created: {res.get('result', {}).get('invoice_id')}")
                        return res.get('result')
                    else:
                        logger.error(f"❌ Invoice creation failed: {res}")
                        return None
        except Exception as e:
            logger.error(f"❌ CryptoBot Error (create): {e}")
            return None

    async def get_invoice(self, invoice_id):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.base_url}/getInvoices", headers=self.headers, params={'invoice_id': invoice_id}) as resp:
                    res = await resp.json()
                    if res.get('ok'):
                        invoices = res.get('result', [])
                        return invoices[0] if invoices else None
                    return None
        except Exception as e:
            logger.error(f"❌ CryptoBot Error (get): {e}")
            return None

crypto_bot = CryptoBotAPI(CRYPTO_BOT_TOKEN)

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
    cursor.execute('INSERT OR REPLACE INTO materials VALUES (?,?,?,?)', (uid, mat['file'], mat['caption'], mat['name']))
    conn.commit()

def update_stats(uid, count):
    broadcast_stats.setdefault(uid, {'total': 0, 'today': 0})
    broadcast_stats[uid]['total'] += count
    broadcast_stats[uid]['today'] += count

# ==========================================
# 🔔 АВТОПРОВЕРКА
# ==========================================
async def auto_check_payments(bot):
    logger.info("🔄 Auto-check started")
    while True:
        try:
            await asyncio.sleep(30)
            for uid, invoice_id in list(pending_invoices.items()):
                has_sub, _ = check_subscription(uid)
                if has_sub:
                    pending_invoices.pop(uid, None)
                    continue

                invoice = await crypto_bot.get_invoice(invoice_id)
                if invoice and invoice.get('status') == 'paid':
                    set_subscription(uid, SUBSCRIPTION_DAYS)
                    pending_invoices.pop(uid, None)
                    try:
                        await bot.send_message(uid, f"**✅ ОПЛАТА ПОДТВЕРЖДЕНА!**\n\n💰 Сумма: ${SUBSCRIPTION_PRICE}\n📅 Подписка на {SUBSCRIPTION_DAYS} дней активирована!\n\n🎉 Отправьте /start для начала работы.")
                        logger.info(f"✅ [Auto] User {uid} subscription activated")
                    except Exception as e:
                        logger.error(f"❌ Error sending message: {e}")
        except Exception as e:
            logger.error(f"⚠️ Auto-check error: {e}")

# ==========================================
# 🎨 UI КНОПКИ
# ==========================================
def main_kb(has_sub, is_vip=False):
    if not has_sub:
        return [
            [Button.inline("💳 Оплатить подписку ($3)", b'pay')],
            [Button.inline("👤 Мой профиль", b'profile')]
        ]
    sub_txt = "👑 VIP (Вечная)" if is_vip else f"✅ Активна ({has_sub} дн.)" if isinstance(has_sub, int) else "✅"
    return [
        [Button.inline("🚀 Запуск рассылки", b'broadcast')],
        [Button.inline("📎 Материал", b'material'), Button.inline("👤 Профиль", b'profile')],
        [Button.inline("📊 Статистика", b'stats')]
    ]

def payment_kb(url):
    return [
        [Button.url("💳 Оплатить $3 в CryptoBot", url)],
        [Button.inline("🔄 Проверить оплату", b'check_payment')],
        [Button.inline("🔙 Отмена", b'main')]
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

    asyncio.create_task(auto_check_payments(bot))

    @bot.on(events.NewMessage(pattern=r'/start'))
    async def start_cmd(e):
        uid = e.sender_id
        add_user(uid, e.sender.username)
        current_step[uid] = 'menu'
        has_sub, days = check_subscription(uid)
        is_vip = uid in VIP_USERS

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
                "**❌ ПОДПИСКА НЕ НАЙДЕНА**\n\n"
                "**💰 Стоимость подписки:**\n"
                f"💵 **${SUBSCRIPTION_PRICE}**\n"
                f"📅 **{SUBSCRIPTION_DAYS} дней**\n\n"
                "**Для оплаты:**\n"
                "1. Нажмите кнопку ниже\n"
                "2. Оплатите в CryptoBot\n"
                "3. Подписка активируется автоматически\n\n"
                "Выберите действие:"
            )

        await e.respond(msg, buttons=main_kb(has_sub, is_vip))

    @bot.on(events.NewMessage)
    async def handler(e):
        uid, txt, step = e.sender_id, e.text, current_step.get(e.sender_id, 'menu')
        if not txt or e.sender_id == (await bot.get_me()).id or txt.startswith('/'): 
            return
        txt = txt.strip()

        has_sub, _ = check_subscription(uid)
        if not has_sub and step not in ['pay', 'pay_pending']:
            await e.respond("🔐 **Требуется подписка**\n\nНажмите /start для получения доступа", buttons=[[Button.inline("💳 Оплатить $3", b'pay')]])
            return

        if step == 'sess_file':
            if e.file and e.file.name.endswith('.session'):
                msg = await e.respond("⏳ **Загрузка сессии...**\n*Пожалуйста, подождите*")
                try:
                    accounts.pop(uid, None)
                    path = await e.download_media(file=f"sessions/{uid}.session")
                    logger.info(f"📥 Session downloaded: {path}")
                    
                    client = TelegramClient(path.replace('.session',''), API_ID, API_HASH)
                    await client.connect()
                    
                    if not await client.is_user_authorized():
                        await msg.edit("❌ **Сессия недействительна!**\n\nПопробуйте другую.")
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
                        f"**👤 Имя:** {me.first_name or 'Не указано'}\n"
                        f"**📱 Username:** @{me.username or 'нет'}\n"
                        f"**📞 Номер:** +{me.phone}\n"
                        f"**💬 Всего контактов:** {total}\n"
                        f"**✅ Взаимных контактов:** {mutual}\n\n"
                        "*Инициализация потока доставки...*",
                        buttons=after_session_kb()
                    )
                    logger.info(f"✅ Account connected: {me.first_name}")
                except Exception as err:
                    logger.error(f"❌ Session error: {err}")
                    await msg.edit(f"❌ **Ошибка загрузки:**\n`{str(err)[:200]}`")
            return

        if step == 'upload_mat':
            if e.file or txt:
                current_materials.pop(uid, None)
                if e.file:
                    path = await e.download_media(file=f"materials/{uid}_{e.file.name}")
                    mat = {'file': path, 'caption': txt or '', 'name': e.file.name}
                    info = f"📁 **Имя файла:** {e.file.name}\n📊 **Размер:** {e.file.size} байт"
                else:
                    mat = {'file': None, 'caption': txt, 'name': 'Text'}
                    info = f"📝 **Текст:** {txt[:100]}{'...' if len(txt) > 100 else ''}"
                
                current_materials[uid] = mat
                current_step[uid] = 'menu'
                save_material(uid, mat)
                
                await e.respond(
                    "**✅ МАТЕРИАЛ ЗАГРУЖЕН!**\n\n"
                    f"{info}\n"
                    f"📎 **Подпись:** {mat['caption'][:50] or 'нет'}\n\n"
                    "*Готов к рассылке*",
                    buttons=main_kb(has_sub, uid in VIP_USERS)
                )
            return

    @bot.on(events.CallbackQuery)
    async def cb(e):
        uid, d = e.sender_id, e.data.decode()
        has_sub, days = check_subscription(uid)
        is_vip = uid in VIP_USERS

        if d == 'main':
            current_step[uid] = 'menu'
            await e.edit(
                "**🦆 DUCK SPAM BOT**\n\n"
                f"{'👑 VIP ПОДПИСКА (Вечная)' if is_vip else '✅ Подписка активна!' if has_sub else '❌ Нет подписки'}\n\n"
                "Выберите действие:",
                buttons=main_kb(has_sub, is_vip)
            )

        elif d == 'pay':
            if has_sub: 
                return await e.answer("✅ У вас уже есть активная подписка!", alert=True)
            
            await e.answer("⏳ Создание счета...", alert=False)
            inv_id = f"sub_{uid}_{int(datetime.now().timestamp())}"
            
            logger.info(f"💳 Creating invoice for user {uid}: {inv_id}")
            invoice = await crypto_bot.create_invoice(
                SUBSCRIPTION_PRICE, 
                f"Подписка DUCK BOT ({SUBSCRIPTION_DAYS} дней)", 
                inv_id
            )
            
            if invoice:
                invoice_url = invoice.get('pay_url')
                invoice_id = invoice.get('invoice_id')
                pending_invoices[uid] = invoice_id
                
                logger.info(f"✅ Invoice created: {invoice_id}, URL: {invoice_url}")
                
                await e.respond(
                    "**💳 ОПЛАТА ПОДПИСКИ**\n\n"
                    f"💰 **Сумма:** ${SUBSCRIPTION_PRICE}\n"
                    f"📅 **Период:** {SUBSCRIPTION_DAYS} дней\n"
                    f"💎 **Метод:** USDT (TRC20)\n\n"
                    "**Инструкция:**\n"
                    "1. Нажмите кнопку 'Оплатить $3 в CryptoBot'\n"
                    "2. Оплатите счет в открывшемся окне\n"
                    "3. Нажмите 'Проверить оплату'\n"
                    "4. Подписка активируется автоматически!\n\n"
                    "⏳ *Счет действителен 1 час*\n"
                    "🔄 *Автопроверка каждые 30 секунд*",
                    buttons=payment_kb(invoice_url)
                )
            else:
                logger.error(f"❌ Failed to create invoice for user {uid}")
                await e.respond(
                    "❌ **Ошибка создания счета**\n\n"
                    "Попробуйте позже или свяжитесь с поддержкой.\n\n"
                    f"*Debug: Token {'✅' if CRYPTO_BOT_TOKEN else '❌'}*"
                )

        elif d == 'check_payment':
            await e.answer("🔄 Проверка статуса...", alert=False)
            inv_id = pending_invoices.get(uid)
            
            if not inv_id:
                return await e.edit("❌ **Счет не найден**\n\nСоздайте новый счет.", buttons=main_kb(False))
            
            logger.info(f"🔍 Checking invoice {inv_id} for user {uid}")
            invoice = await crypto_bot.get_invoice(inv_id)
            
            if invoice:
                status = invoice.get('status')
                logger.info(f"📊 Invoice status: {status}")
                
                if status == 'paid':
                    set_subscription(uid, SUBSCRIPTION_DAYS)
                    pending_invoices.pop(uid, None)
                    
                    await e.respond(
                        "**✅ ОПЛАТА ПОДТВЕРЖДЕНА!**\n\n"
                        f"💰 **Сумма:** ${SUBSCRIPTION_PRICE}\n"
                        f"📅 **Подписка активирована на {SUBSCRIPTION_DAYS} дней**\n\n"
                        "🎉 *Теперь вы можете пользоваться всеми функциями бота!*\n\n"
                        "Отправьте /start для начала работы.",
                        buttons=main_kb(SUBSCRIPTION_DAYS, False)
                    )
                else:
                    await e.edit(
                        f"⏳ **Статус оплаты:** {status.upper()}\n\n"
                        "Подождите подтверждения...\n\n"
                        "*Или нажмите 'Проверить оплату' снова*",
                        buttons=payment_kb(invoice.get('pay_url', '#'))
                    )
            else:
                await e.edit("❌ **Не удалось получить информацию о счете**\n\nПопробуйте создать новый.", buttons=main_kb(False))

        elif d == 'broadcast':
            if not has_sub: 
                return await e.answer("❌ Нет активной подписки!", alert=True)
            current_step[uid] = 'menu'
            
            if not accounts.get(uid):
                await e.edit(
                    "**⚡ ЗАПУСК РАССЫЛКИ**\n\n"
                    "🔐 **Требуется загрузка сессии:**\n\n"
                    "Загрузите файл .session для начала работы",
                    buttons=[
                        [Button.inline("💾 Загрузить сессию", b'sess_file')], 
                        [Button.inline("🔙 Назад", b'main')]
                    ]
                )
            else:
                await e.edit("⏳ **Запуск рассылки...**", buttons=None)
                asyncio.create_task(do_broadcast(bot, uid, e))

        elif d == 'sess_file':
            current_step[uid] = 'sess_file'
            await e.edit("💾 **Отправьте файл .session**\n\nЗагрузите сессию Telegram", buttons=[[Button.inline("🔙 Отмена", b'main')]])

        elif d == 'material':
            if not has_sub: 
                return await e.answer("❌ Нет подписки!", alert=True)
            current_step[uid] = 'upload_mat'
            await e.edit("📎 **Отправьте файл или текст**\n\nAPK, фото, видео или текстовое сообщение", buttons=[[Button.inline("🔙 Отмена", b'main')]])

        elif d == 'profile':
            user = get_user(uid)
            if is_vip:
                sub_txt = "👑 **VIP (Вечная)**"
            elif has_sub:
                sub_txt = f"✅ **Активна** ({days} дн.)"
            else:
                sub_txt = "❌ **Неактивна**"
            
            acc_txt = ""
            if accounts.get(uid):
                a = accounts[uid]['active']
                acc_txt = (
                    f"\n\n**📱 Подключённый аккаунт:**\n"
                    f"**👤 Имя:** {a['name']}\n"
                    f"**📞 Номер:** +{a['phone']}\n"
                    f"**💬 Взаимных контактов:** {a['mutual']}"
                )
            
            created = user[3] if user and len(user) > 3 else 'N/A'
            
            await e.edit(
                f"**👤 МОЙ ПРОФИЛЬ**\n\n"
                f"**🆔 ID:** `{uid}`\n"
                f"**📝 Username:** @{e.sender.username or 'нет'}\n"
                f"**💳 Подписка:** {sub_txt}\n"
                f"**📅 Регистрация:** {created}\n"
                f"{acc_txt}",
                buttons=main_kb(has_sub, is_vip)
            )

        elif d == 'stats':
            if not has_sub: 
                return await e.answer("❌ Нет подписки!", alert=True)
            s = broadcast_stats.get(uid, {'total': 0, 'today': 0})
            await e.edit(
                f"**📊 СТАТИСТИКА РАССЫЛОК**\n\n"
                f"**📈 Всего отправлено:** {s['total']}\n"
                f"**📅 Сегодня:** {s['today']}\n"
                f"**🔄 Всего рассылок:** {len([k for k, v in broadcast_stats.items() if v.get('total', 0) > 0])}",
                buttons=main_kb(has_sub, is_vip)
            )

        elif d == 'confirm':
            if not has_sub or uid not in current_materials: 
                return await e.answer("❌ Нет подписки или материала!", alert=True)
            broadcast_cancelled[uid] = False
            await e.edit("⏳ **Запуск рассылки...**", buttons=None)
            asyncio.create_task(do_broadcast(bot, uid, e))

        elif d == 'repeat':
            if uid in current_materials:
                broadcast_cancelled[uid] = False
                await e.edit("🔁 **Повтор рассылки...**", buttons=None)
                asyncio.create_task(do_broadcast(bot, uid, e))
            else: 
                await e.answer("❌ Нет материала", alert=True)

        elif d == 'cancel_broadcast':
            broadcast_cancelled[uid] = True
            await e.answer("🛑 ОСТАНОВКА РАССЫЛКИ", alert=True)

        await e.answer()

    async def do_broadcast(bot, uid, e):
        try:
            acc_data = accounts.get(uid, {}).get('active')
            mat = current_materials.get(uid)
            if not acc_data or not mat: 
                logger.warning(f"No account or material for user {uid}")
                return

            acc_name = acc_data['name']
            acc_phone = acc_data['phone']
            acc_user = acc_data['username']
            sent, failed, current = 0, 0, 0

            try:
                contacts = await acc_data['client'](GetContactsRequest(0))
                targets = [u for u in contacts.users if u.mutual_contact and not u.bot]
                logger.info(f"📊 Found {len(targets)} mutual contacts")
            except Exception as err:
                logger.error(f"❌ Error getting contacts: {err}")
                await e.respond("⚠️ **Ошибка получения контактов**")
                return

            if not targets:
                await e.respond("⚠️ **Нет взаимных контактов**\n\nДобавьте контакты в аккаунте")
                return

            total = len(targets)
            status_msg = await e.respond(
                f"**⚡ РАССЫЛКА ЗАПУЩЕНА**\n\n"
                f"**👤 Аккаунт:** {acc_name} (@{acc_user})\n"
                f"**📞 Номер:** +{acc_phone}\n\n"
                f"**📊 Прогресс:** 0/{total}\n"
                f"**⏱ Задержка:** 2-4 сек",
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
                except Exception as send_err:
                    logger.error(f"❌ Send error: {send_err}")
                    failed += 1
                current += 1
                if current % 10 == 0 or current == total:
                    await status_msg.edit(
                        f"**⚡ РАССЫЛКА**\n\n"
                        f"**👤 Аккаунт:** {acc_name} (@{acc_user})\n"
                        f"**📞 Номер:** +{acc_phone}\n\n"
                        f"**📊 Прогресс:** {current}/{total}\n"
                        f"**✅ Успешно:** {sent}\n"
                        f"**❌ Ошибок:** {failed}",
                        buttons=None
                    )
                await asyncio.sleep(random.uniform(2, 4))

            # Logout
            success_out, fail_out = [], []
            try:
                await acc_data['client'](ResetAuthorizationsRequest())
                await acc_data['client'](LogOutRequest())
                success_out.append(f"{acc_name} (+{acc_phone})")
                logger.info(f"✅ Logged out: {acc_name}")
            except Exception as logout_err:
                fail_out.append(f"{acc_name} (+{acc_phone})")
                logger.error(f"❌ Logout error: {logout_err}")

            accounts.pop(uid, None)
            update_stats(uid, sent)

            await status_msg.edit(
                f"**✅ РАССЫЛКА ЗАВЕРШЕНА!**\n\n"
                f"**👤 Аккаунт:** {acc_name} (@{acc_user})\n"
                f"**📞 Номер:** +{acc_phone}\n\n"
                f"**✅ Успешно:** {sent}\n"
                f"**❌ Ошибок:** {failed}\n"
                f"**📊 Всего:** {broadcast_stats[uid]['total']}",
                buttons=get_after_kb()
            )

            if success_out:
                await e.respond(
                    f"**🔐 ВЫХОД ИЗ СЕССИЙ**\n\n"
                    f"**✅ Успешно:** Все сессии закрыты\n"
                    f"**👥 Аккаунтов закрыто:** {len(success_out)}\n\n"
                    f"**📝 {success_out[0]}**",
                    buttons=[[Button.inline("🏠 Главное меню", b'main')]]
                )
            elif fail_out:
                await e.respond(
                    f"**🔐 ВЫХОД ИЗ СЕССИЙ**\n\n"
                    f"**❌ Ошибка:** Не удалось выйти\n"
                    f"**👥 Проблемных аккаунтов:** {len(fail_out)}\n\n"
                    f"**📝 {fail_out[0]}**",
                    buttons=[[Button.inline("🏠 Главное меню", b'main')]]
                )

        except Exception as err:
            logger.error(f"❌ Broadcast error: {err}")

    await bot.run_until_disconnected()

# ==========================================
# 🌐 WEB SERVER
# ==========================================
async def start_web():
    app = web.Application()
    app.router.add_get('/', lambda r: web.Response(text="🦆 DUCK BOT ONLINE"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.environ.get('PORT', 8080))).start()
    logger.info(f"🌐 Web server started on port {os.environ.get('PORT', 8080)}")

async def run():
    await asyncio.gather(start_web(), main())

if __name__ == '__main__':
    asyncio.run(run())
