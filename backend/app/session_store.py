"""会话存储抽象及内存、Redis 实现。"""

import json
import os
import time
from collections.abc import Callable
from threading import RLock
from typing import Literal, Protocol, TypedDict, cast

from redis import Redis
from redis.exceptions import WatchError

DEFAULT_SESSION_TTL_SECONDS = 24 * 60 * 60
IN_MEMORY_CLEANUP_INTERVAL_SECONDS = 60
MAX_TRANSACTION_RETRIES = 5


class Message(TypedDict):
    """会话中保存的一条用户或助手消息。"""

    role: Literal["user", "assistant"]
    content: str


class SessionState(TypedDict):
    """一个会话的滚动摘要与未压缩原文。"""

    summary: str
    history: list[Message]


class SessionStore(Protocol):
    """所有会话存储后端共同提供的接口。"""

    def get(self, session_id: str) -> list[Message]: ...

    def get_state(self, session_id: str) -> SessionState: ...

    def set_state(self, session_id: str, state: SessionState) -> None: ...

    def append(
        self, session_id: str, user_msg: Message, assistant_msg: Message
    ) -> None: ...

    def commit_summary(
        self,
        session_id: str,
        *,
        expected_summary: str,
        moved_messages: list[Message],
        new_summary: str,
    ) -> bool: ...


class RedisPipeline(Protocol):
    """Transactional Redis command subset used for session updates."""

    def watch(self, *names: str) -> object: ...

    def get(self, name: str) -> str | None: ...

    def multi(self) -> object: ...

    def setex(self, name: str, time: int, value: str) -> object: ...

    def execute(self) -> list[object]: ...

    def reset(self) -> object: ...


class RedisClient(Protocol):
    """Redis 命令子集，允许测试注入替身客户端。"""

    def get(self, name: str) -> str | None: ...

    def setex(self, name: str, time: int, value: str) -> object: ...

    def expire(self, name: str, time: int) -> object: ...

    def pipeline(self) -> RedisPipeline: ...


def _copy_state(state: SessionState) -> SessionState:
    return {
        "summary": state["summary"],
        "history": [message.copy() for message in state["history"]],
    }


def _configured_ttl(ttl_seconds: int | None) -> int:
    configured_ttl = (
        ttl_seconds
        if ttl_seconds is not None
        else int(os.getenv("SESSION_TTL_SECONDS", str(DEFAULT_SESSION_TTL_SECONDS)))
    )
    if configured_ttl < 1:
        raise ValueError("SESSION_TTL_SECONDS must be a positive integer")
    return configured_ttl


