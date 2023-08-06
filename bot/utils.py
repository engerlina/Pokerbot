import asyncio


def split_text_into_chunks(text, chunk_size):
    for i in range(0, len(text), chunk_size):
        yield text[i:i + chunk_size]


async def cancel_asyncio_task_and_wait(task: asyncio.Task):
    task.cancel()
    # ensure that task is cancelled
    try:
        await task
    except asyncio.CancelledError:
        pass
