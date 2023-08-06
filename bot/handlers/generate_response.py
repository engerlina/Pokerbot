from typing import Tuple, Dict, Optional

import asyncio
import logging
from datetime import datetime
from pathlib import Path

import openai

import telegram
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode


from llm_tools.tokens import TokenExpenses

from bot import config
from bot.database import db, UserId, ChatId
from bot.config import mxp
from bot.queue.utils import MessageQueueTaskId, MessageQueueTaskStatus
from bot.handlers.utils import get_strings, send_reply, ignore_message_not_modified_error
from bot.handlers.tokens import (
    convert_generated_images_to_bot_tokens,
    convert_text_tokens_to_bot_tokens,
)
from bot.handlers.constants import SpeedupMessageQueueButtonData
from bot import openai_utils
from bot.openai_utils import get_total_token_expenses


logger = logging.getLogger(__name__)


async def generate_and_send_text(
    bot: Bot,
    user_id: UserId,
    chat_id: ChatId,
    message_text: str,
    token_expenses: TokenExpenses,
) -> None:
    # fetch prerequisites
    strings = get_strings(user_id)
    chat_mode = db.get_user_attribute(user_id, "current_chat_mode")
    dialog_messages = db.get_dialog_messages(user_id, dialog_id=None)
    parse_mode = {
        "html": ParseMode.HTML,
        "markdown": ParseMode.MARKDOWN
    }[config.chat_modes[chat_mode]["parse_mode"]]
    current_model = db.get_user_attribute(user_id, "current_model")

    # send typing action
    await bot.send_chat_action(chat_id=chat_id, action="typing")

    # make up streaming generator
    chatgpt_instance = openai_utils.ChatGPT(model=current_model)
    gen = chatgpt_instance.send_message_stream(
        message_text,
        dialog_messages=dialog_messages,
        chat_mode=chat_mode,
        token_expenses=token_expenses,
    )

    # send placeholder to user
    placeholder_message = await send_reply(
        bot=bot,
        user_id=user_id,
        chat_id=chat_id,
        text="...",
    )
    if placeholder_message is None:
        return None

    async def _update_placeholder_message(text: str):
        return await send_reply(
            placeholder_message,
            try_edit=True,
            try_no_parse_mode=True,
            ignore_message_not_modified_error=True,
            text=text,
            parse_mode=parse_mode,
        )

    # update placeholder message on the fly
    displayed_answer = ""
    async for gen_item in gen:
        answer, n_first_dialog_messages_removed = gen_item
        answer = answer[:4096]  # telegram message limit

        # update only when 100 new symbols are ready
        if abs(len(answer) - len(displayed_answer)) < 100:
            continue

        placeholder_message = await _update_placeholder_message(answer + "...")
        if placeholder_message is None:
            return None
        await asyncio.sleep(0.1)  # wait a bit to avoid flooding

        displayed_answer = answer

    # send final answer
    if len(answer) != 0:
        await _update_placeholder_message(answer)
    else:
        text = strings["model_answer_is_empty"]
        await _update_placeholder_message(text)

    # update dialog
    new_dialog_message = {"user": message_text, "bot": answer, "date": datetime.now()}
    db.set_dialog_messages(
        user_id,
        db.get_dialog_messages(user_id, dialog_id=None) + [new_dialog_message],
        dialog_id=None
    )

    # send notification if some messages were removed from the context
    if n_first_dialog_messages_removed > 0:
        if n_first_dialog_messages_removed == 1:
            text = strings["dialog_is_too_long_first_message"]
        else:
            text = strings["dialog_is_too_long"].format(
                n_first_dialog_messages_removed=n_first_dialog_messages_removed
            )
        await send_reply(
            bot=bot,
            user_id=user_id,
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
        )


async def generate_and_send_images(
    bot: Bot,
    user_id: UserId,
    chat_id: ChatId,
    message_text: str,
) -> Tuple[int, Optional[Exception]]:
    n_generated_images = 0
    try:
        strings = get_strings(user_id)

        try:
            image_urls = await openai_utils.generate_images(message_text, n_images=config.return_n_generated_images)
        except openai.error.InvalidRequestError as e:
            if "was rejected as a result of our safety system" in str(e).lower():
                text = strings["request_doesnt_comply"]
                await send_reply(
                    bot=bot,
                    user_id=user_id,
                    chat_id=chat_id,
                    text=text,
                    parse_mode=ParseMode.HTML
                )

                # CAVEAT: spending tokens here to avoid abuse
                return config.return_n_generated_images, None
            else:
                raise

        n_generated_images = config.return_n_generated_images

        for i, image_url in enumerate(image_urls):
            await bot.send_chat_action(chat_id=chat_id, action="upload_photo")

            text = f"<i>{message_text}</i>"
            if len(image_urls) > 1:
                text += f" {i + 1}/{len(image_urls)}"
            text += "\n"
            text += strings["image_created_with"].format(bot_username=config.bot_username)

            await bot.send_photo(
                chat_id=chat_id,
                photo=image_url,
                caption=text,
                parse_mode=ParseMode.HTML
            )
    except Exception as e:
        return n_generated_images, e
    else:
        return n_generated_images, None


