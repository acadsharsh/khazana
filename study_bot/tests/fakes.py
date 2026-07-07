"""Lightweight Telegram fakes for handler tests (no network)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


class FakeBot:
    def __init__(self) -> None:
        self.sent: List[Dict[str, Any]] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})
        return {"message_id": 1}


class FakeMessage:
    def __init__(self) -> None:
        self.replies: List[Dict[str, Any]] = []
        self.documents: List[Dict[str, Any]] = []
        self.text: str = ""

    async def reply_text(self, text, **kwargs):
        self.replies.append({"text": text, **kwargs})
        return self

    async def reply_document(self, document=None, **kwargs):
        self.documents.append({"document": document, **kwargs})
        return self


@dataclass
class FakeChat:
    id: int = 1


@dataclass
class FakeUser:
    id: int = 1
    username: Optional[str] = "alice"
    full_name: Optional[str] = "Alice"


class FakeContext:
    def __init__(self, args: Optional[List[str]] = None) -> None:
        self.args = args or []
        self.bot_data: Dict[str, Any] = {}
        self.bot = FakeBot()


class FakeUpdate:
    def __init__(
        self,
        user: Optional[FakeUser] = None,
        args: Optional[List[str]] = None,
        chat_id: int = 1,
        text: str = "",
    ) -> None:
        self.effective_user = user or FakeUser()
        self.effective_chat = FakeChat(id=chat_id)
        self.effective_message = FakeMessage()
        self.effective_message.text = text
        self.message = self.effective_message
        self.context = FakeContext(args=args)
