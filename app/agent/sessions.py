"""In-memory chat sessions. Each keeps the exact, append-only Claude message history (thinking and
tool blocks included) plus its evidence ledger, so follow-up questions can cite earlier evidence."""

from __future__ import annotations

import asyncio
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from ..evidence import EvidenceLedger
from ..schemas import FarmProfile
from ..tools.base import ToolContext
from ..tools.registry import ToolExecutor


@dataclass
class ChatSession:
    id: str
    farm: FarmProfile
    ledger: EvidenceLedger
    executor: ToolExecutor
    messages: list[dict[str, Any]] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    touched: float = field(default_factory=time.time)

    @property
    def started(self) -> bool:
        return bool(self.messages)


class SessionStore:
    def __init__(self, ctx: ToolContext, max_sessions: int = 300, ttl_s: float = 6 * 3600):
        self._ctx = ctx
        self._max = max_sessions
        self._ttl = ttl_s
        self._items: OrderedDict[str, ChatSession] = OrderedDict()

    def get_or_create(self, session_id: str | None, farm: FarmProfile) -> ChatSession:
        self._evict()
        sess = self._items.get(session_id or "")
        if sess is not None and sess.farm.key() == farm.key():
            sess.touched = time.time()
            self._items.move_to_end(sess.id)
            return sess
        ledger = EvidenceLedger()
        sess = ChatSession(id=secrets.token_urlsafe(12), farm=farm, ledger=ledger,
                           executor=ToolExecutor(self._ctx, ledger, farm=farm))
        self._items[sess.id] = sess
        return sess

    def _evict(self) -> None:
        cutoff = time.time() - self._ttl
        for sid in [sid for sid, s in self._items.items() if s.touched < cutoff]:
            self._items.pop(sid, None)
        while len(self._items) >= self._max:
            self._items.popitem(last=False)
