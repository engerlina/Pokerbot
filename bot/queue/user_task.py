from typing import Optional, List, Dict, Any
from enum import Enum

import json
import asyncio
import logging

from telegram import Bot
from telegram.ext import Application

from bot.utils import cancel_asyncio_task_and_wait
from bot.queue.utils import MessageQueueTaskId, MessageQueueTaskPriority
from bot.queue.globals import message_queue, user_tasks
from bot.handlers.generate_response import generate_response
from bot.handlers.utils import user_semaphores
from bot.database import UserId, ChatId
from bot.config import user_tasks_dump_path


logger = logging.getLogger(__name__)


class UserTaskType(Enum):
     ASYNCIO_QUEUE = 1
     MESSAGE_QUEUE = 2


class UserTask:
    def __init__(
        self,
        type: UserTaskType,
        asyncio_queue_task: Optional[asyncio.Task] = None,
        message_queue_task_id: Optional[MessageQueueTaskId] = None
    ):
        self.type = type
        self.asyncio_queue_task = asyncio_queue_task
        self.message_queue_task_id = message_queue_task_id

    async def cancel(self):
        if self.type == UserTaskType.ASYNCIO_QUEUE:
            await cancel_asyncio_task_and_wait(self.asyncio_queue_task)
        elif self.type == UserTaskType.MESSAGE_QUEUE:
            await message_queue.cancel_task(self.message_queue_task_id)
        else:
             raise ValueError(f"Unknown UserTaskType={self.type}")

    @staticmethod
    async def load_user_tasks(
        application: Application,
    ):
        if not user_tasks_dump_path.exists():
            logger.info("No user tasks dump file to load, skipping")
            return

        try:
            with open(user_tasks_dump_path, "r") as f:
                tasks_dump = json.load(f)
        except:
            logger.exception("Failed to load user tasks from dump")
            return

        await UserTask.send_user_tasks_to_queue(
            application=application,
            tasks_dump=tasks_dump,
        )
        logger.info(f"Loaded {len(tasks_dump)} into queue")

    @staticmethod
    async def await_loaded_tasks(
        application: Application,
    ):
        for task in application.bot_data.get("loaded_user_tasks", []):
            try:
                await task
            except:
                pass

    @staticmethod
    async def send_user_tasks_to_queue(
        application: Application,
        tasks_dump: List[Dict[str, Any]],
    ) -> None:
        tasks = []
        for task_dump in tasks_dump:
            tasks.append(asyncio.create_task(create_and_run_user_task(
                user_task_type=UserTaskType.MESSAGE_QUEUE,
                bot=application.bot,
                **task_dump,
            )))

        # save reference somewhere so thqt tasks are not cancelled
        application.bot_data["loaded_user_tasks"] = tasks


async def create_and_run_user_task(
    user_task_type: UserTaskType,
    bot: Bot,
    user_id: UserId,
    chat_id: ChatId,
    message_text: str,
    do_subtract_tokens: bool
):
    async with user_semaphores[user_id]:
        await _create_and_run_user_task_impl(
            user_task_type=user_task_type,
            bot=bot,
            user_id=user_id,
            chat_id=chat_id,
            message_text=message_text,
            do_subtract_tokens=do_subtract_tokens,
        )


async def _create_and_run_user_task_impl(
    user_task_type: UserTaskType,
    bot: Bot,
    user_id: UserId,
    chat_id: ChatId,
    message_text: str,
    do_subtract_tokens: bool
):
    if user_task_type == UserTaskType.ASYNCIO_QUEUE:
        asyncio_queue_task = asyncio.create_task(generate_response(
            bot=bot,
            user_id=user_id,
            chat_id=chat_id,
            message_text=message_text,
            do_subtract_tokens=do_subtract_tokens
        ))

        user_tasks[user_id] = UserTask(
            type=UserTaskType.ASYNCIO_QUEUE,
            asyncio_queue_task=asyncio_queue_task
        )

        try:
            await asyncio_queue_task
        except asyncio.CancelledError:
            pass
        finally:
            if user_id in user_tasks:
                del user_tasks[user_id]
    elif user_task_type == UserTaskType.MESSAGE_QUEUE:
        message_queue_task = message_queue.put_task(
            bot=bot,
            user_id=user_id,
            chat_id=chat_id,
            message_text=message_text,
            do_subtract_tokens=do_subtract_tokens,
            priority=MessageQueueTaskPriority.LOW,
        )

        user_tasks[user_id] = UserTask(
            type=UserTaskType.MESSAGE_QUEUE,
            message_queue_task_id=message_queue_task.id
        )

        try:
            await message_queue_task.is_completed_event.wait()
        except:
            raise
        finally:
            if user_id in user_tasks:
                del user_tasks[user_id]
    else:
        raise ValueError(f"Unknown user task type: {user_task_type}")
