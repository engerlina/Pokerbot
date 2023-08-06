import logging
from datetime import datetime

import telegram
from telegram import Update
from telegram.ext import CallbackContext
from telegram.constants import ParseMode

from bot import config
from bot.database import db
from bot.handlers.utils import add_handler_routines
from bot.handlers.payments_ui import send_user_message_about_n_added_tokens


logger = logging.getLogger(__name__)


@add_handler_routines()
async def add_tokens_handle(update: Update, context: CallbackContext):
    username_or_user_id, n_tokens_to_add = context.args
    n_tokens_to_add = int(n_tokens_to_add)

    try:
        user_id = int(username_or_user_id)
        user_dict = db.user_collection.find_one({"_id": user_id})
    except:
        username = username_or_user_id
        user_dict = db.user_collection.find_one({"username": username})

    if user_dict is None:
        text = f"Username or user_id <b>{username_or_user_id}</b> not found in DB"
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)
        return

    # add tokens
    _balance = db.get_user_attribute(user_dict["_id"], "token_balance")
    db.set_user_attribute(user_dict["_id"], "token_balance", _balance + n_tokens_to_add)

    # save in database
    payment_id = db.get_new_unique_payment_id()
    db.add_new_payment(
        payment_id=payment_id,
        payment_method="add_tokens",
        payment_method_type="add_tokens",
        product_key="add_tokens",
        user_id=user_dict["_id"],
        amount=0.0,
        currency=None,
        status="paid",
        invoice_url="",
        expired_at=datetime.now(),
        n_tokens_to_add=n_tokens_to_add,
    )

    # send message to user
    await send_user_message_about_n_added_tokens(
        context,
        n_tokens_to_add,
        chat_id=user_dict["chat_id"],
        user_id=user_dict["_id"]
    )

    # send message to admin
    text = f"🟣 <b>{n_tokens_to_add}</b> tokens were successfully added to <b>{username_or_user_id}</b> balance!"
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


@add_handler_routines()
async def user_info_handle(update: Update, context: CallbackContext):
    user = update.effective_user

    text = "User info:\n"
    text += f"- <b>user_id</b>: {user.id}\n"
    text += f"- <b>username</b>: {user.username}\n"
    text += f"- <b>chat_id</b>: {update.effective_message.chat_id}\n"

    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)
    if config.admin_chat_id is not None:
        await context.bot.send_message(config.admin_chat_id, text, parse_mode=ParseMode.HTML)


async def notify_admins_about_successfull_payment(
    context: CallbackContext,
    payment_id: int
):
    if config.admin_chat_id is None:
        return
    text = "🟣 Successfull payment:\n"

    payment_dict = db.payment_collection.find_one({"_id": payment_id})
    for key in ["amount", "currency", "product_key", "n_tokens_to_add", "payment_method", "user_id", "status"]:
        text += f"- {key}: <b>{payment_dict[key]}</b>\n"

    user_dict = db.user_collection.find_one({"_id": payment_dict["user_id"]})
    if user_dict["username"] is not None:
        text += f"- username: @{user_dict['username']}\n"

    # tag admins
    for admin_username in config.admin_usernames:
        if not admin_username.startswith("@"):
            admin_username = "@" + admin_username
        text += f"\n{admin_username}"

    await context.bot.send_message(config.admin_chat_id, text, parse_mode=ParseMode.HTML)
