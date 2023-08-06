from bot import config
from bot.queue.message_queue import MessageQueueWithTokenBudget, MessageQueueWatchdog

# message queue
message_queue = MessageQueueWithTokenBudget(
    token_budget_per_day=config.message_queue_token_budget_per_day,
    initial_token_budget=config.message_queue_initial_token_budget,
) if config.enable_message_queue else None

# user tasks (used for task cancelling)
user_tasks = dict()
