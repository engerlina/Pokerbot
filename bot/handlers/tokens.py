from typing import Tuple, Dict

import asyncio
import logging
from datetime import datetime

from telegram import Update, User, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import CallbackContext
from telegram.constants import ParseMode

from bot import config
from bot.database import db, UserId
from bot.config import mxp
from bot.handlers.utils import send_reply
from bot.handlers.constants import SetChatModeData, ChoosePageChatModesData
from bot.handlers.payments_ui import send_user_message_about_n_added_tokens
from bot.handlers.admin import notify_admins_about_successfull_payment



async def confirm_payment_and_add_tokens(
    context: CallbackContext,
    payment_id: int,
):
    """Confirm given payment, send notification to user, update database
    state and log events
    """
    db.set_payment_attribute(payment_id, "status", "paid")
    payment_dict = db.payment_collection.find_one({"_id": payment_id})

    user_id = payment_dict["user_id"]
    n_tokens_to_add = payment_dict["n_tokens_to_add"]

    if payment_dict["are_tokens_added"]:
        return

    _balance = db.get_user_attribute(user_id, "token_balance")
    db.set_user_attribute(
        user_id,
        "token_balance",
        _balance + n_tokens_to_add,
    )
    db.set_payment_attribute(payment_id, "are_tokens_added", True)

    await send_user_message_about_n_added_tokens(context, n_tokens_to_add, user_id=user_id)
    await notify_admins_about_successfull_payment(context, payment_id)

    # mxp
    product = config.products[payment_dict["product_key"]]
    amount = product["price"]
    if product["currency"] == "RUB":
        amount /= 77

    distinct_id, event_name, properties = (
        user_id,
        "successful_payment",
        {
            "token_balance": db.get_user_attribute(user_id, "token_balance"),
            "payment_method": payment_dict["payment_method"],
            "product": payment_dict["product_key"],
            "payment_id": payment_id,
            "amount": amount
        }
    )
    mxp.track(distinct_id, event_name, properties)


async def add_tokens_to_ref_user(
    context: CallbackContext,
    user_id: UserId,
    ref_user_id: UserId
):
    """Create fake payment, send notification to user, update database
    state and log events
    """
    _balance = db.get_user_attribute(ref_user_id, "token_balance")
    db.set_user_attribute(ref_user_id, "token_balance", _balance + config.n_tokens_to_add_to_ref)

    # save in database
    payment_id = db.get_new_unique_payment_id()
    db.add_new_payment(
        payment_id=payment_id,
        payment_method="add_tokens_for_ref",
        payment_method_type="add_tokens_for_ref",
        product_key="add_tokens_for_ref",
        user_id=ref_user_id,
        amount=0.0,
        currency=None,
        status="not_paid",  # don't give paid features to users for invited users
        invoice_url="",
        expired_at=datetime.now(),
        n_tokens_to_add=config.n_tokens_to_add_to_ref,
    )

    # update invites
    _invites = db.get_user_attribute(ref_user_id, "invites")
    db.set_user_attribute(ref_user_id, "invites", _invites + [user_id])

    # send message to ref user
    ref_chat_id = db.get_user_attribute(ref_user_id, "chat_id")
    await send_user_message_about_n_added_tokens(
        context,
        n_tokens_added=config.n_tokens_to_add_to_ref,
        chat_id=ref_chat_id,
        user_id=ref_user_id,
        joined_friend_user_id=user_id,
    )

    # send message to admins
    if (
        config.notify_admins_about_new_invited_user and
        config.admin_chat_id is not None
    ):
        text = (
            f'🟣 User <a href="tg://user?id={ref_user_id}">{ref_user_id}</a> invited '
            f'<a href="tg://user?id={user_id}">{user_id}</a>. '
            f'<b>{config.n_tokens_to_add_to_ref}</b> tokens were successfully added to his balance!'
        )
        await send_reply(
            bot=context.bot,
            chat_id=config.admin_chat_id,
            text=text,
            parse_mode=ParseMode.HTML
        )

    # mxp
    distinct_id, event_name, properties = (
        ref_user_id,
        "user_joined_via_invite",
        {
            "invited_user_id": user_id
        }
    )
    mxp.track(distinct_id, event_name, properties)


def get_total_n_used_bot_tokens(user_id: UserId) -> int:
    total_n_used_bot_tokens = 0
    for model_key, model_values in db.get_user_attribute(user_id, "n_used_tokens").items():
        total_n_used_bot_tokens += convert_text_tokens_to_bot_tokens(model_key, model_values["n_input_tokens"], model_values["n_output_tokens"])

    # voice messages
    voice_recognition_n_used_bot_tokens = convert_transcribed_seconds_to_bot_tokens("whisper-1", db.get_user_attribute(user_id, "n_transcribed_seconds"))
    total_n_used_bot_tokens += voice_recognition_n_used_bot_tokens

    # image generation
    image_generation_n_used_bot_tokens = convert_generated_images_to_bot_tokens("dalle-2", db.get_user_attribute(user_id, "n_generated_images"))
    total_n_used_bot_tokens += image_generation_n_used_bot_tokens

    return total_n_used_bot_tokens


def convert_text_tokens_to_bot_tokens(model: str, n_input_tokens: int, n_output_tokens: int):
    """
    Text token – LLM token
    Bot token – currency inside bot (1 bot token price == 1 gpt-3.5-turbo token price)
    """
    baseline_price_per_1000_tokens = config.models["info"]["gpt-3.5-turbo"]["price_per_1000_input_tokens"]

    n_bot_input_tokens = int(n_input_tokens * (config.models["info"][model]["price_per_1000_input_tokens"] / baseline_price_per_1000_tokens))
    n_bot_output_tokens = int(n_output_tokens * (config.models["info"][model]["price_per_1000_output_tokens"] / baseline_price_per_1000_tokens))

    return n_bot_input_tokens + n_bot_output_tokens


def convert_generated_images_to_bot_tokens(model: str, n_generated_images: int):
    baseline_price_per_1000_tokens = config.models["info"]["gpt-3.5-turbo"]["price_per_1000_input_tokens"]

    n_spent_dollars = n_generated_images * (config.models["info"][model]["price_per_1_image"])
    n_bot_tokens = int(n_spent_dollars / (baseline_price_per_1000_tokens / 1000))

    return n_bot_tokens


def convert_transcribed_seconds_to_bot_tokens(model: str, n_transcribed_seconds: float):
    baseline_price_per_1000_tokens = config.models["info"]["gpt-3.5-turbo"]["price_per_1000_input_tokens"]

    n_spent_dollars = n_transcribed_seconds * (config.models["info"][model]["price_per_1_min"] / 60)
    n_bot_tokens = int(n_spent_dollars / (baseline_price_per_1000_tokens / 1000))

    return n_bot_tokens
