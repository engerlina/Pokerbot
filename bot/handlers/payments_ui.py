from typing import Tuple, Dict, Optional

from telegram import Update, User, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import CallbackContext
from telegram.constants import ParseMode

from bot import config
from bot.database import db, UserId, ChatId
from bot.config import mxp
from bot.handlers.utils import (
    get_strings,
    add_handler_routines,
)
from bot.handlers.constants import ShowProductsData, InvoiceData


async def send_user_message_about_n_added_tokens(
    context: CallbackContext,
    n_tokens_added: int,
    chat_id: Optional[ChatId] = None,
    user_id: Optional[UserId] = None,
    joined_friend_user_id: Optional[UserId] = None,
):
    if chat_id is None:
        if user_id is None:
            raise ValueError(f"chat_id and user_id can't be None simultaneously")
        chat_id = db.get_user_attribute(user_id, "chat_id")

    strings = get_strings(user_id)

    if joined_friend_user_id is not None:
        text = strings["your_friend_joined"].format(friend_user_id=joined_friend_user_id)
        await context.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)

    text = strings["n_tokens_added"].format(n_tokens_added=n_tokens_added)
    await context.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)


@add_handler_routines(answer_callback_query=True)
async def show_payment_methods_handle(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    strings = get_strings(user_id)

    buttons = [
        InlineKeyboardButton(
            payment_method_values["name"][strings.lang],
            callback_data=ShowProductsData(payment_method_key).dump(),
        )
        for payment_method_key, payment_method_values in config.payment_methods.items()
    ]

    await update.effective_message.reply_text(
        strings["choose_payment_method"],
        reply_markup=InlineKeyboardMarkup([[x] for x in buttons]),
        parse_mode=ParseMode.HTML,
    )

    # mxp
    distinct_id, event_name, properties = (
        user_id,
        "show_payment_methods",
        {"token_balance": db.get_user_attribute(user_id, "token_balance")}
    )
    mxp.track(distinct_id, event_name, properties)


@add_handler_routines(answer_callback_query=True)
async def show_products_handle(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    strings = get_strings(user_id)

    payment_method_key = ShowProductsData.load(update.callback_query.data).payment_method_key
    product_keys = config.payment_methods[payment_method_key]["product_keys"]
    buttons = [
        InlineKeyboardButton(
            config.products[product_key]["title_on_button"],
            callback_data=InvoiceData(payment_method_key, product_key).dump(),
        )
        for product_key in product_keys
    ]

    await update.effective_message.reply_text(
        strings["choose_product"],
        reply_markup=InlineKeyboardMarkup([[x] for x in buttons]),
        parse_mode=ParseMode.HTML
    )

    # mxp
    distinct_id, event_name, properties = (
        user_id,
        "show_products",
        {
            "token_balance": db.get_user_attribute(user_id, "token_balance"),
            "payment_method": payment_method_key
        }
    )
    mxp.track(distinct_id, event_name, properties)


@add_handler_routines(answer_callback_query=True)
async def invite_friend_handle(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    strings = get_strings(user_id)

    text = strings["invite_friend"].format(
        n_tokens_to_add_to_ref=config.n_tokens_to_add_to_ref,
        max_invites_per_user=config.max_invites_per_user,
        n_already_invited_users=len(db.get_user_attribute(user_id, "invites"))
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)

    bot_username_without_at = config.bot_username[1:]  # t.me/@... doesn't work sometimes
    invite_url = f"https://t.me/{bot_username_without_at}?start=ref={user_id}"
    text = strings["invite_message"].format(invite_url=invite_url, bot_name=config.bot_name)
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)

    # mxp
    distinct_id, event_name, properties = (
        user_id,
        "invite_friend",
        {
            "token_balance": db.get_user_attribute(user_id, "token_balance"),
        }
    )
    mxp.track(distinct_id, event_name, properties)
