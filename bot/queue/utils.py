from typing import Optional, Dict, Any
from dataclasses import dataclass
from enum import Enum, IntEnum

import asyncio
from datetime import datetime

from telegram import Bot

from bot.database import UserId, ChatId


MessageQueueTaskId = str


class MessageQueueTaskStatus(Enum):
    PENDING = 1
    RUNNING = 2


class MessageQueueTaskPriority(IntEnum):  # using IntEnum to natively sort by value
    LOW = 10
    MEDIUM = 5
    HIGH = 0


@dataclass
class MessageQueueTask:
    id: MessageQueueTaskId
    status: MessageQueueTaskStatus
    bot: Bot
    user_id: UserId
    chat_id: ChatId
    message_text: str
    do_subtract_tokens: bool
    created_at: datetime
    priority: MessageQueueTaskPriority = MessageQueueTaskPriority.LOW
    asyncio_task: Optional[asyncio.Task] = None  # set when started running
    display_progress_asyncio_task: Optional[asyncio.Task] = None  # set when put to queue
    is_completed_event: Optional[asyncio.Event] = None  # set by queue put method

    def dump(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "chat_id": self.chat_id,
            "message_text": self.message_text,
            "do_subtract_tokens": self.do_subtract_tokens,
        }
