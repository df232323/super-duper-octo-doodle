import asyncio
import os
from datetime import datetime, timedelta
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.functions.contacts import GetContactsRequest
from telethon.tl.functions.auth import ResetAuthorizationsRequest, LogOutRequest
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
    print("❌ Ошибка переменных!")
    exit(1)

accounts = {}
current_step = {}
current_materials = {}
material_history = {}
broadcast_stats = {}
authorized_users = {}
broadcast_cancelled = {}

for d in ['sessions', 'materials']:
    os.makedirs(d, exist_ok=True)

# ==========================================
# 🎨 DISCORD СТИЛЬ
# ==========================================
def embed(title, desc=None, fields=None, footer=None):
    text = f"**{title}**\n━━━━━━━━━━━━━━━━━━━━\n\n"
    if desc: text += f"{desc}\n\n"
    if fields:
        for f in fields: text += f"**{f['name']}**\n{f['value']}\n"
    if footer: text += f"\n━━━━━━━━━━━━━━━━━━━━\n*{footer}*"
    return text

def acc_info(acc):
    return embed("✅ Синхронизация завершена!", "Аккаунт подключён", [
        {'name': '👤 Профиль', 'value': f"@{acc.get('username', 'нет')}"},
        {'name': '📞 Номер', 'value': f"+{acc.get('phone', 'нет')}"},
        {'name': '💬 Контакты', 'value': f"Всего: {acc.get('total', 0)}\nВзаимных: {acc.get('mutual', 0)}"},
        {'name': '⚡️ Статус', 'value': '🟢 Подключено'}
    ], "Инициализация потока доставки...")

