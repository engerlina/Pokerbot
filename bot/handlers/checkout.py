from typing import Tuple, Dict

from datetime import datetime, timedelta

from telegram import Update, User, Bot, LabeledPrice
from telegram.ext import CallbackContext
from telegram.constants import ParseMode

from bot import config
from bot.database import db
from bot.config import mxp
from bot.handlers.utils import (
    get_strings,
    add_handler_routines,
)
from bot.handlers.constants import InvoiceData
from bot.handlers.payments_ui import send_user_message_about_n_added_tokens
from bot.handlers.admin import notify_admins_about_successfull_payment
from bot.handlers.tokens import confirm_payment_and_add_tokens
from bot.payment import CryptomusPayment



@add_handler_routines(answer_callback_query=True)
async def send_invoice_handle(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    strings = get_strings(user_id)

    data = InvoiceData.load(update.callback_query.data)
    product = config.products[data.product_key]
    payment_method_type = config.payment_methods[data.payment_method_key]["type"]
    payment_id = db.get_new_unique_payment_id()

    if payment_method_type == "telegram_payments":
        chat_id = update.callback_query.message.chat.id

        # save in database
        db.add_new_payment(
            payment_id=payment_id,
            payment_method=data.payment_method_key,
            payment_method_type=payment_method_type,
            product_key=data.product_key,
            user_id=user_id,
            amount=product["price"],
            currency=product["currency"],
            status="not_paid",
            invoice_url="",
            expired_at=datetime.now() + timedelta(hours=1),
            n_tokens_to_add=product["n_tokens_to_add"]
        )

        # create invoice
        payload = f"{payment_id}"
        prices = [LabeledPrice(product["title"], int(product["price"] * 100))]

        photo_url = None
        if "photo_url" in product and len(product["photo_url"]) > 0:
            photo_url = product["photo_url"]

        # send invoice
        _bot: Bot = context.bot
        await _bot.send_invoice(
            chat_id=chat_id,
            title=product["title"],
            description=product["description"],
            payload=payload,
            provider_token=config.payment_methods[data.payment_method_key]["token"],
            currency=product["currency"],
            prices=prices,
            photo_url=photo_url,
        )
    elif payment_method_type == "cryptomus":
        # create invoice
        cryptomus_payment_instance = CryptomusPayment(
            config.payment_methods[data.payment_method_key]["api_key"],
            config.payment_methods[data.payment_method_key]["merchant_id"]
        )

        invoice_url, status, expired_at = cryptomus_payment_instance.create_invoice(
            payment_id,
            product["price"],
            product["currency"]
        )

        # save in database
        db.add_new_payment(
            payment_id=payment_id,
            payment_method=data.payment_method_key,
            payment_method_type=payment_method_type,
            product_key=data.product_key,
            user_id=user_id,
            amount=product["price"],
            currency=product["currency"],
            status=status,
            invoice_url=invoice_url,
            expired_at=expired_at,
            n_tokens_to_add=product["n_tokens_to_add"],
        )

        # send invoice
        text = strings["invoice_cryptomus"].format(
            invoice_url=invoice_url,
            n_tokens_to_add=product['n_tokens_to_add'],
            support_username=config.support_username
        )
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)
    else:
        raise ValueError(f"Unknown payment method: {payment_method_type}")

    # mxp
    amount = product["price"]
    if product["currency"] == "RUB":  # convert RUB to USD
        amount /= 77.0

    distinct_id, event_name, properties = (
        user_id,
        "send_invoice",
        {
            "token_balance": db.get_user_attribute(user_id, "token_balance"),
            "payment_method": data.payment_method_key,
            "product": data.product_key,
            "payment_id": payment_id,
            "amount": amount,
        }
    )
    mxp.track(distinct_id, event_name, properties)


@add_handler_routines()
async def pre_checkout_handle(update: Update, context: CallbackContext):
    query = update.pre_checkout_query
    await query.answer(ok=True)


@add_handler_routines()
async def successful_payment_handle(update: Update, context: CallbackContext):
    payment_id = int(update.message.successful_payment.invoice_payload)
    await confirm_payment_and_add_tokens(context, payment_id=payment_id)
