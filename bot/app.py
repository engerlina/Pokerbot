import asyncio
import logging

from telegram.ext import (
    ApplicationBuilder,
    CallbackContext,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    JobQueue,
    AIORateLimiter,
    filters,
    Application,
)

from bot import config
from bot.queue.message_queue import MessageQueueWatchdog
from bot.queue.user_task import UserTask
from bot.queue.globals import message_queue
from bot.handlers import constants
from bot.handlers.crypto_payments_status_checkers import (
    check_not_expired_payments_job_fn
)
from bot.handlers.general import (
    start_handle,
    help_handle,
    help_group_chat_handle,
)
from bot.handlers.dialog import (
    message_handle,
    retry_handle,
    cancel_handle,
    new_dialog_handle,
    new_dialog_timeout_confirm_handle,
    voice_message_handle,
)
from bot.handlers.chat_mode import (
    set_chat_mode_handle,
    show_chat_modes_callback_handle,
    show_chat_modes_handle,
)
from bot.handlers.settings import (
    settings_handle,
    set_settings_handle,
)
from bot.handlers.balance import show_balance_handle, speedup_message_queue_button_handle
from bot.handlers.checkout import (
    pre_checkout_handle,
    send_invoice_handle,
    successful_payment_handle,
)
from bot.handlers.payments_ui import (
    invite_friend_handle,
    show_products_handle,
    show_payment_methods_handle,
)
from bot.handlers.admin import (
    user_info_handle,
    add_tokens_handle,
)
from bot.handlers.error import error_handle


logger = logging.getLogger(__name__)


async def post_init(application: Application):
    logger.info("Post init started")

    if not config.enable_message_queue:
        return

    # step 1: run queue
    message_queue.run()

    # step 2: run watchdog
    message_queue_watchdog = MessageQueueWatchdog(
        message_queue=message_queue,
        frequency=config.message_queue_watchdog_frequency,
        bot=application.bot,
        chat_id=config.message_queue_watchdog_chat_id,
        admin_usernames=config.admin_usernames

    )
    message_queue_watchdog.run()
    # save ref to watchdog
    application.bot_data["message_queue_watchdog"] = message_queue_watchdog

    # step 3: load user tasks
    await UserTask.load_user_tasks(application)

    logger.info("Post init finished")


async def pre_stop(application: Application):
    logger.info("Pre stop started")

    if not config.enable_message_queue:
        return

    # step 1: stop queue
    await message_queue.shutdown()

    # step 2: stop watchdog
    await application.bot_data["message_queue_watchdog"].shutdown()

    # step 3: await loaded user tasks
    await UserTask.await_loaded_tasks(application)

    logger.info("Pre stop finished")


def run_bot() -> None:
    class _ApplicationWithPreStop(Application):
        async def stop(self) -> None:
            await pre_stop(self)
            await super().stop()

    application = (
        ApplicationBuilder()
        .application_class(_ApplicationWithPreStop)
        .token(config.telegram_token)
        .concurrent_updates(True)
        .rate_limiter(AIORateLimiter(max_retries=3))
        .http_version("1.1")
        .get_updates_http_version("1.1")
        .read_timeout(30)
        .write_timeout(30)
        .post_init(post_init)
        .build()
    )

    # run job to check not expired payments
    application.job_queue.run_repeating(
        check_not_expired_payments_job_fn,
        interval=config.check_not_expired_payments_update_time,
        name="check_not_expired_payments_job",
    )

    # add handlers
    if len(config.allowed_telegram_usernames) == 0:
        user_filter = filters.ALL
    else:
        user_filter = filters.User(username=config.allowed_telegram_usernames)

    if len(config.admin_usernames) == 0:
        raise ValueError("You must specify at least 1 admin username in config")
    admin_filter = filters.User(username=config.admin_usernames)

    # system
    application.add_handler(CommandHandler("start", start_handle, filters=user_filter))
    application.add_handler(CommandHandler("help", help_handle, filters=user_filter))
    application.add_handler(CommandHandler("help_group_chat", help_group_chat_handle, filters=user_filter))

    # message
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & user_filter, message_handle))
    application.add_handler(CommandHandler("retry", retry_handle, filters=user_filter))
    application.add_handler(CommandHandler("new", new_dialog_handle, filters=user_filter))
    application.add_handler(CallbackQueryHandler(new_dialog_timeout_confirm_handle, pattern=constants.NewDialogButtonData.pattern()))
    application.add_handler(MessageHandler(filters.VOICE & user_filter, voice_message_handle))
    application.add_handler(CommandHandler("cancel", cancel_handle, filters=user_filter))

    # chat mode
    application.add_handler(CommandHandler("mode", show_chat_modes_handle, filters=user_filter))
    application.add_handler(CallbackQueryHandler(show_chat_modes_callback_handle, pattern=constants.ChoosePageChatModesData.pattern()))
    application.add_handler(CallbackQueryHandler(set_chat_mode_handle, pattern=constants.SetChatModeData.pattern()))

    # settings
    application.add_handler(CommandHandler("settings", settings_handle, filters=user_filter))
    application.add_handler(CallbackQueryHandler(set_settings_handle, pattern=constants.SettingsData.pattern()))

    # payment
    application.add_handler(CommandHandler("balance", show_balance_handle, filters=user_filter))
    application.add_handler(CallbackQueryHandler(speedup_message_queue_button_handle, pattern=constants.SpeedupMessageQueueButtonData.pattern()))

    application.add_handler(CallbackQueryHandler(show_payment_methods_handle, pattern=constants.ShowPaymentMethodsData.pattern()))
    application.add_handler(CallbackQueryHandler(show_products_handle, pattern=constants.ShowProductsData.pattern()))
    application.add_handler(CallbackQueryHandler(invite_friend_handle, pattern=constants.InviteFriendData.pattern()))
    application.add_handler(CallbackQueryHandler(send_invoice_handle, pattern=constants.InvoiceData.pattern()))

    application.add_handler(PreCheckoutQueryHandler(pre_checkout_handle))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT & user_filter, successful_payment_handle))

    # admin
    application.add_handler(CommandHandler("add_tokens", add_tokens_handle, filters=admin_filter))
    application.add_handler(CommandHandler("info", user_info_handle, filters=user_filter))
    application.add_error_handler(error_handle)

    # start the bot
    application.run_polling()
