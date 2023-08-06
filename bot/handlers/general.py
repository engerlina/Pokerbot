from typing import Tuple, Dict

import asyncio
import logging
from datetime import datetime

from telegram import Update
from telegram.ext import CallbackContext
from telegram.constants import ParseMode

from bot import config
from bot.database import db, UserId
from bot.config import mxp
from bot.handlers.utils import (
    get_strings,
    add_handler_routines,
    register_user,
    send_reply,
)
from bot.handlers.tokens import add_tokens_to_ref_user
from bot.handlers.chat_mode import show_chat_modes_handle


logger = logging.getLogger(__name__)


def parse_deeplink_parameters(s):
    try:
        if len(s) == 0:
            return dict()

        deeplink_parameters = {}
        for key_value in s.split("-"):
            key, value = key_value.split("=")
            deeplink_parameters[key] = value

        return deeplink_parameters
    except:
        logger.error(f"Wrong deeplink parameters: {s}")
        return dict()


async def start_handle(update: Update, context: CallbackContext):
    is_new_user = await register_user(update, context)
    user_id = update.effective_user.id
    strings = get_strings(user_id)

    # deeplink parameters
    argv = update.effective_message.text.split(" ")
    if len(argv) > 1:
        # example: https://t.me/chatgpt_karfly_bot?start=lang=en-source=durov-ref=karfly
        deeplink_parameters = parse_deeplink_parameters(argv[1])

        if "lang" in deeplink_parameters:
            db.set_user_attribute(user_id, "lang", deeplink_parameters["lang"])

        if "source" in deeplink_parameters:
            if db.get_user_attribute(user_id, "deeplink_source") is None:
                db.set_user_attribute(user_id, "deeplink_source", deeplink_parameters["source"])

        if "ref" in deeplink_parameters and is_new_user:
            ref_user_id = int(deeplink_parameters["ref"])

            # set ref for new user
            if db.get_user_attribute(user_id, "ref") is None:
                db.set_user_attribute(user_id, "ref", ref_user_id)

            if db.check_if_user_exists(ref_user_id):
                ref_user_invites = db.get_user_attribute(ref_user_id, "invites")
                if user_id not in ref_user_invites and len(ref_user_invites) < config.max_invites_per_user:
                    await add_tokens_to_ref_user(context, user_id=user_id, ref_user_id=ref_user_id)

    db.set_user_attribute(user_id, "last_interaction", datetime.now())
    db.start_new_dialog(user_id)

    if is_new_user:
        await send_welcome_message_to_new_user(update, context)
        await show_chat_modes_handle(update, context)
    else:
        text = f"{strings['hello']}\n\n{strings['help'].format(support_username=config.support_username)}"
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)

    # mxp
    distinct_id, event_name, properties = (user_id, "start", {})
    mxp.track(distinct_id, event_name, properties)


async def send_welcome_message_to_new_user(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    strings = get_strings(user_id)

    for message_key in ["welcome_message_1", "welcome_message_2", "welcome_message_3"]:
        placeholder_message = await update.effective_message.reply_text("...")
        await asyncio.sleep(1.0)

        text = ""
        for i, text_chunk in enumerate(strings[message_key]):
            text += text_chunk + "\n"

            # skip empty line
            if len(text_chunk) == 0:
                continue

            current_text = text
            if i != len(strings[message_key]) - 1:
                current_text += "..."

            await send_reply(
                placeholder_message,
                try_edit=True,
                text=current_text,
                parse_mode=ParseMode.HTML,
            )
            delay = min(3.5, max(1.0, 0.025 * len(text_chunk)))
            await asyncio.sleep(delay)


@add_handler_routines()
async def help_handle(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    strings = get_strings(user_id)
    text = strings["help"].format(support_username=config.support_username)
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


@add_handler_routines()
async def help_group_chat_handle(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    strings = get_strings(user_id)
    text = strings["help_group_chat"].format(bot_username=config.bot_username)
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)
    await update.effective_message.reply_video(config.help_group_chat_video_path)

    # mxp
    mxp.track(user_id, "help_group_chat", {})
