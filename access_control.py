"""Private-by-default access gate, independent from channel publishing rights."""
from telegram import ReplyKeyboardMarkup
from telegram.ext import ApplicationHandlerStop, TypeHandler
from telegram import Update
import database

with database.db:
    database.db.execute('CREATE TABLE IF NOT EXISTS bot_access (user_id INTEGER PRIMARY KEY)')


def allowed(uid, owner):
    return bool(owner and (uid == owner or database.db.execute(
        'SELECT 1 FROM bot_access WHERE user_id=?', (uid,)).fetchone()))


async def gate(update, context):
    owner = context.bot_data.get('owner_id')
    user = update.effective_user
    if user and update.effective_chat and update.effective_chat.type == 'private' and allowed(user.id, owner):
        return
    text = 'Бот доступен только по приглашению владельца.'
    if user:
        text += f' Твой Telegram ID: {user.id}'
    if update.callback_query:
        await update.callback_query.answer(text, show_alert=True)
    elif update.effective_message:
        await update.effective_message.reply_text(text)
    raise ApplicationHandlerStop


def install(app, owner):
    app.bot_data['owner_id'] = owner
    app.add_handler(TypeHandler(Update, gate), group=-100)


ROWS = [['➕ Добавить пользователя', '➖ Удалить пользователя'], ['📋 Доступ к боту'], ['🏠 Главное меню']]


async def manage(update, context, owner):
    text = update.effective_message.text.strip()
    buttons = {'👥 Доступ к боту', '➕ Добавить пользователя', '➖ Удалить пользователя', '📋 Доступ к боту'}
    action = context.user_data.get('access_action')
    if text not in buttons and not action:
        return False
    if update.effective_user.id != owner:
        context.user_data.pop('access_action', None)
        await update.effective_message.reply_text('Управление доступом доступно только владельцу.')
        return True
    if text in buttons:
        context.user_data.pop('access_action', None)
    if text in ('👥 Доступ к боту', '📋 Доступ к боту'):
        ids = [row[0] for row in database.db.execute('SELECT user_id FROM bot_access ORDER BY user_id')]
        lines = '\n'.join(str(uid) for uid in ids) or 'Приглашённых пользователей пока нет.'
        await update.effective_message.reply_text(f'👥 Доступ к боту\nВладелец: {owner}\n{lines}\n\nПрава публикации в канал выдаются отдельно.', reply_markup=ReplyKeyboardMarkup(ROWS, resize_keyboard=True))
    elif text in ('➕ Добавить пользователя', '➖ Удалить пользователя'):
        context.user_data['access_action'] = 'add' if text.startswith('➕') else 'remove'
        await update.effective_message.reply_text('Введи Telegram ID пользователя. Он может узнать свой ID, отправив /start этому боту.', reply_markup=ReplyKeyboardMarkup([['🏠 Главное меню']], resize_keyboard=True))
    else:
        try:
            uid = int(text)
            if not 0 < uid < 2**63:
                raise ValueError
        except ValueError:
            await update.effective_message.reply_text('Нужен положительный числовой Telegram ID.')
            return True
        if uid == owner:
            await update.effective_message.reply_text('Владелец всегда имеет доступ.')
        else:
            with database.db:
                if action == 'add':
                    database.db.execute('INSERT OR IGNORE INTO bot_access VALUES (?)', (uid,))
                else:
                    database.db.execute('DELETE FROM bot_access WHERE user_id=?', (uid,))
                    database.db.execute('DELETE FROM channel_publishers WHERE user_id=?', (uid,))
            await update.effective_message.reply_text(f"✅ {uid}: доступ {'добавлен' if action == 'add' else 'отозван'}.", reply_markup=ReplyKeyboardMarkup(ROWS, resize_keyboard=True))
        context.user_data.pop('access_action', None)
    return True
