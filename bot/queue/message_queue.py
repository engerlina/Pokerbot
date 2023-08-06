from typing import List, Tuple, Optional, Dict, Any
from collections import deque
import asyncio
import logging
from datetime import datetime, timedelta
import uuid
import numpy as np
import json

from telegram import Bot
from telegram.constants import ParseMode

from bot import config
from bot.config import mxp
from bot.config import user_tasks_dump_path
from bot.database import UserId, ChatId
from bot.utils import cancel_asyncio_task_and_wait
from bot.queue.utils import MessageQueueTask, MessageQueueTaskId, MessageQueueTaskStatus, MessageQueueTaskPriority
from bot.handlers.generate_response import generate_response, display_message_queue_task_progress
from bot.handlers.error import send_message_about_error_to_user_and_admin


logger = logging.getLogger(__name__)


class MessageQueueWithTokenBudget:
    STATISTICS_MAX_HISTORY_LEN = 1000

    def __init__(
        self,
        token_budget_per_day=10000000,
        initial_token_budget=0
    ):
        self.token_budget_per_second = token_budget_per_day / (24 * 60 * 60)

        self._token_budget: int = initial_token_budget
        self._last_token_budget_update_datetime = datetime.utcnow()

        self.queue = []
        self.put_event = asyncio.Event()  # fired when new task is added to queue
        self.is_active = True
        self.last_asyncio_task = None

        # statistics
        self.statistics = {
            "put_tasks": deque(maxlen=self.STATISTICS_MAX_HISTORY_LEN),
            "finished_tasks": deque(maxlen=self.STATISTICS_MAX_HISTORY_LEN)
        }

    def get_token_budget(self) -> int:
        self._update_token_budget()
        return self._token_budget

    def _update_token_budget(self) -> None:
        now_datetime = datetime.utcnow()
        n_seconds_since_last_update = (now_datetime - self._last_token_budget_update_datetime).total_seconds()

        self._token_budget += n_seconds_since_last_update * self.token_budget_per_second
        self._last_token_budget_update_datetime = now_datetime

    def put_task(
        self,
        bot: Bot,
        user_id: UserId,
        chat_id: ChatId,
        message_text: str,
        do_subtract_tokens: bool,
        priority: MessageQueueTaskPriority = MessageQueueTaskPriority.LOW,
    ) -> MessageQueueTask:
        if not self.is_active:
            raise RuntimeError("MessageQueue is disabled")

        # build task
        task = MessageQueueTask(
            id=str(uuid.uuid4()),
            status=MessageQueueTaskStatus.PENDING,
            bot=bot,
            user_id=user_id,
            chat_id=chat_id,
            message_text=message_text,
            do_subtract_tokens=do_subtract_tokens,
            created_at=datetime.utcnow(),
            priority=priority,
            is_completed_event=asyncio.Event()
        )

        # add to queue
        self.queue.append(task)
        self.put_event.set()

        # run display progress task
        task.display_progress_asyncio_task = asyncio.create_task(
            display_message_queue_task_progress(
                message_queue=self,
                message_queue_task_id=task.id
            )
        )

        self._log_started_task(task)
        return task

    def _log_started_task(self, task: MessageQueueTask) -> None:
        # statistics
        self.statistics["put_tasks"].append({
            "id": task.id,
            "created_at": task.created_at
        })

        # mxp
        distinct_id, event_name, properties = (
            task.user_id,
            "put_task_in_message_queue",
            {
                "id": task.id,
                "user_id": task.user_id,
                "chat_id": task.chat_id,
                "created_at": task.created_at.timestamp(),
                "priority": task.priority.name,
                "queue_size": len(self.queue),
                "token_budget": self.get_token_budget(),
            }
        )
        mxp.track(distinct_id, event_name, properties)

    def _reprioritize_queue(self) -> None:
        self.queue.sort(key=lambda x: (x.priority, x.created_at))

    def get_next_task(self) -> MessageQueueTask:
        if len(self.queue) == 0:
            return None

        self._reprioritize_queue()
        task = self.queue[0]
        return task

    def _get_task_index(self, task_id: MessageQueueTaskId) -> int:
        try:
            index = [x.id for x in self.queue].index(task_id)
        except ValueError:
            # task id not in list
            return None

        return index

    def get_task(self, task_id: MessageQueueTaskId) -> Tuple[
        Optional[MessageQueueTask],
        Optional[int]
    ]:
        task_index = self._get_task_index(task_id)
        if task_index is not None:
            return self.queue[task_index], task_index
        else:
            return None, None

    def _remove_task_from_queue(self, task_id: MessageQueueTaskId) -> None:
        task_index = self._get_task_index(task_id)
        if task_index is not None:
            del self.queue[task_index]

    async def _finalize_and_remove_task(self, task_id: MessageQueueTaskId) -> None:
        task, _ = self.get_task(task_id)
        if task is None:
            return

        # cancel display progress task
        if task.display_progress_asyncio_task is not None:
            await cancel_asyncio_task_and_wait(task.display_progress_asyncio_task)

        # remove from queue
        self._remove_task_from_queue(task_id)

        # fire is_completed_event event
        task.is_completed_event.set()


    async def cancel_task(self, task_id: MessageQueueTaskId) -> None:
        task, task_index = self.get_task(task_id)
        if task is None:
            return

        if task.asyncio_task is not None:
            await cancel_asyncio_task_and_wait(task.asyncio_task)

        await self._finalize_and_remove_task(task_id)

    def __len__(self) -> int:
        return len(self.queue)

    def has(self, task_id) -> bool:
        return any(x for x in self.queue if x.id == task_id)

    def run(self) -> None:
        self._worker_job_task = asyncio.create_task(self._worker_job())

    def disable(self) -> None:
        self.is_active = False
        logger.info("Queue is disabled and will not process new tasks")

    async def shutdown(self) -> None:
        logger.info("Shutting down message queue")
        self.disable()
        try:
            await self.last_asyncio_task
        except:
            pass
        for task in self.queue:
            # set is completed event so that all awaiting handlers are unblocked
            task.is_completed_event.set()

        logger.info("Awaiting for worker job to finish")

        try:
            self._worker_job_task.cancel()
            await self._worker_job_task
        except:
            pass

        logger.info("Dumping user tasks")
        with open(user_tasks_dump_path, "w") as f:
            dumped_tasks = self.dump()
            json.dump(dumped_tasks, f, indent=4, ensure_ascii=False)
            logger.info(f"Dumped {len(dumped_tasks)} user tasks")

    def dump(self) -> List[Dict[str, Any]]:
        return [x.dump() for x in self.queue]

    async def _worker_job(self):
        while self.is_active:
            try:
                await asyncio.wait_for(
                    self._fetch_and_process_new_task(),
                    timeout=1.2 * config.message_queue_task_timeout,
                )
            except:
                logger.info("Error while fetching and processing new task. Restarting _fetch_and_process_new_task()")
                pass

    async def _fetch_and_process_new_task(self):
        token_budget = self.get_token_budget()
        if token_budget > 0:
            if len(self.queue) == 0:
                await asyncio.wait_for(
                    self.put_event.wait(),
                    timeout=10,
                )
                self.put_event.clear()
                return

            task = self.get_next_task()
            assert task is not None

            asyncio_task = asyncio.create_task(generate_response(
                bot=task.bot,
                user_id=task.user_id,
                chat_id=task.chat_id,
                message_text=task.message_text,
                do_subtract_tokens=task.do_subtract_tokens
            ))
            self.last_asyncio_task = task.asyncio_task = asyncio_task
            task.status = MessageQueueTaskStatus.RUNNING

            try:
                # cancel display progress task
                if task.display_progress_asyncio_task is not None:
                    await cancel_asyncio_task_and_wait(task.display_progress_asyncio_task)

                n_used_tokens = await asyncio.wait_for(task.asyncio_task, timeout=config.message_queue_task_timeout)
                self._token_budget -= n_used_tokens

                self._log_finished_task(task)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                await send_message_about_error_to_user_and_admin(
                    error=e,
                    bot=task.bot,
                    chat_id=task.chat_id,
                    user_id=task.user_id
                )
            finally:
                await self._finalize_and_remove_task(task.id)
        else:
            n_seconds_to_sleep_until_budget_is_non_negative = -token_budget / self.token_budget_per_second
            _sleep = n_seconds_to_sleep_until_budget_is_non_negative
            _sleep = max(0, _sleep)
            _sleep = min(10, _sleep)
            await asyncio.sleep(_sleep)

    def _log_finished_task(self, task: MessageQueueTask) -> None:
        # analytics
        processing_time = datetime.utcnow() - task.created_at

        # statistics
        self.statistics["finished_tasks"].append(
            {
                "id": task.id,
                "processing_time": processing_time.total_seconds(),
                "finished_at": datetime.utcnow()
            }
        )

        # mxp
        distinct_id, event_name, properties = (
            task.user_id,
            "finished_message_queue_task",
            {
                "id": task.id,
                "user_id": task.user_id,
                "chat_id": task.chat_id,
                "created_at": task.created_at.timestamp(),
                "priority": task.priority.name,
                "processing_time": processing_time.total_seconds(),
            }
        )

        mxp.track(distinct_id, event_name, properties)


