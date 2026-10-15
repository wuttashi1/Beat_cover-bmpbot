import html
from collections import OrderedDict

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.error import BadRequest, TelegramError

_PANELS = OrderedDict()
_LIMIT = 512
NAVIGATION = (("🖼 Обложка", "ui:covers"), ("🎬 Видео", "ui:video"), ("🎚 BPM", "ui:bpm"))


def navigation(active=None):
    return [
        InlineKeyboardButton(
            ("• " if action == active else "") + label, callback_data=action
        )
        for label, action in NAVIGATION
    ]


def markup(rows, active=None, navigate=True):
    buttons = [
        [InlineKeyboardButton(label, callback_data=action) for label, action in row]
        for row in rows
    ]
    if navigate:
        buttons.append(navigation(active))
        buttons.append([InlineKeyboardButton("⌂ Студия", callback_data="ui:home")])
    return InlineKeyboardMarkup(buttons)


def formatted(text):
    title, _, body = text[:3900].partition("\n")
    return (
        "<b>" + html.escape(title) + "</b>" + ("\n" + html.escape(body) if body else "")
    )


async def present(update, text, reply_markup):
    incoming = update.effective_message
    get_bot = getattr(update, "get_bot", None)
    if not get_bot:
        return await incoming.reply_text(text, reply_markup=reply_markup)
    bot = get_bot()
    chat_id = update.effective_chat.id
    key = (id(bot), chat_id, update.effective_user.id)
    message_id = _PANELS.get(key)
    query = update.callback_query
    if not message_id and query and incoming.text:
        message_id = incoming.message_id
    if message_id:
        try:
            result = await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=formatted(text),
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
            _remember(key, message_id)
            return result
        except BadRequest as error:
            reason = str(error).lower()
            if "not modified" in reason:
                _remember(key, message_id)
                return None
            if not any(
                item in reason
                for item in (
                    "message to edit not found",
                    "message can't be edited",
                    "message can not be edited",
                    "there is no text",
                )
            ):
                raise
    result = await bot.send_message(
        chat_id, formatted(text), reply_markup=ReplyKeyboardRemove(), parse_mode="HTML"
    )
    _remember(key, result.message_id)
    await bot.edit_message_reply_markup(
        chat_id=chat_id, message_id=result.message_id, reply_markup=reply_markup
    )
    return result


def _remember(key, message_id):
    _PANELS[key] = message_id
    _PANELS.move_to_end(key)
    while len(_PANELS) > _LIMIT:
        _PANELS.popitem(last=False)


async def dismiss_input(update):
    if update.callback_query or not update.effective_message.text:
        return
    try:
        await update.effective_message.delete()
    except TelegramError:
        pass
