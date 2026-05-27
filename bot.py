import asyncio
import os
import sqlite3
import random
import aiohttp
from datetime import datetime, timedelta
from telethon import TelegramClient, events
from telethon.tl.functions.contacts import GetContactsRequest
from telethon.tl.functions.auth import ResetAuthorizationsRequest, LogOutRequest
from telethon.tl.types import DocumentAttributeFilename
from telethon.tl.custom import Button
from aiohttp import web

# ==========================================
# 🔒 БЕЗОПАСНАЯ ЗАГРУЗКА ПЕРЕМЕННЫХ
# ==========================================
BOT_TOKEN = os.getenv('BOT_TOKEN')
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
CRYPTO_BOT_TOKEN = os.getenv('CRYPTO_BOT_TOKEN')

if not all([BOT_TOKEN, API_ID, API_HASH, CRYPTO_BOT_TOKEN]):
    print("❌ ОШИБКА: Не заданы все переменные окружения!")
    print("Добавьте в Render: BOT_TOKEN, API_ID, API_HASH, CRYPTO_BOT_TOKEN")
    exit(1)

# ==========================================
# ⚙️ НАСТРОЙКИ
# ==========================================
VIP_USERS = [440077089, 789299303]
SUBSCRIPTION_PRICE = 3
SUBSCRIPTION_DAYS = 7

#  БАЗА ДАННЫХ
conn = sqlite3.connect('bot.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, subscription_end TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS materials (user_id INTEGER PRIMARY KEY, file_path TEXT, caption TEXT, name TEXT)''')
conn.commit()

# 📦 ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ
accounts = {}
current_step = {}
current_materials = {}
broadcast_stats = {}
broadcast_cancelled = {}
broadcast_queue = {}
pending_invoices = {}  # {user_id: invoice_id}

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

    async def create_invoice(self, amount, description, payload):
        data = {'amount': str(amount), 'asset': 'USDT', 'description': description, 'payload': payload, 'allow_comments': False, 'allow_anonymous': False, 'expires_in': 3600}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{self.base_url}/createInvoice", headers=self.headers, json=data) as resp:
                    res = await resp.json()
                    return res.get('result') if res.get('ok') else None
        except Exception as e:
            print(f"❌ CryptoBot Error (create): {e}")
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
            print(f"❌ CryptoBot Error (get): {e}")
            return None

crypto_bot = CryptoBotAPI(CRYPTO_BOT_TOKEN)

# ==========================================
# 🛠 ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
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

def check_subscription(uid):
    if uid in VIP_USERS: return "VIP", -1
    user = get_user(uid)
    if not user or not user[2]: return False, None
    try:
        end_date = datetime.fromisoformat(user[2])
        if datetime.now() < end_date:
            return (end_date - datetime.now()).days, (end_date - datetime.now()).days
        return False, 0
    except: return False, None

def save_material(uid, mat):
    cursor.execute('INSERT OR REPLACE INTO materials VALUES (?,?,?,?)', (uid, mat['file'], mat['caption'], mat['name']))
    conn.commit()

def update_stats(uid, count):
    broadcast_stats.setdefault(uid, {'total': 0, 'today': 0})
    broadcast_stats[uid]['total'] += count
    broadcast_stats[uid]['today'] += count

# ==========================================
# 🔔 АВТОПРОВЕРКА ОПЛАТ (ФОНОВАЯ ЗАДАЧА)
# ==========================================
async def auto_check_payments(bot):
    print("🔄 Система автопроверки оплат запущена (интервал: 30 сек)")
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
                        print(f"✅ [Auto] User {uid} subscription activated")
                    except: pass
        except Exception as e:
            print(f"️ Auto-check error: {e}")

# ==========================================
# 🎨 UI / КНОПКИ
# ==========================================
def main_kb(has_sub, is_vip=False):
    if not has_sub:
        return [[Button.inline("💳 Оплатить подписку ($3)", b'pay')], [Button.inline("👤 Мой профиль", b'profile')]]
    sub_txt = "👑 VIP" if is_vip else f"✅ ({has_sub} дн.)" if isinstance(has_sub, int) else "✅"
    return [
        [Button.inline("🚀 Запуск рассылки", b'broadcast')],
        [Button.inline(" Материал", b'material'), Button.inline("👤 Профиль", b'profile')],
        [Button.inline("📊 Статистика", b'stats')]
    ]

def payment_kb(url):
    return [
        [Button.url("💳 Оплатить $3", url)],
        [Button.inline("🔄 Проверить оплату", b'check_payment')],
        [Button.inline("🔙 Отмена", b'main')]
    ]

def after_session_kb():
    return [
        [Button.inline("🟢 Запустить", b'confirm')],
        [Button.inline(" Назад", b'main')]
    ]

def get_after_kb():
    return [
        [Button.inline("🔁 Повтор", b'repeat')],
        [Button.inline("🏠 Меню", b'main')]
    ]

