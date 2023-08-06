# run: docker compose --env-file config/config.env run chatgpt_telegram_bot_pro python /code/scripts/newsletter_60_new_chat_modes.py

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

import asyncio
import time
import pymongo
from datetime import datetime
import pytz
import random

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder
from telegram.constants import ParseMode

from bot import config
from bot import database
from bot.app import show_balance_handle

newsletter_id = "60_new_chat_modes"

async def main():
    # setup
    application = (
        ApplicationBuilder()
        .token(config.telegram_token)
        .build()
    )

    db = database.Database()

    # db collection
    db.create_newsletter(newsletter_id)
    already_sent_to_user_ids = set(db.get_newsletter_attribute(newsletter_id, "already_sent_to_user_ids"))
    print(f"Already sent to {len(already_sent_to_user_ids)} users")

    # choose users to send
    # user_dicts = list(db.user_collection.find({"username": "karfly"}))
    user_dicts = list(db.user_collection.find({}))
    print(f"Found {len(user_dicts)} users")

    # send newsletter
    text = """🔥🎭 New hot <b>65+ chat modes</b> added to the bot!

<b>Try now</b>: /mode!

Some highlights:
👩‍🎨 <b>Artist</b> - generate images with text description
🧠 <b>Psychologist</b> - feel stressed, it'll help
🇬🇧 <b>English Tutor</b> - your personal teacher
💡 <b>Startup Idea Generator</b> - brainstorm business ideas
✉️ <b>Email Writer</b> - helps with you email routine
📈 <b>Excel Assistant</b> - professinally writes Excel functions for you
💋 <b>Eva Elfie (18+)</b> - well... just try it!

... and 60+ more chat modes for <b>many use cases</b>"""

    random.shuffle(user_dicts)
    for user_dict in user_dicts:
        if user_dict["_id"] in already_sent_to_user_ids:
            print(f"Skipping {user_dict['_id']}. Already sent before")
            continue

        try:
            await application.bot.send_message(
                user_dict['chat_id'],
                text,
                disable_web_page_preview=True,
                parse_mode=ParseMode.HTML
            )
            print(f"Successfully sent to {user_dict['_id']}")

            db.add_user_to_newsletter(newsletter_id, user_dict["_id"])

            time.sleep(1.0)
        except Exception as e:
            print(e)
            if "httpx.LocalProtocolError" in str(e):
                print(e)
                print("You need to start again")
                exit()

            print(f"Failed to send message to {user_dict['_id']}. Reason: {e}")


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
    loop.close()