class MessageQueueWatchdog:
    def __init__(
        self,
        message_queue: MessageQueueWithTokenBudget,
        frequency: float,  # in seconds
        bot: Bot,
        chat_id: ChatId,
        admin_usernames: List[str] = []
    ):
        self.message_queue = message_queue
        self.frequency = frequency
        self.bot = bot
        self.chat_id = chat_id
        self.admin_usernames = admin_usernames
        self.is_active = True

    def _calculate_statistics(self):
        statistics = dict()

        finished_tasks = self.message_queue.statistics["finished_tasks"]
        put_tasks = self.message_queue.statistics["put_tasks"]

        # token_budget
        token_budget = self.message_queue.get_token_budget()
        statistics["token_budget"] = token_budget

        # n tasks running/pending
        queue = self.message_queue.queue

        n_tasks_running = len([1 for task in queue if task.status == MessageQueueTaskStatus.RUNNING])
        statistics["n_tasks_running"] = n_tasks_running

        n_tasks_pending = len([1 for task in queue if task.status == MessageQueueTaskStatus.PENDING])
        statistics["n_tasks_pending"] = n_tasks_pending

        # processing time statistics
        processing_times = [x["processing_time"] for x in finished_tasks]
        if len(processing_times) == 0:
            processing_times = [-1.0]

        statistics["processing_time_min"] = np.min(processing_times)
        statistics["processing_time_median"] = np.mean(processing_times)
        statistics["processing_time_max"] = np.max(processing_times)

        # n tasks finished/put last n minutes
        def get_n_tasks_finished_last_n_minutes(n_minutes: float):
            return len([1 for task in finished_tasks if task["finished_at"] > datetime.utcnow() - timedelta(minutes=n_minutes)])

        def get_n_tasks_put_last_n_minutes(n_minutes: float):
            return len([1 for task in put_tasks if task["created_at"] > datetime.utcnow() - timedelta(minutes=n_minutes)])

        ## last 60 minutes
        statistics["n_finished_tasks_last_60_minutes"] = get_n_tasks_finished_last_n_minutes(60)
        statistics["n_put_tasks_last_60_minutes"] = get_n_tasks_put_last_n_minutes(60)

        ## last 15 minutes
        statistics["n_finished_tasks_last_15_minutes"] = get_n_tasks_finished_last_n_minutes(15)
        statistics["n_put_tasks_last_15_minutes"] = get_n_tasks_put_last_n_minutes(15)

        ## last 5 minutes
        statistics["n_finished_tasks_last_5_minutes"] = get_n_tasks_finished_last_n_minutes(5)
        statistics["n_put_tasks_last_5_minutes"] = get_n_tasks_put_last_n_minutes(5)

        # triggers
        statistics["triggers"] = list()

        ## last 5 minutes no finished tasks
        if (
            statistics["n_finished_tasks_last_5_minutes"] == 0
            and n_tasks_running == 0
            and statistics["n_put_tasks_last_5_minutes"] > 0
            and token_budget > 0
        ):
            statistics["triggers"].append("Last 5 minutes no finished tasks")

        return statistics

    def _get_message_text(self, statistics: dict):
        text = (

        )

        text = (
            f"🚥 <b>Queue Status</b> 🚥\n"
            f"\n"
            f"→ 📟 <b>State:</b>\n"
            f"  ⤷ Token budget: <b>{statistics['token_budget']:.1f}</b>\n"
            f"  ⤷ Running/Pending tasks: <b>{statistics['n_tasks_running']}/{statistics['n_tasks_pending']}</b>\n"
            f"  ⤷ Min/Median/Max processing time: <b>{statistics['processing_time_min']:.1f}s/{statistics['processing_time_median']:.1f}s/{statistics['processing_time_max']:.1f}s</b>\n"
            f"\n"
            f"→ 🏁 <b>Finished/Put tasks:</b>\n"
            f"  ⤷ Last 5 minutes: <b>{statistics['n_finished_tasks_last_5_minutes']}/{statistics['n_put_tasks_last_5_minutes']}</b>\n"
            f"  ⤷ Last 15 minutes: <b>{statistics['n_finished_tasks_last_15_minutes']}/{statistics['n_put_tasks_last_15_minutes']}</b>\n"
            f"  ⤷ Last 60 minutes: <b>{statistics['n_finished_tasks_last_60_minutes']}/{statistics['n_put_tasks_last_60_minutes']}</b>\n"
        )

        if len(statistics["triggers"]) > 0:
            admin_usernames_str = ", ".join(self.admin_usernames)

            text += (
                f"\n"
                f"→ 🚨 <b>Triggers ({admin_usernames_str}):</b>\n"
            )

            for trigger in statistics["triggers"]:
                text += f"  ⤷ {trigger} \n"

        return text

    def run(self) -> None:
        self._worker_job_task = asyncio.create_task(self._worker_job())

    async def shutdown(self) -> None:
        logger.info("Shutting down queue watchdog")
        self.disable()

        try:
            self._worker_job_task.cancel()
            await self._worker_job_task
        except:
            pass
        logger.info("Queue watchdog shut down")

    def disable(self) -> None:
        self.is_active = False
        logger.info("Queue watchdog is disabled")

    async def _worker_job(self):
        while self.is_active:
            try:
                statistics = self._calculate_statistics()
                text = self._get_message_text(statistics)

                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=text,
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.error(f"Error in message queue watchdog: {e}")
            finally:
                await asyncio.sleep(self.frequency)