import asyncio
import logging

from telegram.ext import CallbackContext

from bot import config
from bot.handlers.utils import thread_pool
from bot.database import db
from bot.payment import CryptomusPayment
from bot.handlers.tokens import confirm_payment_and_add_tokens


logger = logging.getLogger(__name__)


async def check_not_expired_payments_job_fn(context: CallbackContext):
    def _get_payment_ids_to_confirm_fn():
        payment_dicts = db.get_all_not_expried_payment_dicts(time_margin_in_seconds=12*60*60)  # give 12 hour margin after expiration

        cryptomus_payment_instance = CryptomusPayment(
            config.payment_methods["cryptomus"]["api_key"],
            config.payment_methods["cryptomus"]["merchant_id"]
        )

        payment_ids_to_confirm = []
        for payment_dict in payment_dicts:
            if payment_dict["payment_method_type"] == "cryptomus":
                try:
                    is_paid = cryptomus_payment_instance.check_invoice_status(payment_dict["_id"])
                except Exception as e:
                    logger.error(f"Failed to check cryptomus invoice status. Reason: {e}")
                    is_paid = False

                if is_paid:
                    payment_ids_to_confirm.append(payment_dict["_id"])

        return payment_ids_to_confirm

    loop = asyncio.get_running_loop()
    payment_ids_to_confirm = await loop.run_in_executor(thread_pool, _get_payment_ids_to_confirm_fn)

    for payment_id in payment_ids_to_confirm:
        try:
            await confirm_payment_and_add_tokens(context, payment_id=payment_id)
        except Exception as e:
            logger.error(f"Failed to confirm payment and add tokens. Reason: {e}")
