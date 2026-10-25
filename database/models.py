from dataclasses import dataclass


@dataclass
class BusinessConnectionModel:
    connection_id: str
    user_id: int
    user_chat_id: int
    can_reply: bool
    is_enabled: bool
    connected_at: int


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
    is_deleted: bool = False
    deleted_at: int | None = None


@dataclass
class BusinessEditModel:
    connection_id: str
    chat_id: int
    message_id: int
    old_text: str
    new_text: str
    edited_at: int
    id: int | None = None
