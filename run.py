import logging
from bot.app import run_bot

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)-12s :%(name)-15s: %(levelname)-8s %(message)s'
    )
    logging.getLogger("openai").setLevel(logging.ERROR)
    run_bot()
