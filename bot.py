import asyncio
import os
import traceback
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

if not all([BOT_TOKEN, API_ID, API_HASH]):
    print("❌ Ошибка: Не все переменные заданы!")
    exit(1)

accounts = {}
current_step = {}
current_materials = {}
material_history = {}
broadcast_stats = {}
authorized_users = {}

for d in ['sessions', 'materials']:
    os.makedirs(d, exist_ok=True)

# ==========================================
# 🎨 КНОПКИ
# ==========================================
def main_kb():
    return [
        [Button.inline("🚀 Запустить рассылку", b'broadcast')],
        [Button.inline("👥 Аккаунты", b'accounts'), Button.inline("📦 Материал", b'material')],
        [Button.inline("📈 Статистика", b'stats')]
    ]

def acc_kb():
    return [
        [Button.inline("📱 По номеру", b'phone'), Button.inline("💾 Session", b'sess_file')],
        [Button.inline("🔑 String", b'sess_str'), Button.inline("🔙 Назад", b'main')]
    ]

def mat_kb():
    return [[Button.inline("📥 Загрузить", b'upload_mat')], [Button.inline("🔙 Назад", b'main')]]

def stats_kb():
    return [
        [Button.inline("📅 Сегодня", b'stats_day'), Button.inline("📅 Неделя", b'stats_week')],
        [Button.inline("📅 Месяц", b'stats_month'), Button.inline("🔙 Назад", b'main')]
    ]

def confirm_kb(): return [[Button.inline("✅ Да", b'confirm'), Button.inline("❌ Нет", b'main')]]
def after_kb(): return [[Button.inline("🔁 Повтор", b'repeat'), Button.inline("🏠 Меню", b'main')]]
def cancel_kb(): return [[Button.inline("❌ Отмена", b'cancel')]]
def acc_info_kb():
    return [
        [Button.inline("🚀 Запустить рассылку", b'broadcast')],
        [Button.inline("➕ Ещё аккаунт", b'accounts')],
        [Button.inline("🔙 Назад", b'main')]
    ]

# ==========================================
# 🔧 ФУНКЦИИ
# ==========================================
def format_acc_info(acc):
    return (f"**✅ Синхронизация завершена!**\n\n"
            f"**👤 Профиль:** @{acc.get('username', 'нет')}\n"
            f"**📞 Номер:** {acc.get('phone', 'нет')}\n"
            f"**💬 Всего контактов:** {acc.get('total', 0)}\n"
            f"**✅ Взаимных:** {acc.get('mutual', 0)}\n"
            f"**⚡️ Статус:** Подключен")

def get_stats(uid, period):
    s = broadcast_stats.get(uid, {})
    d = datetime.now().date()
    if period == 'day': return s.get('daily', {}).get(str(d), 0)
    if period == 'week': return sum(s.get('daily', {}).get(str(d - timedelta(days=i)), 0) for i in range(7))
    if period == 'month': return sum(s.get('daily', {}).get(str(d - timedelta(days=i)), 0) for i in range(30))
    return s.get('total', 0)

def update_stats(uid, count):
    broadcast_stats.setdefault(uid, {'total': 0, 'daily': {}})
    today = str(datetime.now().date())
    broadcast_stats[uid]['total'] += count
    broadcast_stats[uid]['daily'][today] = broadcast_stats[uid]['daily'].get(today, 0) + count

def clear_user_data(uid):
    """Очистка данных пользователя"""
    print(f"🧹 [USER {uid}] Очистка данных...")
    if uid in accounts:
        for acc_id, acc_data in list(accounts[uid].items()):
            try:
                if acc_data.get('client'):
                    asyncio.create_task(acc_data['client'].disconnect())
                    print(f"  ❌ [USER {uid}] Отключен аккаунт {acc_id}")
            except Exception as e:
                print(f"  ⚠️ [USER {uid}] Ошибка отключения {acc_id}: {e}")
    accounts[uid] = {}
    current_step[uid] = 'menu'
    current_materials.pop(uid, None)
    print(f"  ✅ [USER {uid}] Данные очищены")