class InMemorySessionStore:
    """Thread-safe in-memory store with the same sliding TTL semantics as Redis."""

    def __init__(
        self,
        *,
        ttl_seconds: int | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._ttl_seconds = _configured_ttl(ttl_seconds)
        self._clock = clock or time.monotonic
        self._lock = RLock()
        self._sessions: dict[str, tuple[SessionState, float]] = {}
        self._last_cleanup_at = self._clock()

    def _purge_expired_unlocked(self, now: float) -> None:
        """Periodically remove expired sessions that are never accessed again."""
        if now - self._last_cleanup_at < IN_MEMORY_CLEANUP_INTERVAL_SECONDS:
            return
        self._sessions = {
            session_id: record
            for session_id, record in self._sessions.items()
            if record[1] > now
        }
        self._last_cleanup_at = now

    def _load_unlocked(self, session_id: str) -> SessionState:
        now = self._clock()
        self._purge_expired_unlocked(now)
        record = self._sessions.get(session_id)
        if record is None:
            return {"summary": "", "history": []}
        state, expires_at = record
        if expires_at <= now:
            del self._sessions[session_id]
            return {"summary": "", "history": []}
        self._sessions[session_id] = (state, now + self._ttl_seconds)
        return _copy_state(state)

    def _save_unlocked(self, session_id: str, state: SessionState) -> None:
        now = self._clock()
        self._purge_expired_unlocked(now)
        self._sessions[session_id] = (
            _copy_state(state),
            now + self._ttl_seconds,
        )

    def get(self, session_id: str) -> list[Message]:
        """Return a copy of the uncompressed original messages."""
        return self.get_state(session_id)["history"]

    def get_state(self, session_id: str) -> SessionState:
        """Load state and refresh the sliding expiration when it exists."""
        with self._lock:
            return self._load_unlocked(session_id)

    def set_state(self, session_id: str, state: SessionState) -> None:
        """Store summary and history together, refreshing the session TTL."""
        with self._lock:
            self._save_unlocked(session_id, state)

    def append(
        self, session_id: str, user_msg: Message, assistant_msg: Message
    ) -> None:
        """Append a completed turn and renew its expiration."""
        with self._lock:
            state = self._load_unlocked(session_id)
            state["history"].extend((user_msg.copy(), assistant_msg.copy()))
            self._save_unlocked(session_id, state)

    def commit_summary(
        self,
        session_id: str,
        *,
        expected_summary: str,
        moved_messages: list[Message],
        new_summary: str,
    ) -> bool:
        """Commit a summary only if its source prefix is still current."""
        with self._lock:
            state = self._load_unlocked(session_id)
            prefix_length = len(moved_messages)
            if (
                state["summary"] != expected_summary
                or state["history"][:prefix_length] != moved_messages
            ):
                return False
            state["summary"] = new_summary
            state["history"] = state["history"][prefix_length:]
            self._save_unlocked(session_id, state)
            return True


class RedisSessionStore:
    """Persist each session as one Redis JSON value with a sliding TTL."""

    def __init__(
        self,
        client: RedisClient | None = None,
        *,
        ttl_seconds: int | None = None,
    ) -> None:
        self._ttl_seconds = _configured_ttl(ttl_seconds)
        if client is None:
            self._client = cast(
                RedisClient,
                Redis.from_url(
                    os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
                    decode_responses=True,
                ),
            )
        else:
            self._client = client

    @staticmethod
    def _key(session_id: str) -> str:
        return f"session:{session_id}"

    def get_state(self, session_id: str) -> SessionState:
        """Load session state and refresh its sliding expiration."""
        key = self._key(session_id)
        serialized = self._client.get(key)
        if serialized is None:
            return {"summary": "", "history": []}
        self._client.expire(key, self._ttl_seconds)
        return self._decode_state(serialized)

    @staticmethod
    def _decode_state(serialized: str | None) -> SessionState:
        if serialized is None:
            return {"summary": "", "history": []}
        value = json.loads(serialized)
        if not isinstance(value, dict):
            raise ValueError("Invalid session data in Redis")
        summary = value.get("summary", "")
        history = value.get("history", [])
        if not isinstance(summary, str) or not isinstance(history, list):
            raise ValueError("Invalid session data in Redis")
        return cast(SessionState, {"summary": summary, "history": history})

    def set_state(self, session_id: str, state: SessionState) -> None:
        """Persist summary and history together with a refreshed TTL."""
        serialized = json.dumps(state, ensure_ascii=False)
        self._client.setex(self._key(session_id), self._ttl_seconds, serialized)

    def get(self, session_id: str) -> list[Message]:
        """Return a copy of the uncompressed original messages."""
        return [message.copy() for message in self.get_state(session_id)["history"]]

    def append(
        self, session_id: str, user_msg: Message, assistant_msg: Message
    ) -> None:
        """Atomically append a completed turn without overwriting concurrent writes."""
        key = self._key(session_id)
        for _ in range(MAX_TRANSACTION_RETRIES):
            pipeline = self._client.pipeline()
            try:
                pipeline.watch(key)
                state = self._decode_state(pipeline.get(key))
                state["history"].extend((user_msg.copy(), assistant_msg.copy()))
                pipeline.multi()
                pipeline.setex(
                    key,
                    self._ttl_seconds,
                    json.dumps(state, ensure_ascii=False),
                )
                pipeline.execute()
                return
            except WatchError:
                continue
            finally:
                pipeline.reset()
        raise WatchError("Session changed during every append retry")

    def commit_summary(
        self,
        session_id: str,
        *,
        expected_summary: str,
        moved_messages: list[Message],
        new_summary: str,
    ) -> bool:
        """Remove a summarized prefix while preserving concurrent appended turns."""
        key = self._key(session_id)
        for _ in range(MAX_TRANSACTION_RETRIES):
            pipeline = self._client.pipeline()
            try:
                pipeline.watch(key)
                state = self._decode_state(pipeline.get(key))
                prefix_length = len(moved_messages)
                if (
                    state["summary"] != expected_summary
                    or state["history"][:prefix_length] != moved_messages
                ):
                    return False

                state["summary"] = new_summary
                state["history"] = state["history"][prefix_length:]
                pipeline.multi()
                pipeline.setex(
                    key,
                    self._ttl_seconds,
                    json.dumps(state, ensure_ascii=False),
                )
                pipeline.execute()
                return True
            except WatchError:
                continue
            finally:
                pipeline.reset()
        raise WatchError("Session changed during every summary retry")


def create_session_store() -> SessionStore:
    """Create the configured store; memory is the safe local/test default."""
    backend = os.getenv("SESSION_BACKEND", "memory").strip().lower()
    if backend == "memory":
        return InMemorySessionStore()
    if backend == "redis":
        return RedisSessionStore()
    raise ValueError("SESSION_BACKEND must be 'memory' or 'redis'")
