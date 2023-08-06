from typing import Optional
import logging
import traceback
import html
import json
import uuid

from telegram import Update, Bot
from telegram.ext import CallbackContext
from telegram.constants import ParseMode

from bot import config
from bot.config import mxp
from bot.database import db, ChatId, UserId
from bot.utils import split_text_into_chunks
from bot.handlers.utils import get_strings
from bot.handlers.utils import send_reply


logger = logging.getLogger(__name__)


async def send_message_about_error_to_user_and_admin(
    error: Exception,
    bot: Bot,
    chat_id: Optional[ChatId] = None,
    user_id: Optional[UserId] = None,
    update: Optional[Update] = None,
) -> None:
    error_id = str(uuid.uuid4())
    logger.exception(f"Exception while handling an update (error_id={error_id})")

    try:
        await send_message_about_error_to_user(
            error=error,
            bot=bot,
            chat_id=chat_id,
            user_id=user_id,
        )
        if config.admin_chat_id is not None:
            await send_message_about_error_to_admin(
                error=error,
                error_id=error_id,
                bot=bot,
                chat_id=chat_id,
                user_id=user_id,
                update=update,
            )
    except Exception as e:
        try:
            if config.admin_chat_id is not None:
                await send_message_about_error_to_admin(
                    error=e,
                    error_id=error_id,
                    bot=bot,
                    chat_id=chat_id,
                    user_id=user_id,
                    update=update,
                )
        except:
            logger.exception("Exception while sending message about failed error handle to admin")



async def send_message_about_error_to_user(
    error: Exception,
    bot: Bot,
    chat_id: ChatId,
    user_id: UserId,
):
    strings = get_strings(user_id)

    # send message to user
    text = strings["exception"].format(support_username=config.support_username)
    await send_reply(
        bot=bot,
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
    )


async def send_message_about_error_to_admin(
    error: Exception,
    error_id: str,
    bot: Bot,
    chat_id: ChatId,
    user_id: UserId,
    update: Optional[Update] = None,
):
    # update str
    if update is not None:
        update_str = update.to_dict() if isinstance(update, Update) else str(update)
    else:
        update_str = ""

    # traceback str
    traceback_list = traceback.format_exception(None, error, error.__traceback__)
    traceback_str = "".join(traceback_list)

    text = (
        f"<b>🚨 Exception was raised:</b>\n"
        f"  ⤷ error_id: <code>{error_id}</code>\n"
        f"  ⤷ chat_id: <code>{chat_id}</code>\n"
        f"  ⤷ user_id: <code>{user_id}</code>\n\n"
    )

    if update_str:
        text += (
            f"<b>🔄 Update:</b>\n"
            f"<pre>update = {html.escape(json.dumps(update_str, indent=2, ensure_ascii=False))}</pre>\n\n"
        )

    if traceback_str:
        text += (
            f"<b>🔴 Traceback:</b>\n"
            f"<pre>{html.escape(traceback_str)}</pre>"
        )

    # split text into multiple messages
    for text_chunk in split_text_into_chunks(text, 4096):
        await send_reply(
            bot=bot,
            chat_id=config.admin_chat_id,
            text=text_chunk,
            parse_mode=ParseMode.HTML,
            try_no_parse_mode=True
        )


async def error_handle(update: Update, context: CallbackContext) -> None:
    error = context.error
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    await send_message_about_error_to_user_and_admin(
        error=error,
        bot=context.bot,
        chat_id=chat_id,
        user_id=user_id,
        update=update
    )