async def generate_response(
    bot: Bot,
    user_id: UserId,
    chat_id: ChatId,
    message_text: str,
    do_subtract_tokens: bool = True,
):
    chat_mode = db.get_user_attribute(user_id, "current_chat_mode")

    db.set_user_attribute(user_id, "last_message_ts", datetime.now())
    current_model = db.get_user_attribute(user_id, "current_model")
    initial_balance = db.get_user_attribute(user_id, "token_balance")

    if chat_mode == 'artist':
        (
            n_generated_images,
            error,
        ) = await generate_and_send_images(
            bot=bot,
            user_id=user_id,
            chat_id=chat_id,
            message_text=message_text,
        )

        _n = db.get_user_attribute(user_id, "n_generated_images")
        db.set_user_attribute(user_id, "n_generated_images", _n + n_generated_images)
        n_used_bot_tokens = convert_generated_images_to_bot_tokens("dalle-2", n_generated_images)

        if do_subtract_tokens:
            new_balance = max(0, initial_balance - n_used_bot_tokens)
            db.set_user_attribute(user_id, "token_balance", new_balance)

        distinct_id, event_name, properties = (
            user_id,
            "generate_image",
            {"prompt": message_text, "n_used_bot_tokens": n_used_bot_tokens}
        )
        mxp.track(distinct_id, event_name, properties)
        if error is not None:
            raise error
    else:
        token_expenses = TokenExpenses()
        try:
            await generate_and_send_text(
                bot=bot,
                user_id=user_id,
                chat_id=chat_id,
                message_text=message_text,
                token_expenses=token_expenses,
            )
        finally:
            n_input_tokens, n_output_tokens = get_total_token_expenses(token_expenses)

            db.update_n_used_tokens(user_id, current_model, n_input_tokens, n_output_tokens)
            n_used_bot_tokens = convert_text_tokens_to_bot_tokens(current_model, n_input_tokens, n_output_tokens)

            if do_subtract_tokens:
                new_balance = max(0, initial_balance - n_used_bot_tokens)
                db.set_user_attribute(user_id, "token_balance", new_balance)

        # mxp
        distinct_id, event_name, properties = (
            user_id,
            "send_message_done",
            {
                "dialog_id": db.get_user_attribute(user_id, "current_dialog_id"),
                "chat_mode": chat_mode,
                "model": current_model,
                "n_used_bot_tokens": n_used_bot_tokens
            }
        )
        mxp.track(distinct_id, event_name, properties)

    return n_used_bot_tokens


async def display_message_queue_task_progress(
    message_queue: "MessageQueueWithTokenBudget",  # can't set type MessageQueueWithTokenBudget here because of circular imports
    message_queue_task_id: MessageQueueTaskId
):
    try:
        message_queue_task, _ = message_queue.get_task(message_queue_task_id)
        if message_queue_task is None:
            return

        bot = message_queue_task.bot
        user_id = message_queue_task.user_id
        chat_id = message_queue_task.chat_id

        strings = get_strings(user_id)

        progress_message = None

        while True:
            message_queue_task, message_queue_task_index = message_queue.get_task(message_queue_task_id)
            if (message_queue_task is None) or message_queue_task.status != MessageQueueTaskStatus.PENDING:
                raise asyncio.CancelledError

            text = strings["message_queue_progress_message"].format(
                message_queue_position=message_queue_task_index + 1,
                n_tasks_in_message_queue=len(message_queue),
                message_queue_progress_update_time=config.message_queue_progress_update_time
            )

            reply_markup = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    strings["message_queue_progress_message_button"],
                    callback_data=SpeedupMessageQueueButtonData().dump()
                )
            ]])

            if progress_message is None:
                # first time to send progress message
                progress_message = await send_reply(
                    bot=bot,
                    user_id=user_id,
                    chat_id=chat_id,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML
                )

                # if user forbids to send messages, cancel the task
                if progress_message is None:
                    raise asyncio.CancelledError
            else:
                try:
                    with ignore_message_not_modified_error():
                        await bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=progress_message.id,
                            text=text,
                            reply_markup=reply_markup,
                            parse_mode=ParseMode.HTML
                        )
                except Exception as e:
                    if "message to edit not found" in str(e).lower():
                        raise asyncio.CancelledError
                    else:
                        logger.error(f"Failed to edit progress message. Reason: {e}")

            await asyncio.sleep(config.message_queue_progress_update_time)
    except asyncio.CancelledError:
        logger.info(f"Display progress task was cancelled")
    except Exception as e:
        logger.exception(e)
    finally:
        try:
            await bot.delete_message(
                chat_id=chat_id,
                message_id=progress_message.id
            )
        except Exception as e:
            logger.error(f"Failed to delete progress message. Reason: {e}")
