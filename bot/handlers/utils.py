from typing import Optional, List, Dict, Any, Union
from contextlib import contextmanager

from functools import wraps

import logging
import asyncio
from datetime import datetime
from collections import defaultdict
import concurrent.futures

import telegram
from telegram import Update, Message, Bot
from telegram.ext import CallbackContext
from telegram.constants import ParseMode, ChatAction, ChatType

from bot import config
from bot.config import mxp, strings
from bot.database import db, UserId


logger = logging.getLogger(__name__)

# asyncio utils
user_semaphores: Dict[UserId, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(1))
thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)


async def send_reply(
    message: Optional[Message] = None,
    bot: Optional[Bot] = None,
    user_id: Optional[int] = None,
    chat_id: Optional[int] = None,
    message_id: Optional[int] = None,
    try_edit: bool = False,
    try_delete: bool = False,
    try_no_parse_mode: bool = False,
    send_as_reply: bool = False,
    ignore_message_not_modified_error: bool = True,
    **kwargs,
) -> Message:
    if message is None:
        assert bot is not None
    else:
        bot = message.get_bot()
        chat_id = message.chat.id
        message_id = message.message_id

    if chat_id is None and user_id is not None:
        chat_id = db.get_chat_id(user_id)

    assert chat_id is not None
    assert bot is not None
    kwargs['chat_id'] = chat_id

    if try_edit or try_delete or send_as_reply:
        assert message_id is not None

    if try_edit and ignore_message_not_modified_error:
        assert message is not None

    async def _reply():
        if try_edit:
            try:  # TODO: forbidden error
                return await bot.edit_message_text(message_id=message_id, **kwargs)
            except telegram.error.BadRequest as e:
                if ignore_message_not_modified_error and "message is not modified" in str(e).lower():
                    return message
            except telegram.error.Forbidden:
                logger.info("Failed to edit message (forbidden by user)")
                return None
            except:  # TODO: handle specific error only
                pass
        elif try_delete:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=message_id)
            except:   # TODO: handle specific error only
                pass

        if send_as_reply:
            kwargs['reply_to_message_id'] = message_id

        try:
            return await bot.send_message(**kwargs)
        except telegram.error.Forbidden:
            logger.info("Failed to send message (forbidden by user)")
            return None

    if try_no_parse_mode:
        try:
            return await _reply()
        except telegram.error.BadRequest:
            if 'parse_mode' in kwargs:
                kwargs.pop('parse_mode')
                return await _reply()
            else:
                raise
    else:
        return await _reply()


def add_handler_routines(
    register_user: bool = True,
    send_typing: bool = False,
    answer_callback_query: bool = False,
    ignore_if_bot_is_not_mentioned: bool = False,
    check_if_previous_message_is_answered: bool = False,
):
    def decorator(f):
        @wraps(f)
        async def _fn(update: Update, context: CallbackContext, *args, **kwargs):
            if register_user:
                await _register_user(update, context)
            if ignore_if_bot_is_not_mentioned and not is_bot_mentioned(update, context):
                return
            if check_if_previous_message_is_answered:
                if await is_previous_message_not_answered_yet(update, context):
                    return
            if send_typing:
                try:
                    await update.effective_chat.send_action(ChatAction.TYPING)
                except:
                    pass
            if answer_callback_query:
                try:
                    await update.callback_query.answer()
                except:
                    pass
            await f(update, context, *args, **kwargs)
        return _fn
    return decorator


def get_strings(user_id: UserId) -> Dict[str, str]:
    try:
        lang = db.get_user_attribute(user_id, "lang")
    except Exception as e:
        logger.error(f"Failed to get user language, fallback to default language: {e}")
        lang = config.default_lang

    if lang is None:
        lang = config.default_lang

    class _Wrapper:
        def __getitem__(self, name: str) -> str:
            return strings[name][lang]

        @property
        def lang(self) -> str:
            return lang

    return _Wrapper()


async def is_previous_message_not_answered_yet(
    update: Update,
    context: CallbackContext,
) -> bool:
    user_id = update.effective_user.id
    if user_semaphores[user_id].locked():
        text = get_strings(user_id)["previous_message_is_not_answered_yet"]
        try:
            await send_reply(
                message=update.effective_message,
                text=text,
                send_as_reply=True,
                parse_mode=ParseMode.HTML,
            )
        except:
            logger.error("Failed to tell user that previous message is not answered")
        finally:
            return True
    else:
        return False


def is_bot_mentioned(update: Update, context: CallbackContext) -> bool:
    try:
        message = update.effective_message

        if message.chat.type == ChatType.PRIVATE:
            return True
        if message.text is not None and config.bot_username in message.text:
            return True
        if message.reply_to_message is not None:
            if message.reply_to_message.from_user.id == context.bot.id:
                return True
    except:
        logger.error("Could not check if the bot was mentioned, fallback to True")
        return True

    return False


async def _register_user(update: Update, context: CallbackContext) -> bool:
    user = update.effective_user
    is_new_user = False
    if not db.check_if_user_exists(user.id):
        db.add_new_user(
            user.id,
            update.effective_chat.id,
            initial_token_balance=config.initial_token_balance,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name
        )
        db.start_new_dialog(user.id)

        is_new_user = True

    if db.get_user_attribute(user.id, "current_dialog_id") is None:
        db.start_new_dialog(user.id)

    # token balance
    if not db.check_if_user_attribute_exists(user.id, "token_balance"):
        db.set_user_attribute(user.id, "token_balance", config.initial_token_balance)

    db.set_user_attribute(user.id, "last_interaction", datetime.now())

    if db.get_user_attribute(user.id, "current_model") is None:
        db.set_user_attribute(user.id, "current_model", config.models["available_text_models"][0])

    # back compatibility for n_used_tokens field
    n_used_tokens = db.get_user_attribute(user.id, "n_used_tokens")
    if isinstance(n_used_tokens, int):  # old format
        new_n_used_tokens = {
            "gpt-3.5-turbo": {
                "n_input_tokens": 0,
                "n_output_tokens": n_used_tokens
            }
        }
        db.set_user_attribute(user.id, "n_used_tokens", new_n_used_tokens)

    # image generation
    if db.get_user_attribute(user.id, "n_generated_images") is None:
        db.set_user_attribute(user.id, "n_generated_images", 0)

    # voice message transcription
    if db.get_user_attribute(user.id, "n_transcribed_seconds") is None:
        db.set_user_attribute(user.id, "n_transcribed_seconds", 0.0)

    # lang
    if (lang := db.get_user_attribute(user.id, "lang")) is None:
        db.set_user_attribute(user.id, "lang", config.default_lang)

    # invites
    if db.get_user_attribute(user.id, "invites") is None:
        db.set_user_attribute(user.id, "invites", [])

    # make balance always non-negative
    token_balance = db.get_user_attribute(user.id, "token_balance")
    if token_balance < 0:
        db.set_user_attribute(user.id, "token_balance", max(0, token_balance))

    # mxp
    user_dict = db.user_collection.find_one({"_id": user.id})
    mxp.people_set(user.id, user_dict)

    return is_new_user

# alias for outer imports
register_user = _register_user


@contextmanager
def ignore_message_not_modified_error():
    try:
        yield
    except telegram.error.BadRequest as e:
        if "message is not modified" in str(e).lower():
            pass
        else:
            raise