# ==========================================
# 🏁 ОСНОВНОЙ БОТ
# ==========================================
async def main():
    bot = TelegramClient('bot_session', API_ID, API_HASH)
    await bot.start(bot_token=BOT_TOKEN)
    bot_id = (await bot.get_me()).id
    print(f"✅ Бот запущен: @{bot_id}")

    # Запуск автопроверки в фоне
    asyncio.create_task(auto_check_payments(bot))

    @bot.on(events.NewMessage(pattern=r'/start'))
    async def start_cmd(e):
        uid = e.sender_id
        add_user(uid, e.sender.username)
        current_step[uid] = 'menu'
        has_sub, days = check_subscription(uid)
        is_vip = uid in VIP_USERS

        if has_sub:
            msg = f"**👑 VIP ПОДПИСКА**\nВечный доступ!" if is_vip else f"**✅ Подписка активна!** ({days} дн.)"
        else:
            msg = f"** Подписка не найдена**\n💰 ${SUBSCRIPTION_PRICE} / {SUBSCRIPTION_DAYS} дней"

        await e.respond(f"**🦆 DUCK BOT**\n\n{msg}\n\nВыберите действие:", buttons=main_kb(has_sub, is_vip))

    @bot.on(events.NewMessage)
    async def handler(e):
        uid, txt, step = e.sender_id, e.text, current_step.get(e.sender_id, 'menu')
        if not txt or e.sender_id == (await bot.get_me()).id or txt.startswith('/'): return
        txt = txt.strip()

        has_sub, _ = check_subscription(uid)
        if not has_sub and step != 'pay_pending':
            await e.respond(" Требуется подписка. Нажмите /start", buttons=[[Button.inline("💳 Оплатить $3", b'pay')]])
            return

        if step == 'sess_file':
            if e.file and e.file.name.endswith('.session'):
                msg = await e.respond("⏳ Загрузка сессии...")
                try:
                    accounts.pop(uid, None)
                    path = await e.download_media(file=f"sessions/{uid}.session")
                    client = TelegramClient(path.replace('.session',''), API_ID, API_HASH)
                    await client.connect()
                    if not await client.is_user_authorized():
                        await msg.edit("❌ Сессия недействительна"); await client.disconnect(); return

                    me = await client.get_me()
                    contacts = await client(GetContactsRequest(0))
                    total = len([u for u in contacts.users if not u.bot])
                    mutual = len([u for u in contacts.users if u.mutual_contact and not u.bot])

                    accounts[uid] = {'active': {'client': client, 'phone': me.phone, 'name': me.first_name or 'User', 'username': me.username or 'нет', 'total': total, 'mutual': mutual}}
                    current_step[uid] = 'menu'

                    await msg.edit(
                        f"**✅ Аккаунт подключён!**\n\n"
                        f"**👤 Имя:** {me.first_name}\n"
                        f"** Username:** @{me.username or 'нет'}\n"
                        f"**📞 Номер:** +{me.phone}\n"
                        f"**💬 Всего контактов:** {total}\n"
                        f"**✅ Взаимных:** {mutual}",
                        buttons=after_session_kb()
                    )
                except Exception as err:
                    await msg.edit(f" Ошибка: {str(err)[:150]}")
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
            await e.edit(f"**🦆 DUCK BOT**\n\n{'👑 VIP' if is_vip else '✅ Активно' if has_sub else '❌ Нет подписки'}\n\nВыберите действие:", buttons=main_kb(has_sub, is_vip))

        elif d == 'pay':
            if has_sub: return await e.answer("✅ У вас уже есть подписка!", alert=True)
            await e.answer("⏳ Создание счета...", alert=False)
            inv_id = f"sub_{uid}_{int(datetime.now().timestamp())}"
            invoice = await crypto_bot.create_invoice(SUBSCRIPTION_PRICE, f"Подписка DUCK BOT ({SUBSCRIPTION_DAYS} дн.)", inv_id)
            if invoice:
                pending_invoices[uid] = invoice.get('invoice_id')
                await e.respond(f"**💳 ОПЛАТА ПОДПИСКИ**\n\n💰 ${SUBSCRIPTION_PRICE} | 📅 {SUBSCRIPTION_DAYS} дн.\n\n1. Нажмите 'Оплатить'\n2. Оплатите в CryptoBot\n3. Нажмите 'Проверить оплату'\n\n Автопроверка каждые 30 сек.", buttons=payment_kb(invoice['pay_url']))
            else:
                await e.respond("❌ Ошибка создания счета.")

        elif d == 'check_payment':
            await e.answer("🔄 Проверка...", alert=False)
            inv_id = pending_invoices.get(uid)
            if not inv_id: return await e.edit("❌ Счет не найден.", buttons=main_kb(False))
            invoice = await crypto_bot.get_invoice(inv_id)
            if invoice and invoice['status'] == 'paid':
                set_subscription(uid, SUBSCRIPTION_DAYS)
                pending_invoices.pop(uid, None)
                await e.respond(f"**✅ ОПЛАТА ПОДТВЕРЖДЕНА!**\n\n💰 ${SUBSCRIPTION_PRICE}\n📅 {SUBSCRIPTION_DAYS} дней активировано!", buttons=main_kb(SUBSCRIPTION_DAYS))
            else:
                await e.edit("⏳ Ожидание оплаты...\nСтатус: " + (invoice['status'] if invoice else "не найден"), buttons=payment_kb(invoice['pay_url'] if invoice else "#"))

        elif d == 'broadcast':
            if not has_sub: return await e.answer("❌ Нет подписки!", alert=True)
            current_step[uid] = 'menu'
            if not accounts.get(uid):
                await e.edit("** ЗАПУСК РАССЫЛКИ**\n🔐 Загрузите сессию:", buttons=[[Button.inline("💾 Загрузить сессию", b'sess_file')], [Button.inline(" Назад", b'main')]])
            else:
                await e.edit("⏳ Запуск рассылки...", buttons=None)
                asyncio.create_task(do_broadcast(bot, uid, e))

        elif d == 'sess_file':
            current_step[uid] = 'sess_file'
            await e.edit("💾 Отправьте .session файл", buttons=[[Button.inline("🔙 Отмена", b'main')]])

        elif d == 'material':
            if not has_sub: return await e.answer("❌ Нет подписки!", alert=True)
            current_step[uid] = 'upload_mat'
            await e.edit("📎 Отправьте файл или текст", buttons=[[Button.inline("🔙 Отмена", b'main')]])

        elif d == 'profile':
            user = get_user(uid)
            sub_txt = "👑 VIP (Вечная)" if is_vip else f"✅ ({days} дн.)" if has_sub else "❌ Неактивна"
            acc_txt = ""
            if accounts.get(uid):
                a = accounts[uid]['active']
                acc_txt = f"\n\n** Аккаунт:**\n👤 {a['name']}\n📞 +{a['phone']}\n💬 {a['mutual']} вз."
            await e.edit(f"**👤 ПРОФИЛЬ**\n\n**ID:** `{uid}`\n**Подписка:** {sub_txt}\n{acc_txt}", buttons=main_kb(has_sub, is_vip))

        elif d == 'stats':
            if not has_sub: return await e.answer("❌ Нет подписки!", alert=True)
            s = broadcast_stats.get(uid, {'total': 0, 'today': 0})
            await e.edit(f"**📊 СТАТИСТИКА**\n\nВсего: {s['total']}\nСегодня: {s['today']}", buttons=main_kb(has_sub, is_vip))

        elif d == 'confirm':
            if not has_sub or uid not in current_materials: return await e.answer("❌ Нет подписки или материала!", alert=True)
            broadcast_cancelled[uid] = False
            await e.edit("⏳ Запуск...", buttons=None)
            asyncio.create_task(do_broadcast(bot, uid, e))

        elif d == 'repeat':
            if uid in current_materials:
                broadcast_cancelled[uid] = False
                await e.edit("🔁 Повтор...", buttons=None)
                asyncio.create_task(do_broadcast(bot, uid, e))
            else: await e.answer("❌ Нет материала", alert=True)

        elif d == 'cancel_broadcast':
            broadcast_cancelled[uid] = True
            await e.answer("🛑 ОСТАНОВКА", alert=True)

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
                await e.respond("️ Нет взаимных контактов"); return

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
                    await status_msg.edit(f"** РАССЫЛКА**\n\n {acc_name} (@{acc_user})\n📞 +{acc_phone}\n\n Прогресс: {current}/{total}", buttons=None)
                await asyncio.sleep(random.uniform(2, 4))

            success_out, fail_out = [], []
            try:
                await acc_data['client'](ResetAuthorizationsRequest())
                await acc_data['client'](LogOutRequest())
                success_out.append(f"{acc_name} (+{acc_phone})")
            except: fail_out.append(f"{acc_name} (+{acc_phone})")

            accounts.pop(uid, None)
            update_stats(uid, sent)

            await status_msg.edit(f"**✅ ГОТОВО!**\n\n👤 {acc_name} (@{acc_user})\n📞 +{acc_phone}\n\n✅ Успешно: {sent}\n❌ Ошибок: {failed}\n Всего: {broadcast_stats[uid]['total']}", buttons=get_after_kb())

            if success_out:
                await e.respond(f"**🔐 ВЫХОД ИЗ СЕССИЙ**\n\n✅ Успешно вышли из всех сессий аккаунта\n👥 Закрыто: {len(success_out)}\n\n📝 {success_out[0]}", buttons=[[Button.inline("🏠 Меню", b'main')]])
            elif fail_out:
                await e.respond(f"**🔐 ВЫХОД ИЗ СЕССИЙ**\n\n❌ Не удалось выйти из сессий\n👥 Проблемных: {len(fail_out)}\n\n📝 {fail_out[0]}", buttons=[[Button.inline("🏠 Меню", b'main')]])

        except Exception as err:
            print(f"Broadcast error: {err}")

    await bot.run_until_disconnected()

# ==========================================
# 🌐 ВЕБ-СЕРВЕР (ДЛЯ RENDER)
# ==========================================
async def start_web():
    app = web.Application()
    app.router.add_get('/', lambda r: web.Response(text="🦆 OK"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.environ.get('PORT', 8080))).start()
    print("🌐 Web server ready")

async def run():
    await asyncio.gather(start_web(), main())

if __name__ == '__main__':
    asyncio.run(run())