# ==========================================
# 🌐 ВЕБ-СЕРВЕР
# ==========================================
async def start_web():
    app = web.Application()
    app.router.add_get('/', lambda r: web.Response(text="🦆 OK"))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.environ.get('PORT', 8080))).start()
    print("🌐 Web server ready")

# ==========================================
# 🏁 ГЛАВНАЯ ЛОГИКА
# ==========================================
async def main():
    bot = TelegramClient('bot_session', API_ID, API_HASH)
    await bot.start(bot_token=BOT_TOKEN)
    me = await bot.get_me()
    print(f"✅ {me.first_name} запущен! (ID: {me.id})")

    @bot.on(events.NewMessage(pattern=r'/start'))
    async def start_cmd(e):
        uid = e.sender_id
        print(f"📩 [USER {uid}] Команда /start")
        clear_user_data(uid)
        authorized_users[uid] = False
        current_step[uid] = 'pin'
        await e.respond("**🔐 DUCK SPAM BOT**\n\nВведите PIN-код (4 цифры):", buttons=Button.clear())

    @bot.on(events.NewMessage)
    async def handler(e):
        uid, txt = e.sender_id, e.text
        step = current_step.get(uid, 'menu')
        
        if (txt and txt.startswith('/')) or e.sender_id == me.id:
            return

        print(f"📨 [USER {uid}] Шаг: {step}, Текст: {txt[:30] if txt else 'file'}")

        # 🔐 PIN
        if step == 'pin':
            if txt and txt.isdigit() and len(txt) == 4:
                if txt == CORRECT_PIN:
                    authorized_users[uid] = True
                    current_step[uid] = 'menu'
                    print(f"✅ [USER {uid}] Успешная авторизация")
                    await e.respond("**✅ Доступ разрешён**\n\n**🦆 DUCK SPAM BOT**\n*Панель управления*", buttons=main_kb())
                else:
                    await e.respond("❌ Неверный PIN", buttons=Button.clear())
            return

        if not authorized_users.get(uid):
            current_step[uid] = 'pin'
            await e.respond("🔐 Введите /start", buttons=Button.clear())
            return

        # 📱 НОМЕР
        if step == 'phone':
            if txt and txt.startswith('+') and txt[1:].isdigit():
                current_step[uid] = 'code'
                client = TelegramClient(f'acc_{uid}_{len(accounts.get(uid,{}))}', API_ID, API_HASH)
                await client.connect()
                await client.send_code_request(txt)
                accounts.setdefault(uid, {})[f'a{len(accounts[uid])+1}'] = {'client': client, 'phone': txt}
                await e.respond(f"📨 Код отправлен на `{txt}`", buttons=cancel_kb())
            return

        # 🔢 КОД
        if step == 'code':
            if txt and txt.isdigit() and 4 <= len(txt) <= 6:
                accs = accounts.get(uid, {})
                if not accs: return
                last = list(accs.values())[-1]
                try:
                    await last['client'].sign_in(last['phone'], txt)
                    me_acc = await last['client'].get_me()
                    contacts = await last['client'](GetContactsRequest(0))
                    total = len([u for u in contacts.users if not u.bot])
                    mutual = len([u for u in contacts.users if u.mutual_contact and not u.bot])
                    last.update({'client': last['client'], 'phone': me_acc.phone, 'name': me_acc.first_name,
                                 'username': me_acc.username or 'нет', 'total': total, 'mutual': mutual})
                    current_step[uid] = 'menu'
                    await e.respond(format_acc_info(last), buttons=acc_info_kb())
                    print(f"✅ [USER {uid}] Аккаунт добавлен: {me_acc.first_name}")
                except Exception as err:
                    print(f"❌ [USER {uid}] Ошибка входа по коду: {err}")
                    traceback.print_exc()
                    await e.respond(f"❌ Ошибка: {err}", buttons=Button.clear())
            return

        # 💾 SESSION ФАЙЛ — ДЕТАЛЬНОЕ ЛОГИРОВАНИЕ
        if step == 'sess_file':
            if e.file and e.file.name.endswith('.session'):
                print(f"💾 [USER {uid}] Получен session файл: {e.file.name}")
                clear_user_data(uid)
                
                msg = await e.respond("⏳ **Загрузка session файла...**\n*Старые сессии удалены*", buttons=Button.clear())
                
                try:
                    # Шаг 1: Скачивание
                    print(f"⏳ [USER {uid}] Шаг 1: Скачивание файла...")
                    await msg.edit("⏳ **Шаг 1/4: Скачивание файла...**")
                    session_path = await e.download_media(
                        file=f"sessions/acc_{uid}_new.session"
                    )
                    print(f"✅ [USER {uid}] Файл скачан: {session_path}")
                    
                    # Шаг 2: Подключение
                    print(f"🔄 [USER {uid}] Шаг 2: Подключение к сессии...")
                    await msg.edit("🔄 **Шаг 2/4: Подключение к сессии...**")
                    session_name = session_path.replace('.session', '')
                    client = TelegramClient(session_name, API_ID, API_HASH)
                    
                    try:
                        await asyncio.wait_for(client.connect(), timeout=10.0)
                        print(f"✅ [USER {uid}] Сессия подключена")
                    except asyncio.TimeoutError:
                        raise Exception("⏱️ Превышено время подключения. Сессия может быть недействительна.")
                    except Exception as conn_err:
                        raise Exception(f"❌ Ошибка подключения: {conn_err}")
                    
                    # Шаг 3: Получение информации
                    print(f"📱 [USER {uid}] Шаг 3: Получение информации об аккаунте...")
                    await msg.edit("📱 **Шаг 3/4: Получение информации...**")
                    try:
                        me_acc = await asyncio.wait_for(client.get_me(), timeout=10.0)
                        print(f"✅ [USER {uid}] Информация получена: {me_acc.first_name}")
                    except asyncio.TimeoutError:
                        raise Exception("⏱️ Превышено время получения информации.")
                    
                    # Шаг 4: Подсчёт контактов
                    print(f"📊 [USER {uid}] Шаг 4: Подсчёт контактов...")
                    await msg.edit("📊 **Шаг 4/4: Подсчёт контактов...**")
                    try:
                        contacts = await asyncio.wait_for(client(GetContactsRequest(0)), timeout=15.0)
                        total = len([u for u in contacts.users if not u.bot])
                        mutual = len([u for u in contacts.users if u.mutual_contact and not u.bot])
                        print(f"✅ [USER {uid}] Контактов: {total}, Взаимных: {mutual}")
                    except asyncio.TimeoutError:
                        total = 0
                        mutual = 0
                        print(f"⚠️ [USER {uid}] Таймаут при подсчёте контактов")
                    except Exception as e:
                        total = 0
                        mutual = 0
                        print(f"⚠️ [USER {uid}] Ошибка подсчёта контактов: {e}")
                    
                    # Сохранение аккаунта
                    accounts.setdefault(uid, {})['active'] = {
                        'client': client, 
                        'phone': me_acc.phone, 
                        'name': me_acc.first_name or 'Без имени',
                        'username': me_acc.username or 'нет', 
                        'total': total, 
                        'mutual': mutual
                    }
                    
                    current_step[uid] = 'menu'
                    print(f"✅ [USER {uid}] Session успешно загружена!")
                    
                    await msg.edit(
                        format_acc_info(accounts[uid]['active']), 
                        buttons=acc_info_kb()
                    )
                    
                except Exception as err:
                    error_trace = traceback.format_exc()
                    print(f"❌ [USER {uid}] КРИТИЧЕСКАЯ ОШИБКА:\n{error_trace}")
                    error_msg = str(err)[:300]
                    await msg.edit(
                        f"**❌ ОШИБКА ЗАГРУЗКИ SESSION!**\n\n"
                        f"**Детали:**\n`{error_msg}`\n\n"
                        f"Возможные причины:\n"
                        f"• Сессия устарела или недействительна\n"
                        f"• Неверный API_ID или API_HASH\n"
                        f"• Проблемы с сетью\n\n"
                        f"Попробуйте другую сессию.",
                        buttons=Button.clear()
                    )
            return

        # 🔑 STRING
        if step == 'sess_str':
            if txt and txt.startswith('1') and len(txt) > 100:
                print(f"🔑 [USER {uid}] Получен session string")
                clear_user_data(uid)
                
                msg = await e.respond("🔄 **Подключение...**", buttons=Button.clear())
                try:
                    client = TelegramClient(StringSession(txt), API_ID, API_HASH)
                    await client.connect()
                    me_acc = await client.get_me()
                    contacts = await client(GetContactsRequest(0))
                    total = len([u for u in contacts.users if not u.bot])
                    mutual = len([u for u in contacts.users if u.mutual_contact and not u.bot])
                    
                    accounts.setdefault(uid, {})['active'] = {
                        'client': client, 'phone': me_acc.phone, 'name': me_acc.first_name,
                        'username': me_acc.username or 'нет', 'total': total, 'mutual': mutual}
                    current_step[uid] = 'menu'
                    await msg.edit(format_acc_info(accounts[uid]['active']), buttons=acc_info_kb())
                    print(f"✅ [USER {uid}] String загружен: {me_acc.first_name}")
                except Exception as err:
                    print(f"❌ [USER {uid}] Ошибка string: {err}")
                    await msg.edit(f"❌ Ошибка: {err}", buttons=Button.clear())
            return

        # 📥 МАТЕРИАЛ
        if step == 'upload_mat':
            if e.file:
                original_name = e.file.name
                path = await e.download_media(file=f"materials/mat_{uid}_{original_name}")
                cap = txt or ''
                mat = {
                    'file': path, 
                    'caption': cap, 
                    'name': original_name,
                    'original_name': original_name
                }
                material_history.setdefault(uid, []).append(mat)
                current_materials[uid] = mat
                current_step[uid] = 'menu'
                await e.respond(f"**✅ Сохранено:** {original_name}\n📝 Текст: {cap[:50] or 'нет'}", buttons=mat_kb())
            elif txt:
                mat = {'file': None, 'caption': txt, 'name': 'Text', 'original_name': 'Text'}
                material_history.setdefault(uid, []).append(mat)
                current_materials[uid] = mat
                current_step[uid] = 'menu'
                await e.respond(f"**✅ Текст сохранён**\n📝 {txt[:100]}", buttons=mat_kb())
            return

    @bot.on(events.CallbackQuery)
    async def cb(e):
        uid = e.sender_id
        if not authorized_users.get(uid):
            return await e.answer("🔐 Введите /start", alert=True)
        
        d = e.data.decode()
        print(f"🔘 [USER {uid}] Callback: {d}")

        if d == 'main':
            current_step[uid] = 'menu'
            await e.edit("**🦆 DUCK SPAM BOT**\n*Панель*", buttons=main_kb())
        elif d == 'accounts':
            accs = accounts.get(uid, {})
            txt = f"**👥 Аккаунты:** {len(accs)}\n\n"
            for i, a in enumerate(accs.values(), 1): txt += f"{i}. {a['name']} ({a.get('mutual',0)} вз.)\n"
            await e.edit(txt or "*Пусто*", buttons=acc_kb())
        elif d == 'material':
            mats = material_history.get(uid, [])
            txt = f"**📦 Материалы:** {len(mats)}\n"
            if current_materials.get(uid): txt += f"📎 Активен: {current_materials[uid]['name']}"
            await e.edit(txt or "*Пусто*", buttons=mat_kb())
        elif d == 'stats':
            t = broadcast_stats.get(uid, {}).get('total', 0)
            await e.edit(f"**📈 Статистика**\n📊 Всего: {t}\n📅 Сегодня: {get_stats(uid,'day')}\n📅 Неделя: {get_stats(uid,'week')}\n📅 Месяц: {get_stats(uid,'month')}", buttons=stats_kb())
        elif d.startswith('stats_'):
            p = d.split('_')[1]
            await e.answer(f"📅 {p.capitalize()}: {get_stats(uid, p)}", alert=True)
        elif d == 'phone': current_step[uid]='phone'; await e.edit("**📱 Номер:** (+7...)", buttons=cancel_kb())
        elif d == 'sess_file': current_step[uid]='sess_file'; await e.edit("**💾 Отправьте .session**\n*⚠️ Старая сессия будет удалена*", buttons=cancel_kb())
        elif d == 'sess_str': current_step[uid]='sess_str'; await e.edit("**🔑 Введите String**\n*⚠️ Старая сессия будет удалена*", buttons=cancel_kb())
        elif d == 'upload_mat': current_step[uid]='upload_mat'; await e.edit("**📥 Файл или текст**", buttons=cancel_kb())
        elif d == 'broadcast':
            accs = accounts.get(uid, {})
            if not accs: return await e.answer("❌ Нет аккаунтов", alert=True)
            if uid not in current_materials: return await e.answer("❌ Нет материала", alert=True)
            total_mut = sum(a.get('mutual',0) for a in accs.values())
            txt = f"**🚀 Рассылка**\n\n"
            for a in accs.values(): txt += f"👤 {a['name']} | 📞 {a.get('mutual',0)} вз.\n"
            txt += f"\n📦 Материал: {current_materials[uid]['name']}\n📊 Всего контактов: {total_mut}\n\n**Запустить?**"
            await e.edit(txt, buttons=confirm_kb())
        elif d == 'confirm':
            await e.edit("⏳ **Запуск...**", buttons=Button.clear())
            await do_broadcast(bot, uid, e)
        elif d == 'repeat':
            if uid in current_materials:
                await e.edit("🔁 **Повтор...**", buttons=Button.clear())
                await do_broadcast(bot, uid, e)
            else: await e.answer("❌ Нет материала", alert=True)
        elif d == 'cancel':
            current_step[uid] = 'menu'
            await e.edit("**❌ Отмена**", buttons=main_kb())
        await e.answer()

    # 🚀 РАССЫЛКА
    async def do_broadcast(bot, uid, e):
        accs = accounts.get(uid, {})
        mat = current_materials.get(uid)
        if not accs or not mat: return
        
        sent, failed = 0, 0
        targets = []
        for a in accs.values():
            try:
                c = await a['client'](GetContactsRequest(0))
                targets.extend([(a, u) for u in c.users if u.mutual_contact and not u.bot])
            except Exception as err:
                await e.respond(f"❌ Ошибка аккаунта: {err}", buttons=Button.clear())
        
        if not targets:
            await e.respond("⚠️ Нет взаимных контактов", buttons=Button.clear())
            return
        
        total = len(targets)
        status_msg = await e.respond(f"**🚀 Рассылка запущена**\n📊 Прогресс: 0/{total}", buttons=Button.clear())
        
        for acc, user in targets:
            try:
                if mat['file']:
                    original_name = mat.get('original_name', mat.get('name', 'file'))
                    await acc['client'].send_file(
                        user.id, 
                        mat['file'], 
                        caption=mat['caption'],
                        attributes=[DocumentAttributeFilename(file_name=original_name)]
                    )
                else:
                    await acc['client'].send_message(user.id, mat['caption'])
                sent += 1
            except: 
                failed += 1
            
            if sent % 10 == 0:
                await status_msg.edit(f"**🚀 Рассылка**\n✅ Отправлено: {sent}/{total}\n❌ Ошибок: {failed}", buttons=Button.clear())
            await asyncio.sleep(2)
        
        update_stats(uid, sent)
        await status_msg.edit(f"**✅ Готово!**\n✅ Успешно: {sent}\n❌ Ошибок: {failed}\n📊 Всего: {broadcast_stats[uid]['total']}", buttons=after_kb())
        current_step[uid] = 'after'

    await bot.run_until_disconnected()

async def run():
    await asyncio.gather(start_web(), main())

if __name__ == '__main__':
    asyncio.run(run())
