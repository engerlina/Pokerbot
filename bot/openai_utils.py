import logging
from pydoc import locate

import openai

from llm_tools.llm_streaming import StreamingOpenAIChatModel
from llm_tools.llm_fallback import StreamingModelWithFallback
from llm_tools.tokens import TokenExpenses
from llm_tools.errors import ModelContextSizeExceededError

from bot import config


logger = logging.getLogger(__name__)


def get_total_token_expenses(token_expenses: TokenExpenses):
    n_input_tokens, n_output_tokens = 0, 0
    for _, expense in token_expenses.expenses.items():
        n_input_tokens += expense.n_input_tokens
        n_output_tokens += expense.n_output_tokens

    return n_input_tokens, n_output_tokens


class ChatGPT:
    def __init__(self, model="gpt-3.5-turbo"):
        assert model in {"gpt-3.5-turbo", "gpt-4"}, f"Unknown model: {model}"
        self.model = model

    async def send_message_stream(
        self,
        message,
        token_expenses: TokenExpenses,
        dialog_messages=[],
        chat_mode: str = "assistant",
    ):
        if chat_mode not in config.chat_modes.keys():
            raise ValueError(f"Chat mode {chat_mode} is not supported")

        answer = ""
        n_dialog_messages_before = len(dialog_messages)
        n_first_dialog_messages_removed = 0

        is_finished = False
        while not is_finished:  # iterating to reduce context size if needed
            messages = self._generate_prompt_messages(message, dialog_messages, chat_mode)
            streaming_model = self._get_streaming_model()

            try:
                gen = streaming_model.stream_llm_reply(messages=messages)
                async for answer, _ in gen:
                    n_first_dialog_messages_removed = n_dialog_messages_before - len(dialog_messages)
                    yield answer, n_first_dialog_messages_removed

                answer = self._postprocess_answer(answer)
                is_finished = True
            except ModelContextSizeExceededError as e:
                if e.during_streaming:  # TODO: catch separate error when n_output_tokens >= max_tokens
                    is_finished = True
                else:
                    logger.info("Context length exceeded. Removing first message in dialog_messages")
                    if len(dialog_messages) == 0:
                        raise ValueError("Context length exceeded and dialog messages is empty")

                    # forget first message in dialog_messages
                    dialog_messages = dialog_messages[1:]
                continue
            finally:
                new_token_expenses = streaming_model.get_tokens_spent()
                for expense in new_token_expenses.expenses.values():
                    token_expenses.add_expense(expense)

    def _get_streaming_model(self):
        model_api = config.model_apis[self.model]

        def _instantinate_streaming_model(type: str):
            assert type in {"default", "fallback"}
            model_cls = locate(model_api[type]["class"])
            model_kwargs = dict(model_api[type]["kwargs"])
            model = model_cls(**model_kwargs)
            streaming_model = StreamingOpenAIChatModel(
                model,
                **model_api[type].get("streaming_kwargs", {})
            )
            return streaming_model

        streaming_model = _instantinate_streaming_model("default")
        if "fallback" in model_api:
            fallback_streaming_model = _instantinate_streaming_model("fallback")
            streaming_model = StreamingModelWithFallback(
                [streaming_model, fallback_streaming_model]
            )

        return streaming_model

    def _generate_prompt_messages(self, message, dialog_messages, chat_mode):
        prompt = config.chat_modes[chat_mode]["prompt_start"]

        messages = [{"role": "system", "content": prompt}]
        for dialog_message in dialog_messages:
            messages.append({"role": "user", "content": dialog_message["user"]})
            messages.append({"role": "assistant", "content": dialog_message["bot"]})
        messages.append({"role": "user", "content": message})

        return messages

    def _postprocess_answer(self, answer):
        answer = answer.strip()
        return answer


async def transcribe_audio(audio_file):
    model = "whisper-1"
    model_api = config.model_apis[model]

    r = await openai.Audio.atranscribe(
        file=audio_file,
        model=model,
        api_key=model_api["openai_api_key"],
    )
    return r["text"]


async def generate_images(prompt, n_images=4):
    model = "dalle-2"
    model_api = config.model_apis[model]

    r = await openai.Image.acreate(
        prompt=prompt,
        n=n_images,
        size="512x512",
        api_key=model_api["openai_api_key"],
    )
    image_urls = [item.url for item in r.data]
    return image_urls


async def is_content_acceptable(prompt):
    model = "moderation"
    model_api = config.model_apis[model]

    r = await openai.Moderation.acreate(
        input=prompt,
        api_key=model_api["openai_api_key"],
    )

    return not all(r.results[0].categories.values())
