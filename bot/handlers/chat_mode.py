from typing import Tuple, Dict
from enum import Enum

import asyncio
import logging

from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import CallbackContext
from telegram.constants import ParseMode

from bot import config
from bot.database import db, UserId, ChatId
from bot.config import mxp
from bot.handlers.utils import (
    get_strings,
    add_handler_routines,
    send_reply,
    ignore_message_not_modified_error,
)
from bot.handlers.balance import check_if_user_has_enough_tokens, show_balance, ShowBalanceSource
from bot.handlers.constants import SetChatModeData, ChoosePageChatModesData
from bot.handlers.payments_ui import show_payment_methods_handle


logger = logging.getLogger(__name__)


def is_pro_chat_mode(chat_mode_key: str):
    return ("is_pro" in config.chat_modes[chat_mode_key]) and (config.chat_modes[chat_mode_key]["is_pro"] == True)


def get_chat_mode_menu(page_index: int, strings: Dict[str, str]) -> Tuple[str, InlineKeyboardMarkup]:
    n_chat_modes_per_page = config.n_chat_modes_per_page
    text = strings["select_chat_mode"].format(n_chat_modes=len(config.chat_modes))

    # buttons
    chat_mode_keys = list(config.chat_modes.keys())
    _start = page_index * n_chat_modes_per_page
    _end = (page_index + 1) * n_chat_modes_per_page
    page_chat_mode_keys = chat_mode_keys[_start:_end]

    keyboard = []
    for chat_mode_key in page_chat_mode_keys:
        name = config.chat_modes[chat_mode_key]["name"][strings.lang]
        if "is_pro" in config.chat_modes[chat_mode_key] and (config.chat_modes[chat_mode_key]["is_pro"] == True):
            name += " [PRO]"
        keyboard.append([InlineKeyboardButton(name, callback_data=SetChatModeData(chat_mode_key).dump())])

    # pagination
    if len(chat_mode_keys) > n_chat_modes_per_page:
        is_first_page = (page_index == 0)
        is_last_page = ((page_index + 1) * n_chat_modes_per_page >= len(chat_mode_keys))

        forward_button = InlineKeyboardButton("»", callback_data=ChoosePageChatModesData(page_index+1).dump())
        backward_button = InlineKeyboardButton("«", callback_data=ChoosePageChatModesData(page_index-1).dump())

        last_row_buttons = []
        if not is_first_page:
            last_row_buttons.append(backward_button)
        if not is_last_page:
            last_row_buttons.append(forward_button)
        keyboard.append(last_row_buttons)

    return text, InlineKeyboardMarkup(keyboard)


@add_handler_routines(check_if_previous_message_is_answered=True)
async def show_chat_modes_handle(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    strings = get_strings(user_id)
    text, reply_markup = get_chat_mode_menu(0, strings=strings)
    await update.effective_message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


@add_handler_routines(check_if_previous_message_is_answered=True, answer_callback_query=True)
async def show_chat_modes_callback_handle(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    strings = get_strings(user_id)

    page_index = ChoosePageChatModesData.load(update.callback_query.data).page
    if page_index < 0:
        return

    text, reply_markup = get_chat_mode_menu(page_index, strings=strings)
    await send_reply(
        update.effective_message,
        try_edit=True,
        text=text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML,
    )


@add_handler_routines(answer_callback_query=True)
async def set_chat_mode_handle(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    strings = get_strings(user_id)
    chat_mode = SetChatModeData.load(update.callback_query.data).chat_mode_key

    distinct_id, event_name, properties = (
        user_id,
        "select_chat_mode",
        {"chat_mode": chat_mode}
    )
    mxp.track(distinct_id, event_name, properties)

    # is redirect to chatgpt_plus?
    if "type" in config.chat_modes[chat_mode] and config.chat_modes[chat_mode]["type"] == "redirect_to_chatgpt_plus":
        await update.effective_message.reply_text(
            strings["redirect_to_chatgpt_plus"],
            parse_mode=ParseMode.HTML,
        )
        return

    # is pro?
    if (
        (is_pro_chat_mode(chat_mode)) and
        (not check_if_user_has_enough_tokens(user_id=user_id)) and
        (config.enable_message_queue)
    ):
        await show_balance(
            bot=context.bot,
            user_id=update.effective_user.id,
            chat_id=update.effective_chat.id,
            source=ShowBalanceSource.PRO_CHAT_MODE,
            source_chat_mode_key=chat_mode
        )
        return

    db.set_user_attribute(user_id, "current_chat_mode", chat_mode)
    db.start_new_dialog(user_id)

    current_model = db.get_user_attribute(user_id, "current_model")

    text = ""
    if config.chat_modes[chat_mode]["model_type"] == "text":
        text += f"<i>{config.models['info'][current_model]['name']}</i>: "
    text += config.chat_modes[chat_mode]["welcome_message"][strings.lang]
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)

    # mxp
    distinct_id, event_name, properties = (
        user_id,
        "set_chat_mode",
        {"chat_mode": chat_mode}
    )
    mxp.track(distinct_id, event_name, properties)


async def maybe_switch_chat_mode_to_default_because_not_enough_tokens(
    bot: Bot,
    user_id: UserId,
    chat_id: ChatId,
):
    current_chat_mode = db.get_user_attribute(user_id, "current_chat_mode")
    if (
      (not check_if_user_has_enough_tokens(user_id=user_id)) and
      (is_pro_chat_mode(current_chat_mode)) and
      (config.enable_message_queue)
    ):
        strings = get_strings(user_id)
        default_chat_mode = "assistant"

        db.set_user_attribute(user_id, "current_chat_mode", default_chat_mode)
        db.start_new_dialog(user_id)

        text = strings["switch_chat_mode_to_default_because_not_enough_tokens"].format(
            current_chat_mode_name=config.chat_modes[current_chat_mode]["name"][strings.lang],
            default_chat_mode_name=config.chat_modes[default_chat_mode]["name"][strings.lang]
        )

        await send_reply(
            bot=bot,
            user_id=user_id,
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
        )

        # mxp
        distinct_id, event_name, properties = (
            user_id,
            "set_chat_mode",
            {"chat_mode": default_chat_mode}
        )
        mxp.track(distinct_id, event_name, properties)