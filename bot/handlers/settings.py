from typing import Tuple, Dict

import asyncio

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
)
from bot.handlers.balance import check_if_user_has_enough_tokens
from bot.handlers.constants import SettingsData
from bot.handlers.payments_ui import show_payment_methods_handle


def get_settings_menu(user_id: int) -> Tuple[str, InlineKeyboardMarkup]:
    strings = get_strings(user_id)
    current_model = db.get_user_attribute(user_id, "current_model")
    text = config.models["info"][current_model]["description"][strings.lang]

    text += "\n\n"
    scores = config.models["info"][current_model]["scores"]
    for score_dict in scores:
        text += "🟢" * score_dict["score"] + "⚪️" * (5 - score_dict["score"]) + f" – {score_dict['title'][strings.lang]}\n\n"

    text += strings["select_model"]

    # buttons to choose models
    buttons = []
    for model_key in config.models["available_text_models"]:
        title = config.models["info"][model_key]["name"]
        if model_key == current_model:
            title = "✅ " + title
        buttons.append(
            InlineKeyboardButton(title, callback_data=SettingsData(model_key).dump())
        )
    reply_markup = InlineKeyboardMarkup([buttons])

    return text, reply_markup


@add_handler_routines(check_if_previous_message_is_answered=True)
async def settings_handle(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    text, reply_markup = get_settings_menu(user_id)
    await update.effective_message.reply_text(
        text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )


@add_handler_routines(answer_callback_query=True)
async def set_settings_handle(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    strings = get_strings(user_id)
    model_key = SettingsData.load(update.callback_query.data).model_key

    # is pro?
    is_pro = is_pro_model(model_key=model_key)
    if is_pro and not db.does_user_have_successful_payment(user_id):
        text = strings["pro_model"].format(model_name=config.models["info"][model_key]["name"])
        await update.effective_message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
        )

        await asyncio.sleep(3.0)
        await show_payment_methods_handle(update, context)
        return

    db.set_user_attribute(user_id, "current_model", model_key)
    db.start_new_dialog(user_id)

    text, reply_markup = get_settings_menu(user_id)
    await send_reply(
        update.effective_message,
        try_edit=True,
        text=text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML,
    )

    # mxp
    distinct_id, event_name, properties = (
        user_id,
        "set_settings",
        {"model": model_key}
    )
    mxp.track(distinct_id, event_name, properties)


def is_pro_model(model_key: str) -> bool:
    return (
        ("is_pro" in config.models["info"][model_key]) and
        (config.models["info"][model_key]["is_pro"] == True)
    )


async def maybe_switch_model_to_default_because_not_enough_tokens(
    bot: Bot,
    user_id: UserId,
    chat_id: ChatId,
):
    current_model = db.get_user_attribute(user_id, "current_model")
    if (
      (not check_if_user_has_enough_tokens(user_id=user_id)) and
      (is_pro_model(current_model)) and
      (config.enable_message_queue)
    ):
        strings = get_strings(user_id)
        default_model = "gpt-3.5-turbo"

        db.set_user_attribute(user_id, "current_model", default_model)
        db.start_new_dialog(user_id)

        text = strings["switch_model_to_default_because_not_enough_tokens"].format(
            current_model_name=config.models["info"][current_model]["name"],
            default_model_name=config.models["info"][default_model]["name"],
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
            "set_settings",
            {"model": default_model}
        )
        mxp.track(distinct_id, event_name, properties)
