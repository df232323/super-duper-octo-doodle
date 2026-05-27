import asyncio
import os
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
# 🎨 DISCORD СТИЛЬ И ФОРМАТИРОВАНИЕ
# ==========================================
def discord_embed(title, description, fields=None, footer=None, color="🟦"):
    """Создаёт красивое сообщение в стиле Discord"""
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
    """Информация об аккаунте в Discord стиле"""
    fields = [
        {'name': '👤 Профиль', 'value': f"@{acc.get('username', 'нет')}"},
        {'name': '📞 Номер', 'value': f"+{acc.get('phone', 'нет')}"},
        {'name': '💬 Всего контактов', 'value': str(acc.get('total', 0))},
        {'name': '✅ Взаимных контактов', 'value': str(acc.get('mutual', 0))},
        {'name': '⚡️ Статус', 'value': '🟢 Подключение стабильно'}
    ]
    return discord_embed(
        "✅ Синхронизация завершена!",
        "Аккаунт успешно подключён к системе",
        fields=fields,
        footer="Инициализация потока доставки..."
    )

def broadcast_progress(sent, total, failed, acc_name, mutual_count):
    """Прогресс рассылки в Discord стиле"""
    percentage = int((sent / total) * 100) if total > 0 else 0
    progress_bar = "█" * (percentage // 10) + "░" * (10 - percentage // 10)
    
    fields = [
        {'name': '📊 Прогресс', 'value': f"{progress_bar} {percentage}%"},
        {'name': '✅ Отправлено', 'value': f"{sent}/{total}"},
        {'name': '❌ Ошибок', 'value': str(failed)},
        {'name': '👤 Аккаунт', 'value': acc_name},
        {'name': '📞 Взаимных контактов', 'value': str(mutual_count)}
    ]
    
    return discord_embed(
        "🚀 Рассылка активна",
        "Идёт отправка сообщений контактам",
        fields=fields,
        footer="Доставка инициирована..."
    )

def broadcast_result(sent, total, failed, stats):
    """Результат рассылки"""
    fields = [
        {'name': '✅ Успешно', 'value': str(sent)},
        {'name': '❌ Ошибок', 'value': str(failed)},
        {'name': '📊 Всего отправлено', 'value': str(stats.get('total', 0))},
        {'name': '📅 Сегодня', 'value': str(stats.get('today', 0))},
        {'name': '📅 За всё время', 'value': f"{stats.get('broadcasts', 0)} рассылок"}
    ]
    return discord_embed(
        "✅ Рассылка завершена!",
        "Все сообщения обработаны",
        fields=fields,
        footer="Сессии автоматически удалены"
    )

# ==========================================
# 🎨 КНОПКИ
# ==========================================
def main_kb():
    return [
        [Button.inline("🚀 Запустить рассылку", b'broadcast')],
        [Button.inline("👥 Аккаунты", b'accounts'), Button.inline("📦 Материал", b'material')],
        [Button.inline("📈 Статистика", b'stats')],
        [Button.inline("⚙️ Настройки", b'settings')]
    ]

def acc_kb():
    return [
        [Button.inline("📱 По номеру", b'phone'), Button.inline("💾 Session", b'sess_file')],
        [Button.inline("🔑 String", b'sess_str'), Button.inline("🔙 Назад", b'main')]
    ]

def mat_kb():
    return [
        [Button.inline("📥 Загрузить материал", b'upload_mat')],
        [Button.inline("🔙 Назад", b'main')]
    ]

def stats_kb():
    return [
        [Button.inline("📅 Сегодня", b'stats_day'), Button.inline("📅 Неделя", b'stats_week')],
        [Button.inline("📅 Месяц", b'stats_month'), Button.inline("🔙 Назад", b'main')]
    ]

def confirm_kb(): 
    return [[Button.inline("✅ Подтвердить", b'confirm'), Button.inline("❌ Отмена", b'main')]]

def after_kb(): 
    return [
        [Button.inline("🔁 Повторить", b'repeat'), Button.inline("📥 Новый материал", b'new_mat')],
        [Button.inline("➕ Добавить аккаунт", b'accounts'), Button.inline("🏠 Меню", b'main')]
    ]

def cancel_kb(): 
    return [[Button.inline("❌ Отмена", b'cancel')]]

def acc_info_kb():
    return [
        [Button.inline("🚀 Запустить рассылку", b'broadcast')],
        [Button.inline("➕ Ещё аккаунт", b'accounts')],
        [Button.inline("🔙 Назад", b'main')]
    ]

# ==========================================
# 🔧 ФУНКЦИИ
# ==========================================
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
        for acc_id, acc_data in list(accounts[uid].items()):
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
    print(f"✅ {me.first_name} запущен!")

    @bot.on(events.NewMessage(pattern=r'/start'))
    async def start_cmd(e):
        uid = e.sender_id
        clear_user_data(uid)
        authorized_users[uid] = False
        current_step[uid] = 'pin'
        await e.respond(
            discord_embed(
                "🔐 DUCK SPAM BOT",
                "Система требует авторизации",
                fields=[
                    {'name': '📝 Действие', 'value': 'Введите 4-значный PIN-код'},
                    {'name': '🔑 Код по умолчанию', 'value': '`6611`'}
                ],
                footer="Без авторизации доступ запрещён"
            ),
            buttons=Button.clear()
        )

    @bot.on(events.NewMessage)
    async def handler(e):
        uid, txt = e.sender_id, e.text
        step = current_step.get(uid, 'menu')
        
        if (txt and txt.startswith('/')) or e.sender_id == me.id:
            return

        if step == 'pin':
            if txt and txt.isdigit() and len(txt) == 4:
                if txt == CORRECT_PIN:
                    authorized_users[uid] = True
                    current_step[uid] = 'menu'
                    await e.respond(
                        discord_embed(
                            "✅ ДОСТУП РАЗРЕШЁН",
                            "Добро пожаловать в панель управления",
                            fields=[
                                {'name': '🦆 Бот', 'value': 'DUCK SPAM BOT v2.0'},
                                {'name': '📊 Функции', 'value': '• Массовая рассылка\n• Управление аккаунтами\n• Загрузка файлов\n• Статистика'}
                            ],
                            footer="Выберите действие в меню ниже"
                        ),
                        buttons=main_kb()
                    )
                else:
                    await e.respond("❌ **Неверный PIN-код**", buttons=Button.clear())
            return

        if not authorized_users.get(uid):
            current_step[uid] = 'pin'
            await e.respond("🔐 **Введите /start** для авторизации", buttons=Button.clear())
            return

        # 💾 SESSION
        if step == 'sess_file':
            if e.file and e.file.name.endswith('.session'):
                clear_user_data(uid)
                msg = await e.respond(
                    discord_embed(
                        "⏳ Загрузка session...",
                        "Инициализация подключения",
                        fields=[{'name': '📁 Файл', 'value': e.file.name}],
                        footer="Старые сессии удалены"
                    ),
                    buttons=Button.clear()
                )
                try:
                    session_path = await e.download_media(file=f"sessions/acc_{uid}_new.session")
                    client = TelegramClient(session_path.replace('.session', ''), API_ID, API_HASH)
                    await client.connect()
                    me_acc = await client.get_me()
                    
                    try:
                        contacts = await client(GetContactsRequest(0))
                        total = len([u for u in contacts.users if not u.bot])
                        mutual = len([u for u in contacts.users if u.mutual_contact and not u.bot])
                    except:
                        total, mutual = 0, 0
                    
                    accounts.setdefault(uid, {})['active'] = {
                        'client': client, 'phone': me_acc.phone, 'name': me_acc.first_name or 'Без имени',
                        'username': me_acc.username or 'нет', 'total': total, 'mutual': mutual}
                    current_step[uid] = 'menu'
                    
                    await msg.edit(
                        format_acc_info(accounts[uid]['active']),
                        buttons=acc_info_kb()
                    )
                except Exception as err:
                    await msg.edit(f"❌ **Ошибка:** {str(err)[:200]}", buttons=Button.clear())
            return

        # 📥 МАТЕРИАЛ
        if step == 'upload_mat':
            if e.file:
                original_name = e.file.name
                path = await e.download_media(file=f"materials/mat_{uid}_{original_name}")
                cap = txt or ''
                mat = {'file': path, 'caption': cap, 'name': original_name, 'original_name': original_name}
                material_history.setdefault(uid, []).append(mat)
                current_materials[uid] = mat
                current_step[uid] = 'menu'
                await e.respond(
                    discord_embed(
                        "✅ Материал сохранён",
                        "Файл успешно загружен",
                        fields=[
                            {'name': '📁 Имя файла', 'value': original_name},
                            {'name': '📝 Текст', 'value': cap[:100] or 'нет'}
                        ],
                        footer="Готов к рассылке"
                    ),
                    buttons=mat_kb()
                )
            elif txt:
                mat = {'file': None, 'caption': txt, 'name': 'Text', 'original_name': 'Text'}
                material_history.setdefault(uid, []).append(mat)
                current_materials[uid] = mat
                current_step[uid] = 'menu'
                await e.respond(
                    discord_embed(
                        "✅ Текст сохранён",
                        "Текстовый материал готов",
                        fields=[{'name': '📝 Содержание', 'value': txt[:200]}],
                        footer="Готов к рассылке"
                    ),
                    buttons=mat_kb()
                )
            return

    @bot.on(events.CallbackQuery)
    async def cb(e):
        uid = e.sender_id
        if not authorized_users.get(uid):
            return await e.answer("🔐 Введите /start", alert=True)
        
        d = e.data.decode()

        if d == 'main':
            await e.edit(
                discord_embed(
                    "🦆 DUCK SPAM BOT",
                    "Панель управления",
                    footer="Выберите действие"
                ),
                buttons=main_kb()
            )
        elif d == 'accounts':
            accs = accounts.get(uid, {})
            fields = []
            if accs:
                for i, a in enumerate(accs.values(), 1):
                    fields.append({'name': f'👤 Аккаунт {i}', 'value': f"{a['name']} | {a.get('mutual',0)} вз."})
            else:
                fields = [{'name': '⚠️', 'value': 'Аккаунты не добавлены'}]
            await e.edit(
                discord_embed("👥 Управление аккаунтами", f"Всего: {len(accs)}", fields=fields),
                buttons=acc_kb()
            )
        elif d == 'material':
            mats = material_history.get(uid, [])
            fields = []
            if current_materials.get(uid):
                fields.append({'name': '📎 Активный материал', 'value': current_materials[uid]['name']})
            else:
                fields.append({'name': '⚠️', 'value': 'Материал не загружен'})
            await e.edit(
                discord_embed("📦 Материалы", f"Всего: {len(mats)}", fields=fields),
                buttons=mat_kb()
            )
        elif d == 'stats':
            t = broadcast_stats.get(uid, {}).get('total', 0)
            await e.edit(
                discord_embed(
                    "📈 Статистика",
                    "Общая информация",
                    fields=[
                        {'name': '📊 Всего отправлено', 'value': str(t)},
                        {'name': '📅 Сегодня', 'value': str(get_stats(uid,'day'))},
                        {'name': '📅 За неделю', 'value': str(get_stats(uid,'week'))},
                        {'name': '📅 За месяц', 'value': str(get_stats(uid,'month'))},
                        {'name': '🔄 Всего рассылок', 'value': str(broadcast_stats.get(uid, {}).get('broadcasts', 0))}
                    ]
                ),
                buttons=stats_kb()
            )
        elif d == 'phone': 
            current_step[uid]='phone'
            await e.edit("**📱 Введите номер**\n\nФормат: `+79991234567`", buttons=cancel_kb())
        elif d == 'sess_file': 
            current_step[uid]='sess_file'
            await e.edit("**💾 Отправьте .session файл**", buttons=cancel_kb())
        elif d == 'sess_str': 
            current_step[uid]='sess_str'
            await e.edit("**🔑 Введите Session String**", buttons=cancel_kb())
        elif d == 'upload_mat': 
            current_step[uid]='upload_mat'
            await e.edit("**📥 Отправьте файл или текст**", buttons=cancel_kb())
        elif d == 'broadcast':
            accs = accounts.get(uid, {})
            if not accs: return await e.answer("❌ Нет аккаунтов", alert=True)
            if uid not in current_materials: return await e.answer("❌ Нет материала", alert=True)
            total_mut = sum(a.get('mutual',0) for a in accs.values())
            fields = []
            for a in accs.values():
                fields.append({'name': f"👤 {a['name']}", 'value': f"📞 {a.get('mutual',0)} вз."})
            fields.append({'name': '📦 Материал', 'value': current_materials[uid]['name']})
            fields.append({'name': '📊 Всего контактов', 'value': str(total_mut)})
            
            await e.edit(
                discord_embed("🚀 Запуск рассылки", "Параметры рассылки", fields=fields, footer="Подтвердите запуск"),
                buttons=confirm_kb()
            )
        elif d == 'confirm':
            await e.edit("⏳ **Инициализация...**", buttons=Button.clear())
            await do_broadcast(bot, uid, e)
        elif d == 'repeat':
            if uid in current_materials:
                await e.edit("🔁 **Повтор...**", buttons=Button.clear())
                await do_broadcast(bot, uid, e)
        elif d == 'new_mat':
            current_step[uid]='upload_mat'
            await e.edit("**📥 Отправьте новый материал**", buttons=cancel_kb())
        elif d == 'cancel':
            current_step[uid] = 'menu'
            await e.edit("**❌ Отменено**", buttons=main_kb())
        await e.answer()

    async def do_broadcast(bot, uid, e):
        accs = accounts.get(uid, {})
        mat = current_materials.get(uid)
        if not accs or not mat: return
        
        sent, failed = 0, 0
        all_targets = []
        
        for acc_id, acc_data in accs.items():
            try:
                c = await acc_data['client'](GetContactsRequest(0))
                targets = [u for u in c.users if u.mutual_contact and not u.bot]
                all_targets.append((acc_data, targets))
            except Exception as err:
                await e.respond(f"❌ Ошибка: {err}", buttons=Button.clear())
        
        if not all_targets:
            await e.respond("⚠️ **Нет контактов**", buttons=Button.clear())
            return
        
        total_contacts = sum(len(t) for _, t in all_targets)
        status_msg = await e.respond(
            broadcast_progress(0, total_contacts, 0, "Инициализация", total_contacts),
            buttons=Button.clear()
        )
        
        current = 0
        for acc_data, targets in all_targets:
            for user in targets:
                try:
                    if mat['file']:
                        await acc_data['client'].send_file(
                            user.id, mat['file'], caption=mat['caption'],
                            attributes=[DocumentAttributeFilename(file_name=mat.get('original_name', 'file'))]
                        )
                    else:
                        await acc_data['client'].send_message(user.id, mat['caption'])
                    sent += 1
                except: 
                    failed += 1
                
                current += 1
                if current % 10 == 0:
                    await status_msg.edit(
                        broadcast_progress(current, total_contacts, failed, acc_data['name'], len(targets)),
                        buttons=Button.clear()
                    )
                await asyncio.sleep(2)
        
        update_stats(uid, sent)
        stats = {
            'total': broadcast_stats[uid]['total'],
            'today': get_stats(uid, 'day'),
            'broadcasts': broadcast_stats[uid].get('broadcasts', 0)
        }
        
        await status_msg.edit(
            broadcast_result(sent, total_contacts, failed, stats),
            buttons=after_kb()
        )
        current_step[uid] = 'after'

    await bot.run_until_disconnected()

async def run():
    await asyncio.gather(start_web(), main())

if __name__ == '__main__':
    asyncio.run(run())
