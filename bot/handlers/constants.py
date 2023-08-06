from typing import Optional, Tuple, Any

from enum import Enum
from dataclasses import dataclass, asdict, fields
from bson.objectid import ObjectId


@dataclass
class CallbackData:
    def dump(self):
        data = asdict(self)
        data.pop("prefix")
        parts = [self.prefix, *data.values()]
        return "|".join(str(x) for x in parts)

    @classmethod
    def load(cls, data):
        parts = data.split("|")
        prefix = parts[0]
        if prefix != cls.prefix:
            raise ValueError(f"Invalid prefix: {prefix}")
        _fields = fields(cls)
        if len(parts) != len(_fields):
            raise ValueError(f"Invalid number of parts {len(parts)} in data {data} for {_fields}")
        _fields = [x for x in _fields if x.name != "prefix"]
        kwargs = {f.name: cls.string_to_field_value(p, f.type) for f, p in zip(_fields, parts[1:])}
        return cls(**kwargs)

    @classmethod
    def pattern(cls):
        return f"^{cls.prefix}"

    @staticmethod
    def string_to_field_value(line: str, field_type: type) -> Any:
        if field_type is bool:
            return line == "True"
        return field_type(line)


@dataclass
class SetChatModeData(CallbackData):
    chat_mode_key: str
    prefix: str = "set_chat_mode"


@dataclass
class ChoosePageChatModesData(CallbackData):
    page: int
    prefix: str = "show_chat_modes"


@dataclass
class ShowProductsData(CallbackData):
    payment_method_key: str
    prefix: str = "show_products"


@dataclass
class InvoiceData(CallbackData):
    payment_method_key: str
    product_key: str
    prefix: str = "send_invoice"


@dataclass
class SettingsData(CallbackData):
    model_key: str
    prefix: str = "set_settings"


@dataclass
class ShowPaymentMethodsData(CallbackData):
    prefix: str = "show_payment_methods"


@dataclass
class InviteFriendData(CallbackData):
    prefix: str = "invite_friend"


@dataclass
class NewDialogButtonData(CallbackData):
    use_new_dialog: bool
    prefix: str = "new_dialog_button"

@dataclass
class SpeedupMessageQueueButtonData(CallbackData):
    prefix: str = "speedup_message_queue"
