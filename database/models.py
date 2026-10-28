import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class BusinessConnectionModel:
    connection_id: str
    user_id: int
    user_chat_id: int
    can_reply: bool
    is_enabled: bool
    connected_at: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BusinessConnectionModel":
        return cls(
            connection_id=str(data["connection_id"]),
            user_id=int(data["user_id"]),
            user_chat_id=int(data["user_chat_id"]),
            can_reply=bool(data["can_reply"]),
            is_enabled=bool(data["is_enabled"]),
            connected_at=int(data["connected_at"]),
        )


@dataclass
class BusinessMessageModel:
    connection_id: str
    chat_id: int
    message_id: int
    sender_id: int
    sender_name: str
    date: int
    id: int | None = None
    chat_title: str | None = None
    chat_username: str | None = None
    sender_username: str | None = None
    text: str | None = None
    media_type: str | None = None
    media_file_id: str | None = None
    media_file_unique_id: str | None = None
    is_deleted: bool = False
    deleted_at: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BusinessMessageModel":
        return cls(
            id=data.get("id"),
            connection_id=str(data["connection_id"]),
            chat_id=int(data["chat_id"]),
            chat_title=data.get("chat_title"),
            chat_username=data.get("chat_username"),
            message_id=int(data["message_id"]),
            sender_id=int(data["sender_id"]),
            sender_name=str(data["sender_name"]),
            sender_username=data.get("sender_username"),
            date=int(data["date"]),
            text=data.get("text"),
            media_type=data.get("media_type"),
            media_file_id=data.get("media_file_id"),
            media_file_unique_id=data.get("media_file_unique_id"),
            is_deleted=bool(data.get("is_deleted", False)),
            deleted_at=data.get("deleted_at"),
        )


@dataclass
class BusinessEditModel:
    connection_id: str
    chat_id: int
    message_id: int
    old_text: str
    new_text: str
    edited_at: int
    id: int | None = None


@dataclass
class ReactorConfig:
    owner_id: int
    enabled: bool = False
    emoji: str = "❤️\u200d🔥"
    is_big: bool = True
    only_incoming: bool = True
    target_chat_ids: list[int] = field(default_factory=list)
    updated_at: int = field(default_factory=lambda: int(time.time()))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReactorConfig":
        raw_targets = data.get("target_chat_ids", [])
        if isinstance(raw_targets, str):
            try:
                parsed = json.loads(raw_targets)
                targets = [int(x) for x in parsed] if isinstance(parsed, list) else []
            except (json.JSONDecodeError, ValueError, TypeError):
                targets = []
        elif isinstance(raw_targets, list):
            targets = [int(x) for x in raw_targets]
        else:
            targets = []

        raw_emoji = str(data.get("emoji", "❤️\u200d🔥"))
        if raw_emoji == "❤️🔥":
            raw_emoji = "❤️\u200d🔥"

        return cls(
            owner_id=int(data["owner_id"]),
            enabled=bool(data.get("enabled", False)),
            emoji=raw_emoji,
            is_big=bool(data.get("is_big", True)),
            only_incoming=bool(data.get("only_incoming", True)),
            target_chat_ids=targets,
            updated_at=int(data.get("updated_at") or int(time.time())),
        )
