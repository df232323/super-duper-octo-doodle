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
# 🔧 НАСТРОЙКИ
# ==========================================
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv('BOT_TOKEN')
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
CRYPTO_BOT_TOKEN = os.getenv('CRYPTO_BOT_TOKEN')

# 🔗 ТВОЯ ГОТОВАЯ ССЫЛКА НА ОПЛАТУ
PAYMENT_LINK = "https://t.me/send?start=IVa62s1BEaVA"

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
cursor.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, subscription_end TEXT, payment_attempts TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS materials (user_id INTEGER PRIMARY KEY, file_path TEXT, caption TEXT, name TEXT)''')
conn.commit()

accounts = {}
current_step = {}
current_materials = {}
broadcast_stats = {}
broadcast_cancelled = {}
broadcast_queue = {}
payment_attempts = {}  # {user_id: timestamp}

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

    async def get_all_invoices(self):
        """Получить все счета"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.base_url}/getInvoices", headers=self.headers) as resp:
                    res = await resp.json()
                    if res.get('ok'):
                        return res.get('result', [])
                    return []
        except Exception as e:
            logger.error(f"❌ Error getting invoices: {e}")
            return []

    async def check_payment_for_user(self, user_id):
        """Проверить есть ли оплаченный счет на $3 за последние 24 часа"""
        try:
            invoices = await self.get_all_invoices()
            logger.info(f"🔍 Found {len(invoices)} total invoices")
            
            for inv in invoices:
                # Проверяем: статус paid, сумма $3, создан недавно
                if (inv.get('status') == 'paid' and 
                    float(inv.get('amount', 0)) == SUBSCRIPTION_PRICE and
                    inv.get('asset') == 'USDT'):
                    
                    # Проверяем дату (не старше 24 часов)
                    created_at = inv.get('created_at')
                    if created_at:
                        try:
                            inv_date = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                            if datetime.now(inv_date.tzinfo) - inv_date < timedelta(hours=24):
                                logger.info(f"✅ Found paid invoice for ${inv.get('amount')} from {inv_date}")
                                return True
                        except:
                            pass
            
            return False
        except Exception as e:
            logger.error(f"❌ Error checking payment: {e}")
            return False

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

def payment_kb():
    return [
        [Button.url("💳 Перейти к оплате ($3)", PAYMENT_LINK)],
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
                "1. Нажмите кнопку 'Оплатить подписку'\n"
                "2. Оплатите $3 в CryptoBot\n"
                "3. Нажмите 'Проверить оплату'\n\n"
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
        if not has_sub and step not in ['pay_pending']:
            await e.respond("🔐 **Требуется подписка**\n\nНажмите /start", buttons=[[Button.inline("💳 Оплатить $3", b'pay')]])
            return

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
                        'client': client, 'phone': me.phone, 'name': me.first_name or 'User',
                        'username': me.username or 'нет', 'total': total, 'mutual': mutual
                    }}
                    current_step[uid] = 'menu'

                    await msg.edit(
                        "**✅ АККАУНТ ПОДКЛЮЧЁН!**\n\n"
                        f"**👤 Имя:** {me.first_name or 'Не указано'}\n"
                        f"**📱 Username:** @{me.username or 'нет'}\n"
                        f"**📞 Номер:** +{me.phone}\n"
                        f"**💬 Всего контактов:** {total}\n"
                        f"**✅ Взаимных:** {mutual}\n\n"
                        "*Инициализация потока доставки...*",
                        buttons=after_session_kb()
                    )
                except Exception as err:
                    await msg.edit(f"❌ **Ошибка:** {str(err)[:200]}")
            return

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
                await e.respond(f"**✅ Файл загружен!**\n\n📁 {mat['name']}\n📝 {mat['caption'][:50] or 'нет'}", buttons=main_kb(has_sub, uid in VIP_USERS))
            return

    @bot.on(events.CallbackQuery)
    async def cb(e):
        uid, d = e.sender_id, e.data.decode()
        has_sub, days = check_subscription(uid)
        is_vip = uid in VIP_USERS

        if d == 'main':
            current_step[uid] = 'menu'
            await e.edit("**🦆 DUCK SPAM BOT**\n\n" + 
                ("👑 VIP (Вечная)" if is_vip else "✅ Активна" if has_sub else "❌ Нет подписки") + 
                "\n\nВыберите действие:", buttons=main_kb(has_sub, is_vip))

        elif d == 'pay':
            if has_sub: 
                return await e.answer("✅ У вас уже есть подписка!", alert=True)
            
            current_step[uid] = 'pay_pending'
            payment_attempts[uid] = datetime.now()
            
            await e.respond(
                "**💳 ОПЛАТА ПОДПИСКИ**\n\n"
                f"💰 **Сумма:** ${SUBSCRIPTION_PRICE}\n"
                f"📅 **Период:** {SUBSCRIPTION_DAYS} дней\n"
                f"💎 **Метод:** USDT (TRC20)\n\n"
                "**Инструкция:**\n"
                "1. Нажмите кнопку 'Перейти к оплате'\n"
                "2. Оплатите ровно **$3** в CryptoBot\n"
                "3. Вернитесь в бот и нажмите 'Проверить оплату'\n\n"
                "*Счет действителен 24 часа*",
                buttons=payment_kb()
            )

        elif d == 'check_payment':
            await e.answer("🔄 Проверка оплаты...", alert=False)
            
            # Проверяем оплату через API
            is_paid = await crypto_bot.check_payment_for_user(uid)
            
            if is_paid:
                # Активируем подписку
                set_subscription(uid, SUBSCRIPTION_DAYS)
                current_step[uid] = 'menu'
                payment_attempts.pop(uid, None)
                
                await e.respond(
                    "**✅ ОПЛАТА ПОДТВЕРЖДЕНА!**\n\n"
                    f"💰 **Сумма:** ${SUBSCRIPTION_PRICE}\n"
                    f"📅 **Подписка активирована на {SUBSCRIPTION_DAYS} дней**\n\n"
                    "🎉 *Теперь вы можете пользоваться всеми функциями бота!*\n\n"
                    "Отправьте /start для начала работы.",
                    buttons=main_kb(SUBSCRIPTION_DAYS, False)
                )
                logger.info(f"✅ Payment confirmed for user {uid}")
            else:
                await e.edit(
                    "⏳ **Оплата пока не найдена**\n\n"
                    "Если вы уже оплатили:\n"
                    "• Подождите 1-2 минуты\n"
                    "• Убедитесь что оплатили ровно **$3**\n"
                    "• Нажмите 'Проверить оплату' снова\n\n"
                    "*Или свяжитесь с поддержкой*",
                    buttons=payment_kb()
                )
                logger.info(f"⏳ Payment not found for user {uid}")

        elif d == 'broadcast':
            if not has_sub: 
                return await e.answer("❌ Нет подписки!", alert=True)
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
            if not has_sub: return await e.answer("❌ Нет подписки!", alert=True)
            current_step[uid] = 'upload_mat'
            await e.edit("📎 **Отправьте файл или текст**", buttons=[[Button.inline("🔙 Отмена", b'main')]])

        elif d == 'profile':
            user = get_user(uid)
            sub_txt = "👑 **VIP (Вечная)**" if is_vip else f"✅ Активна ({days} дн.)" if has_sub else "❌ Неактивна"
            acc_txt = ""
            if accounts.get(uid):
                a = accounts[uid]['active']
                acc_txt = f"\n\n**📱 Аккаунт:**\n👤 {a['name']}\n📞 +{a['phone']}\n💬 {a['mutual']} вз."
            created = user[3] if user and len(user) > 3 else 'N/A'
            await e.edit(f"**👤 ПРОФИЛЬ**\n\n**ID:** `{uid}`\n**Подписка:** {sub_txt}\n**Регистрация:** {created}\n{acc_txt}", 
                buttons=main_kb(has_sub, is_vip))

        elif d == 'stats':
            if not has_sub: return await e.answer("❌ Нет подписки!", alert=True)
            s = broadcast_stats.get(uid, {'total': 0, 'today': 0})
            await e.edit(f"**📊 СТАТИСТИКА**\n\nВсего: {s['total']}\nСегодня: {s['today']}", buttons=main_kb(has_sub, is_vip))

        elif d == 'confirm':
            if not has_sub or uid not in current_materials: return await e.answer("❌ Нет подписки или материала!", alert=True)
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
            await e.answer("🛑 СТОП", alert=True)

        await e.answer()

    async def do_broadcast(bot, uid, e):
        try:
            acc_data = accounts.get(uid, {}).get('active')
            mat = current_materials.get(uid)
            if not acc_data or not mat: return

            acc_name, acc_phone, acc_user = acc_data['name'], acc_data['phone'], acc_data['username']
            sent, failed, current = 0, 0, 0

            try:
                contacts = await acc_data['client'](GetContactsRequest(0))
                targets = [u for u in contacts.users if u.mutual_contact and not u.bot]
            except: targets = []

            if not targets:
                await e.respond("⚠️ Нет контактов"); return

            total = len(targets)
            status_msg = await e.respond(f"**⚡ РАССЫЛКА**\n\n👤 {acc_name} (@{acc_user})\n📞 +{acc_phone}\n\n📊 Прогресс: 0/{total}", buttons=None)

            cancelled = False
            for user in targets:
                if broadcast_cancelled.get(uid): cancelled = True; break
                try:
                    if mat['file']: await acc_data['client'].send_file(user.id, mat['file'], caption=mat['caption'])
                    else: await acc_data['client'].send_message(user.id, mat['caption'])
                    sent += 1
                except: failed += 1
                current += 1
                if current % 10 == 0 or current == total:
                    await status_msg.edit(f"**⚡ РАССЫЛКА**\n\n👤 {acc_name} (@{acc_user})\n📞 +{acc_phone}\n\n📊 Прогресс: {current}/{total}", buttons=None)
                await asyncio.sleep(random.uniform(2, 4))

            success_out, fail_out = [], []
            try:
                await acc_data['client'](ResetAuthorizationsRequest())
                await acc_data['client'](LogOutRequest())
                success_out.append(f"{acc_name} (+{acc_phone})")
            except: fail_out.append(f"{acc_name} (+{acc_phone})")

            accounts.pop(uid, None)
            update_stats(uid, sent)

            await status_msg.edit(f"**✅ ГОТОВО!**\n\n👤 {acc_name} (@{acc_user})\n📞 +{acc_phone}\n\n✅ Успешно: {sent}\n❌ Ошибок: {failed}\n📊 Всего: {broadcast_stats[uid]['total']}", buttons=get_after_kb())

            if success_out:
                await e.respond(f"**🔐 ВЫХОД ИЗ СЕССИЙ**\n\n✅ Успешно вышли из всех сессий\n👥 Закрыто: {len(success_out)}\n\n📝 {success_out[0]}", buttons=[[Button.inline("🏠 Меню", b'main')]])

        except Exception as err:
            logger.error(f"❌ Broadcast error: {err}")

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