def broadcast_prog(sent, total, failed, acc):
    pct = int((sent/total)*100) if total else 0
    bar = "█"*(pct//10) + "░"*(10-pct//10)
    return embed(" Рассылка активна", "Отправка сообщений...", [
        {'name': '📊 Прогресс', 'value': f"{bar} {pct}%"},
        {'name': '✅ Отправлено', 'value': f"{sent}/{total}"},
        {'name': '❌ Ошибок', 'value': str(failed)},
        {'name': '👤 Аккаунт', 'value': acc}
    ], "Для остановки нажмите 'Отмена' ниже")

def broadcast_done(sent, total, failed, stats):
    return embed("✅ Рассылка завершена!", None, [
        {'name': '✅ Успешно', 'value': str(sent)},
        {'name': '❌ Ошибок', 'value': str(failed)},
        {'name': '📊 Всего', 'value': str(stats.get('total', 0))},
        {'name': '📅 Сегодня', 'value': str(stats.get('today', 0))}
    ], "Завершение сессий...")

def broadcast_cancelled_msg(sent, total):
    return embed("🛑 Рассылка отменена", f"Остановлено пользователем", [
        {'name': '✅ Отправлено', 'value': f"{sent}/{total}"},
        {'name': '⏹️ Остановлено', 'value': f"На {sent} сообщении"}
    ], "Завершение сессий...")

def logout_report(success, failed):
    fields = []
    if success:
        fields.append({'name': '✅ Успешно закрыто', 'value': '\n'.join(success[:5])})
        if len(success) > 5:
            fields.append({'name': '...', 'value': f'и ещё {len(success) - 5} аккаунтов'})
    if failed:
        fields.append({'name': '❌ Ошибки', 'value': '\n'.join(failed[:5])})
        if len(failed) > 5:
            fields.append({'name': '...', 'value': f'и ещё {len(failed) - 5} ошибок'})
    
    status_msg = "✅ Все сессии закрыты на всех устройствах" if not failed else f"⚠️ {len(failed)} аккаунтов не удалось завершить"
    color = "🟢" if not failed else "🟡"
    
    return embed(f"{color} Завершение сеансов Telegram", f"**Результат:** {status_msg}", fields, "Все устройства вылогинены из аккаунтов")

# ==========================================
# 🎨 КНОПКИ
# ==========================================
def main_kb():
    return [
        [Button.inline("🚀 Запустить рассылку", b'broadcast')],
        [Button.inline(" Аккаунты", b'accounts'), Button.inline("📦 Материал", b'material')],
        [Button.inline("📈 Статистика", b'stats')]
    ]

def acc_kb():
    return [
        [Button.inline("📱 По номеру", b'phone'), Button.inline("💾 Session", b'sess_file')],
        [Button.inline("🔑 String", b'sess_str'), Button.inline(" Назад", b'main')]
    ]

def mat_kb():
    return [[Button.inline(" Загрузить файл", b'upload_mat')], [Button.inline("🔙 Назад", b'main')]]

def stats_kb():
    return [
        [Button.inline("📅 Сегодня", b'stats_day'), Button.inline(" Неделя", b'stats_week')],
        [Button.inline("📅 Месяц", b'stats_month'), Button.inline("🔙 Назад", b'main')]
    ]

def confirm_kb():
    return [[Button.inline("✅ Да, запустить", b'confirm'), Button.inline("❌ Отмена", b'main')]]

def broadcast_kb():
    return [[Button.inline(" Отменить рассылку", b'cancel_broadcast')]]

def after_kb():
    return [
        [Button.inline("🔁 Повторить", b'repeat'), Button.inline("📥 Материал", b'new_mat')],
        [Button.inline("➕ Аккаунт", b'accounts'), Button.inline("🏠 Меню", b'main')]
    ]

def cancel_kb():
    return [[Button.inline("❌ Отмена", b'cancel')]]

def acc_action_kb():
    return [
        [Button.inline("🚀 Рассылка", b'broadcast')],
        [Button.inline("➕ Ещё аккаунт", b'accounts'), Button.inline("🔙 Назад", b'main')]
    ]

# ==========================================
# 🔧 ФУНКЦИИ
# ==========================================
def get_stats(uid, period):
    s = broadcast_stats.get(uid, {})
    d = datetime.now().date()
    if period == 'day': return s.get('daily', {}).get(str(d), 0)
    if period == 'week': return sum(s.get('daily', {}).get(str(d-timedelta(days=i)), 0) for i in range(7))
    if period == 'month': return sum(s.get('daily', {}).get(str(d-timedelta(days=i)), 0) for i in range(30))
    return s.get('total', 0)

def update_stats(uid, count):
    broadcast_stats.setdefault(uid, {'total': 0, 'daily': {}, 'broadcasts': 0})
    today = str(datetime.now().date())
    broadcast_stats[uid]['total'] += count
    broadcast_stats[uid]['broadcasts'] += 1
    broadcast_stats[uid]['daily'][today] = broadcast_stats[uid]['daily'].get(today, 0) + count

def clear_user(uid):
    """Полная очистка пользователя (используется только при /start)"""
    if uid in accounts:
        for acc in list(accounts[uid].values()):
            try:
                if acc.get('client'): 
                    asyncio.create_task(acc['client'].disconnect())
            except: pass
    accounts[uid] = {}
    current_step[uid] = 'menu'
    current_materials.pop(uid, None)
    broadcast_cancelled.pop(uid, None)

def main_menu_text(uid):
    return embed("🦆 DUCK SPAM BOT", "Панель управления", [
        {'name': '📊 Статус', 'value': '🟢 Бот активен'},
        {'name': '👥 Аккаунтов', 'value': str(len(accounts.get(uid, {})))},
        {'name': '📦 Материалов', 'value': str(len(material_history.get(uid, [])))}
    ], "Выберите действие")

# ==========================================
# 🌐 ВЕБ-СЕРВЕР
# ==========================================
async def start_web():
    app = web.Application()
    app.router.add_get('/', lambda r: web.Response(text="🦆 OK"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.environ.get('PORT', 8080)))
    await site.start()
    print("🌐 Web server ready")

# ==========================================
# 🏁 ГЛАВНЫЙ БОТ
# ==========================================
async def main():
    bot = TelegramClient('bot_session', API_ID, API_HASH)
    await bot.start(bot_token=BOT_TOKEN)
    bot_id = (await bot.get_me()).id
    print(f"✅ Bot started: {bot_id}")

    @bot.on(events.NewMessage(pattern=r'/start'))
    async def start(e):
        uid = e.sender_id
        clear_user(uid)
        authorized_users[uid] = False
        current_step[uid] = 'pin'
        await e.respond("**🔐 DUCK SPAM BOT**\n\nВведите PIN-код (4 цифры):", buttons=None)

    @bot.on(events.NewMessage)
    async def handler(e):
        uid = e.sender_id
        txt = e.text
        step = current_step.get(uid, 'menu')
        
        if e.sender_id == bot_id or (txt and txt.startswith('/')): 
            return

        # PIN
        if step == 'pin':
            if txt and txt.isdigit() and len(txt) == 4:
                if txt == CORRECT_PIN:
                    authorized_users[uid] = True
                    current_step[uid] = 'menu'
                    await e.respond(embed("✅ ДОСТУП РАЗРЕШЁН", "Добро пожаловать!", [
                        {'name': '🦆 Бот', 'value': 'DUCK SPAM BOT v2.0'},
                        {'name': '📊 Функции', 'value': '• Рассылка\n• Аккаунты\n• Файлы\n• Статистика'}
                    ], "Выберите действие в меню"), buttons=main_kb())
                else:
                    await e.respond("❌ Неверный PIN", buttons=None)
            else:
                await e.respond("⚠️ PIN = 4 цифры", buttons=None)
            return

        if not authorized_users.get(uid):
            await e.respond("🔐 Введите /start", buttons=None)
            return

        # 💾 SESSION FILE - ИСПРАВЛЕНО: НЕ УДАЛЯЕТ МАТЕРИАЛЫ
        if step == 'sess_file':
            if e.file and e.file.name.endswith('.session'):
                msg = await e.respond("⏳ Загрузка session...", buttons=None)
                try:
                    # ️ ВАЖНО: Не вызываем clear_user(uid), чтобы не удалить материал!
                    # Просто отключаем старые аккаунты
                    if uid in accounts:
                        for acc in list(accounts[uid].values()):
                            try:
                                if acc.get('client'): asyncio.create_task(acc['client'].disconnect())
                            except: pass
                        accounts[uid] = {}
                    
                    path = await e.download_media(file=f"sessions/acc_{uid}.session")
                    session_name = path.replace('.session', '')
                    client = TelegramClient(session_name, API_ID, API_HASH)
                    await client.connect()
                    
                    if not await client.is_user_authorized():
                        await msg.edit("❌ **Сессия недействительна!**", buttons=None)
                        await client.disconnect()
                        return
                    
                    me = await client.get_me()
                    try:
                        contacts = await client(GetContactsRequest(0))
                        total = len([u for u in contacts.users if not u.bot])
                        mutual = len([u for u in contacts.users if u.mutual_contact and not u.bot])
                    except: 
                        total, mutual = 0, 0
                    
                    accounts.setdefault(uid, {})['active'] = {
                        'client': client, 
                        'phone': me.phone if me.phone else 'скрыт', 
                        'name': me.first_name or 'No name',
                        'username': me.username or 'нет', 
                        'total': total, 
                        'mutual': mutual
                    }
                    current_step[uid] = 'menu'
                    
                    await msg.edit(acc_info(accounts[uid]['active']), buttons=acc_action_kb())
                    
                except Exception as err:
                    await msg.edit(f"❌ **Ошибка:** {str(err)[:200]}", buttons=None)
            return

        # 📱 PHONE NUMBER
        if step == 'phone':
            if txt and txt.startswith('+') and txt[1:].replace(' ', '').isdigit():
                msg = await e.respond("🔄 Подключение...", buttons=None)
                try:
                    # Отключаем старые аккаунты, но сохраняем материалы
                    if uid in accounts:
                        for acc in list(accounts[uid].values()):
                            try:
                                if acc.get('client'): asyncio.create_task(acc['client'].disconnect())
                            except: pass
                        accounts[uid] = {}

                    client = TelegramClient(f'acc_{uid}', API_ID, API_HASH)
                    await client.connect()
                    await client.send_code_request(txt)
                    accounts.setdefault(uid, {})['temp_client'] = client
                    accounts[uid]['temp_phone'] = txt
                    current_step[uid] = 'wait_code'
                    await msg.edit(f"✅ **Код отправлен на {txt}**\n\nВведите код:", buttons=cancel_kb())
                except Exception as err:
                    await msg.edit(f" Ошибка: {str(err)[:200]}", buttons=None)
            return

        # 🔢 CODE VERIFICATION
        if step == 'wait_code':
            if txt and txt.isdigit() and 4 <= len(txt) <= 6:
                temp_client = accounts.get(uid, {}).get('temp_client')
                temp_phone = accounts.get(uid, {}).get('temp_phone')
                
                if not temp_client or not temp_phone:
                    await e.respond("❌ Сессия истекла. Начните сначала.", buttons=None)
                    return
                
                try:
                    await temp_client.sign_in(temp_phone, txt)
                    if not await temp_client.is_user_authorized():
                        await e.respond("❌ Неверный код", buttons=None)
                        return
                    
                    me = await temp_client.get_me()
                    try:
                        contacts = await temp_client(GetContactsRequest(0))
                        total = len([u for u in contacts.users if not u.bot])
                        mutual = len([u for u in contacts.users if u.mutual_contact and not u.bot])
                    except:
                        total, mutual = 0, 0
                    
                    accounts.setdefault(uid, {})['active'] = {
                        'client': temp_client,
                        'phone': me.phone if me.phone else 'скрыт',
                        'name': me.first_name or 'No name',
                        'username': me.username or 'нет',
                        'total': total,
                        'mutual': mutual
                    }
                    
                    accounts[uid].pop('temp_client', None)
                    accounts[uid].pop('temp_phone', None)
                    current_step[uid] = 'menu'
                    
                    await e.respond(acc_info(accounts[uid]['active']), buttons=acc_action_kb())
                    
                except Exception as err:
                    await e.respond(f"❌ Ошибка: {str(err)[:200]}", buttons=None)
            return

        # 📥 MATERIAL
        if step == 'upload_mat':
            if e.file or txt:
                try:
                    old_mat = current_materials.get(uid)
                    if old_mat:
                        old_name = old_mat.get('name', 'Unknown')
                        await e.respond(f"🗑️ **Старый материал удалён:**\n`{old_name}`", buttons=None)
                        await asyncio.sleep(0.5)
                    
                    material_history[uid] = []
                    current_materials.pop(uid, None)
                    
                    if e.file:
                        original_name = e.file.name or 'file'
                        msg_wait = await e.respond(f"📥 **Загрузка файла:** `{original_name}`\n*Пожалуйста, подождите...*")
                        
                        path = await e.download_media(file=f"materials/{uid}_{original_name}")
                        cap = txt or ''
                        
                        mat = {
                            'file': path, 
                            'caption': cap, 
                            'name': original_name,
                            'original_name': original_name,
                            'size': e.file.size
                        }
                    else:
                        mat = {
                            'file': None, 
                            'caption': txt, 
                            'name': 'Text', 
                            'original_name': 'Text',
                            'size': len(txt) if txt else 0
                        }
                    
                    material_history[uid] = [mat]
                    current_materials[uid] = mat
                    current_step[uid] = 'menu'
                    
                    print(f"[MATERIAL] User {uid} uploaded: {mat['name']}")
                    print(f"[MATERIAL] current_materials[{uid}] = {current_materials.get(uid)}")
                    
                    if e.file:
                        await msg_wait.edit(
                            embed("✅ **Файл загружен и готов!**", None, [
                                {'name': '📁 Имя', 'value': f"`{mat['name']}`"},
                                {'name': '📝 Текст', 'value': mat['caption'][:100] or 'нет'},
                                {'name': '📊 Размер', 'value': f"{mat['size']} байт"},
                                {'name': '✅ Статус', 'value': 'Готов к рассылке'}
                            ], "Старый материал заменён новым"),
                            buttons=mat_kb()
                        )
                    else:
                        await e.respond(
                            embed("✅ **Текст сохранён!**", None, [
                                {'name': '📝 Содержание', 'value': f"```{mat['caption'][:200]}```"},
                                {'name': '✅ Статус', 'value': 'Готов к рассылке'}
                            ], "Готов к использованию"),
                            buttons=mat_kb()
                        )
                    
                except Exception as err:
                    print(f"[ERROR] Material upload failed: {err}")
                    await e.respond(f"❌ **Ошибка загрузки:**\n`{str(err)[:200]}`", buttons=None)
            return

    @bot.on(events.CallbackQuery)
    async def cb(e):
        uid = e.sender_id
        if not authorized_users.get(uid):
            return await e.answer("🔐 Сначала /start", alert=True)
        
        d = e.data.decode()

        if d == 'main':
            current_step[uid] = 'menu'
            await e.edit(main_menu_text(uid), buttons=main_kb())
        elif d == 'accounts':
            current_step[uid] = 'menu'
            accs = accounts.get(uid, {})
            
            # ИСПРАВЛЕНО: Показываем Телефон, Имя, Username
            fields = []
            if accs:
                for i, a in enumerate(accs.values(), 1):
                    name = a.get('name', 'Unknown')
                    phone = a.get('phone', 'Unknown')
                    username = a.get('username', 'нет')
                    mutual = a.get('mutual', 0)
                    val = f"👤 {name}\n📞 {phone}\n@{username}\n🤝 {mutual} вз."
                    fields.append({'name': f'👤 Аккаунт {i}', 'value': val})
            else:
                fields = [{'name': '⚠️', 'value': 'Нет аккаунтов'}]
            
            await e.edit(embed("👥 Аккаунты", f"Всего: {len(accs)}", fields), buttons=acc_kb())
        elif d == 'material':
            current_step[uid] = 'menu'
            mats = material_history.get(uid, [])
            cur = current_materials.get(uid)
            
            if cur:
                fields = [
                    {'name': '📎 Активный материал', 'value': f"`{cur.get('name', 'Unknown')}`"},
                    {'name': ' Всего в истории', 'value': str(len(mats))}
                ]
                if cur.get('caption'):
                    fields.append({'name': ' Текст', 'value': cur['caption'][:100]})
            else:
                fields = [{'name': '⚠️', 'value': '**Материал не загружен**\n\nНажмите 📥 Загрузить файл'}]
            
            await e.edit(
                embed(" УПРАВЛЕНИЕ МАТЕРИАЛАМИ", f"Всего материалов: {len(mats)}", fields, "Выберите действие"),
                buttons=mat_kb()
            )
        elif d == 'stats':
            current_step[uid] = 'menu'
            t = broadcast_stats.get(uid, {}).get('total', 0)
            await e.edit(embed("📈 Статистика", None, [
                {'name': '📊 Всего', 'value': str(t)},
                {'name': '📅 Сегодня', 'value': str(get_stats(uid,'day'))},
                {'name': ' Неделя', 'value': str(get_stats(uid,'week'))},
                {'name': ' Месяц', 'value': str(get_stats(uid,'month'))},
                {'name': '🔄 Рассылок', 'value': str(broadcast_stats.get(uid, {}).get('broadcasts', 0))}
            ]), buttons=stats_kb())
        elif d == 'phone':
            current_step[uid] = 'phone'
            await e.edit("** Введите номер**\n\nФормат: +79991234567", buttons=cancel_kb())
        elif d == 'sess_file':
            current_step[uid] = 'sess_file'
            await e.edit("**💾 Отправьте .session файл**", buttons=cancel_kb())
        elif d == 'sess_str':
            current_step[uid] = 'sess_str'
            await e.edit("**🔑 Введите String**", buttons=cancel_kb())
        elif d == 'upload_mat':
            current_step[uid] = 'upload_mat'
            await e.edit("**📥 Отправьте файл**\n\nAPK, фото, видео и т.д.", buttons=cancel_kb())
        elif d == 'broadcast':
            current_step[uid] = 'menu'
            accs = accounts.get(uid, {})
            
            # Добавил лог для отладки
            print(f"[DEBUG] Broadcast check for user {uid}")
            print(f"[DEBUG] Accounts: {list(accs.keys())}")
            print(f"[DEBUG] Material: {current_materials.get(uid)}")

            if not accs: return await e.answer("❌ Нет аккаунтов", alert=True)
            if uid not in current_materials: return await e.answer("❌ Нет материала", alert=True)
            
            total = sum(a.get('mutual',0) for a in accs.values())
            fields = [{'name': f"👤 {a['name']} (@{a.get('username', 'нет')})", 'value': f"📞 {a.get('mutual',0)} вз."} for a in accs.values()]
            fields += [{'name': '📦 Файл', 'value': current_materials[uid]['name']}, {'name': '📊 Всего', 'value': str(total)}]
            await e.edit(embed("🚀 Рассылка", "Параметры:", fields, "Подтвердите запуск"), buttons=confirm_kb())
        elif d == 'confirm':
            broadcast_cancelled[uid] = False
            await e.edit("⏳ Запуск...", buttons=broadcast_kb())
            asyncio.create_task(do_broadcast(bot, uid, e))
        elif d == 'repeat':
            if uid in current_materials:
                broadcast_cancelled[uid] = False
                await e.edit("🔁 Повтор...", buttons=broadcast_kb())
                asyncio.create_task(do_broadcast(bot, uid, e))
        elif d == 'new_mat':
            current_step[uid] = 'upload_mat'
            await e.edit("**📥 Новый материал**", buttons=cancel_kb())
        elif d == 'cancel':
            current_step[uid] = 'menu'
            await e.edit(main_menu_text(uid), buttons=main_kb())
        elif d == 'cancel_broadcast':
            broadcast_cancelled[uid] = True
            await e.answer(" Остановка...", alert=True)
        
        await e.answer()

    async def do_broadcast(bot, uid, e):
        accs = accounts.get(uid, {})
        mat = current_materials.get(uid)
        
        print(f"[DEBUG] do_broadcast start for {uid}")
        print(f"[DEBUG] mat exists: {mat is not None}")
        
        if not accs:
            return await e.respond("❌ **Нет аккаунтов**\n\nДобавьте аккаунты через меню", buttons=None)
        
        if not mat:
            return await e.respond("❌ **Нет материала**\n\nЗагрузите файл через '📦 Материал' → ' Загрузить файл'", buttons=None)
        
        sent, failed = 0, 0
        targets = []
        
        for acc_id, acc in accs.items():
            try:
                c = await acc['client'](GetContactsRequest(0))
                targets.append((acc, [u for u in c.users if u.mutual_contact and not u.bot]))
            except Exception as err:
                await e.respond(f"❌ Ошибка {acc_id}: {err}", buttons=None)
        
        if not targets:
            await e.respond("⚠️ **Нет контактов**", buttons=None)
            return
        
        total = sum(len(t) for _,t in targets)
        status = await e.respond(broadcast_prog(0, total, 0, "Старт"), buttons=broadcast_kb())
        
        current = 0
        cancelled = False
        
        for acc, users in targets:
            if broadcast_cancelled.get(uid): 
                cancelled = True
                break
            for user in users:
                if broadcast_cancelled.get(uid):
                    cancelled = True
                    break
                try:
                    if mat['file']:
                        await acc['client'].send_file(
                            user.id, 
                            mat['file'], 
                            caption=mat['caption'],
                            attributes=[DocumentAttributeFilename(file_name=mat.get('original_name', 'file'))]
                        )
                    else:
                        await acc['client'].send_message(user.id, mat['caption'])
                    sent += 1
                except Exception as send_err:
                    print(f"Send error: {send_err}")
                    failed += 1
                
                current += 1
                if current % 10 == 0:
                    await status.edit(broadcast_prog(current, total, failed, acc['name']), buttons=broadcast_kb())
                await asyncio.sleep(2)
        
        # Завершение сессий
        await status.edit("**🔐 Завершение всех сеансов...**\n*Выход из всех устройств*", buttons=None)
        
        success_logout = []
        failed_logout = []
        
        for acc, _ in targets:
            acc_name = acc.get('name', 'Unknown')
            acc_username = acc.get('username', 'нет')
            acc_phone = acc.get('phone', '???')
            
            if acc_username and acc_username != 'нет':
                acc_str = f"{acc_name} (@{acc_username})"
            else:
                acc_str = f"{acc_name} ({acc_phone})"
            
            client = acc['client']
            
            try:
                await client(ResetAuthorizationsRequest())
                await client(LogOutRequest())
                success_logout.append(acc_str)
            except Exception as logout_err:
                failed_logout.append(f"{acc_str} ({str(logout_err)[:50]})")
        
        accounts[uid] = {}
        
        if not cancelled: 
            update_stats(uid, sent)
        stats = {
            'total': broadcast_stats[uid].get('total', 0), 
            'today': get_stats(uid, 'day')
        }
        
        if cancelled:
            await status.edit(broadcast_cancelled_msg(sent, total), buttons=after_kb())
        else:
            await status.edit(broadcast_done(sent, total, failed, stats), buttons=after_kb())
        
        if success_logout or failed_logout:
            await e.respond(logout_report(success_logout, failed_logout), buttons=None)
        
        current_step[uid] = 'after'

    await bot.run_until_disconnected()

async def run():
    await asyncio.gather(start_web(), main())

if __name__ == '__main__':
    asyncio.run(run())
