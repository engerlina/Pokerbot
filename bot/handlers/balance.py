from typing import Optional
from enum import Enum
import logging

from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext
from telegram.constants import ParseMode

from bot import config
from bot.config import mxp
from bot.database import db, UserId, ChatId
from bot.handlers.utils import add_handler_routines, get_strings, send_reply
from bot.handlers.tokens import get_total_n_used_bot_tokens
from bot.handlers.constants import ShowPaymentMethodsData, InviteFriendData

from bot.queue.globals import message_queue
from bot.queue.user_task import UserTaskType


logger = logging.getLogger(__name__)


class ShowBalanceSource(Enum):
    COMMAND = 1
    NOT_ENOUGH_TOKENS = 2
    SPEED_UP_MESSAGE_QUEUE = 3
    PRO_CHAT_MODE = 4
    VOICE_MESSAGE = 5
    MESSAGE_QUEUE_IS_FULL = 6


def check_if_user_has_enough_tokens(user_id: UserId) -> bool:
    token_balance = db.get_user_attribute(user_id, "token_balance")
    return token_balance > 0


@add_handler_routines()
async def speedup_message_queue_button_handle(update: Update, context: CallbackContext):
    await show_balance(
        bot=context.bot,
        user_id=update.effective_user.id,
        chat_id=update.effective_chat.id,
        source=ShowBalanceSource.SPEED_UP_MESSAGE_QUEUE,
    )


async def show_balance(
    bot: Bot,
    user_id: UserId,
    chat_id: ChatId,
    source: ShowBalanceSource = ShowBalanceSource.COMMAND,
    source_chat_mode_key: Optional[str] = None
):
    lang = db.get_user_attribute(user_id, "lang")
    strings = get_strings(user_id)

    if not config.enable_message_queue and source == ShowBalanceSource.COMMAND:
        source = ShowBalanceSource.NOT_ENOUGH_TOKENS

    token_balance = db.get_user_attribute(user_id, "token_balance")
    total_n_used_bot_tokens = get_total_n_used_bot_tokens(user_id)
    if token_balance > 0:
        text = strings["you_have_have_n_tokens_left"].format(
            token_balance=token_balance,
            total_n_used_bot_tokens=total_n_used_bot_tokens
        )
    else:
        if source == ShowBalanceSource.COMMAND:
            text = strings["you_have_have_no_tokens_left"].format(
                total_n_used_bot_tokens=total_n_used_bot_tokens
            )
            text += "\n\n"
            text += strings["call_to_buy_tokens"]
        elif source == ShowBalanceSource.NOT_ENOUGH_TOKENS:
            text = strings["you_have_have_no_tokens_left"].format(
                total_n_used_bot_tokens=total_n_used_bot_tokens
            )
            text += "\n\n"
            text += strings["to_continue_using_the_bot"]
        elif source == ShowBalanceSource.SPEED_UP_MESSAGE_QUEUE:
            text = strings["call_to_buy_tokens"]
        elif source == ShowBalanceSource.PRO_CHAT_MODE:
            text = strings["you_have_have_no_tokens_left_for_pro_chat_mode"].format(
                chat_mode_name=config.chat_modes[source_chat_mode_key]["name"][lang],
                total_n_used_bot_tokens=total_n_used_bot_tokens
            )
            text += "\n\n"
            text += strings["call_to_buy_tokens"]
        elif source == ShowBalanceSource.VOICE_MESSAGE:
            text = strings["you_have_have_no_tokens_left_for_voice_message"].format(
                total_n_used_bot_tokens=total_n_used_bot_tokens
            )
            text += "\n\n"
            text += strings["call_to_buy_tokens"]
        elif source == ShowBalanceSource.MESSAGE_QUEUE_IS_FULL:
            text = strings["message_queue_is_full"].format(
                message_queue_max_size=config.message_queue_max_size
            )
            text += "\n\n"
            text += strings["call_to_buy_tokens"]
        else:
            raise ValueError(f"Unknown source: {source}")

    buttons = []
    buttons.append(InlineKeyboardButton(strings["get_tokens_button"], callback_data=ShowPaymentMethodsData.prefix))
    if config.enable_ref_system:
        buttons.append(InlineKeyboardButton(strings["invite_friend_button"], callback_data=InviteFriendData.prefix))
    reply_markup = InlineKeyboardMarkup([[x] for x in buttons])

    await send_reply(
        bot=bot,
        chat_id=chat_id,
        user_id=user_id,
        text=text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )

    # mxp
    distinct_id, event_name, properties = (
        user_id,
        "show_balance",
        {"token_balance": token_balance},
    )
    mxp.track(distinct_id, event_name, properties)


@add_handler_routines()
async def show_balance_handle(
    update: Update,
    context: CallbackContext,
):
    await show_balance(
        bot=context.bot,
        user_id=update.effective_user.id,
        chat_id=update.effective_chat.id,
        source=ShowBalanceSource.COMMAND,
    )


async def show_balance_if_message_queue_is_full(
    bot: Bot,
    user_id: UserId,
    chat_id: ChatId,
    user_task_type: UserTaskType,
) -> bool:
    if (
        (user_task_type == UserTaskType.MESSAGE_QUEUE) and
        (config.message_queue_max_size is not None) and
        (len(message_queue) >= config.message_queue_max_size)
    ):
        await show_balance(
            bot=bot,
            user_id=user_id,
            chat_id=chat_id,
            source=ShowBalanceSource.MESSAGE_QUEUE_IS_FULL,
        )
        return True  # message queue is full
    else:
        return False
