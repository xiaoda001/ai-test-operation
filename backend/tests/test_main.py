"""Tests for the FastAPI application."""

import json
import sqlite3
from collections.abc import Callable, Iterable, Iterator
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openai.types.chat import ChatCompletion, ChatCompletionChunk
from redis.exceptions import WatchError

from app import main, observability
from app.session_store import (
    InMemorySessionStore,
    Message,
    RedisSessionStore,
    SessionState,
    create_session_store,
)

client = TestClient(main.app)


class FakeRedis:
    """Small in-memory Redis double for SessionStore tests."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.versions: dict[str, int] = {}
        self.after_pipeline_read: Callable[[], None] | None = None

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def setex(self, name: str, time: int, value: str) -> bool:
        self.values[name] = value
        self.ttls[name] = time
        self.versions[name] = self.versions.get(name, 0) + 1
        return True

    def expire(self, name: str, time: int) -> bool:
        if name not in self.values:
            return False
        self.ttls[name] = time
        self.versions[name] = self.versions.get(name, 0) + 1
        return True

    def pipeline(self) -> "FakeRedisPipeline":
        return FakeRedisPipeline(self)


class FakeRedisPipeline:
    """Optimistic transaction double that detects watched-key changes."""

    def __init__(self, redis: FakeRedis) -> None:
        self.redis = redis
        self.watched: dict[str, int] = {}
        self.pending: list[tuple[str, int, str]] = []
        self.in_multi = False

    def watch(self, *names: str) -> None:
        for name in names:
            self.watched[name] = self.redis.versions.get(name, 0)

    def get(self, name: str) -> str | None:
        value = self.redis.get(name)
        hook = self.redis.after_pipeline_read
        self.redis.after_pipeline_read = None
        if hook is not None:
            hook()
        return value

    def multi(self) -> None:
        self.in_multi = True

    def setex(self, name: str, time: int, value: str) -> None:
        if self.in_multi:
            self.pending.append((name, time, value))
        else:
            self.redis.setex(name, time, value)

    def execute(self) -> list[object]:
        if any(
            self.redis.versions.get(name, 0) != version
            for name, version in self.watched.items()
        ):
            raise WatchError("Watched session changed")
        return [self.redis.setex(name, ttl, value) for name, ttl, value in self.pending]

    def reset(self) -> None:
        self.watched.clear()
        self.pending.clear()
        self.in_multi = False


@pytest.fixture
def trace_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Use and remove a SQLite file inside the writable workspace."""
    path = Path(__file__).with_name(".test-traces.sqlite3")
    path.unlink(missing_ok=True)
    monkeypatch.setattr(observability, "DB_PATH", path)
    yield
    path.unlink(missing_ok=True)


def test_health_returns_ok() -> None:
    """The health endpoint returns a successful status and JSON body."""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_chat_allows_local_frontend_cors_preflight() -> None:
    """The local frontend can preflight a JSON POST to the chat endpoint."""
    response = client.options(
        "/chat",
        headers={
            "Origin": "http://localhost:10111",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:10111"
    assert "POST" in response.headers["access-control-allow-methods"]


def test_chat_streams_sse_tokens_and_done(
    monkeypatch: pytest.MonkeyPatch,
    trace_db: None,
) -> None:
    """The chat endpoint streams text chunks and terminates with the done frame."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("DEEPSEEK_MODEL", "test-model")
    store = RedisSessionStore(FakeRedis())
    store.append(
        "session-1",
        {"role": "user", "content": "我想做视频"},
        {"role": "assistant", "content": "之前的回答"},
    )
    monkeypatch.setattr(main, "session_store", store)

    class FakeCompletions:
        def create(
            self,
            *,
            model: str,
            messages: list[dict[str, str]],
            stream: bool,
            stream_options: dict[str, bool],
        ) -> Iterable[ChatCompletionChunk]:
            assert model == "test-model"
            assert [message["role"] for message in messages] == [
                "system",
                "user",
                "assistant",
                "user",
            ]
            assert messages[0]["content"].startswith("你是内容运营助手")
            assert "正常用自然语言回答用户问题" in messages[0]["content"]
            assert '"hook_type"' not in messages[0]["content"]
            assert messages[1:] == [
                {"role": "user", "content": "我想做视频"},
                {"role": "assistant", "content": "之前的回答"},
                {
                    "role": "user",
                    "content": (
                        "夏天 test-key sk-testcredential123456 "
                        "sk_live_abcdefghijklmnop "
                        "api_key=other-secret-value"
                    ),
                },
            ]
            assert stream is True
            assert stream_options == {"include_usage": True}
            return iter(
                [
                    ChatCompletionChunk.model_construct(
                        choices=[
                            {
                                "delta": {"content": "你好，我是内容运营助手，"},
                                "index": 0,
                                "finish_reason": None,
                            }
                        ]
                    ),
                    ChatCompletionChunk.model_construct(
                        choices=[
                            {
                                "delta": {"content": None},
                                "index": 0,
                                "finish_reason": None,
                            }
                        ]
                    ),
                    ChatCompletionChunk.model_construct(
                        choices=[
                            {
                                "delta": {"content": "可以帮你策划内容。"},
                                "index": 0,
                                "finish_reason": None,
                            }
                        ]
                    ),
                    ChatCompletionChunk.model_construct(
                        choices=[],
                        usage={"prompt_tokens": 37, "completion_tokens": 12},
                    ),
                ]
            )

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAI:
        def __init__(self, *, api_key: str, base_url: str) -> None:
            assert api_key == "test-key"
            assert base_url == "https://example.invalid/v1"
            self.chat = FakeChat()

    monkeypatch.setattr("app.main.OpenAI", FakeOpenAI)

    response = client.post(
        "/chat",
        json={
            "session_id": "session-1",
            "message": (
                "夏天 test-key sk-testcredential123456 "
                "sk_live_abcdefghijklmnop api_key=other-secret-value"
            ),
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = [frame for frame in response.text.split("\n\n") if frame]
    token_text = "".join(
        json.loads(frame.removeprefix("data: "))["token"]
        for frame in frames
        if frame != "data: [DONE]"
    )
    assert token_text == "你好，我是内容运营助手，可以帮你策划内容。"
    assert frames[-1] == "data: [DONE]"

    traces = client.get("/traces")
    assert traces.status_code == 200
    trace = traces.json()["items"][0]
    assert trace["model"] == "test-model"
    assert trace["call_type"] == "chat"
    assert trace["prompt_tokens"] == 37
    assert trace["completion_tokens"] == 12
    assert trace["status"] == "success"
    assert "messages" not in trace

    detail = client.get(f"/traces/{trace['trace_id']}")
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["call_type"] == "chat"
    assert detail_body["messages"][-1]["content"] == (
        "夏天 [REDACTED] [REDACTED] [REDACTED] api_key=[REDACTED]"
    )
    assert "test-key" not in str(detail_body["messages"])
    assert "sk-testcredential123456" not in str(detail_body["messages"])
    assert "sk_live_abcdefghijklmnop" not in str(detail_body["messages"])
    assert "other-secret-value" not in str(detail_body["messages"])
    assert store.get("session-1")[-2:] == [
        {
            "role": "user",
            "content": (
                "夏天 test-key sk-testcredential123456 "
                "sk_live_abcdefghijklmnop api_key=other-secret-value"
            ),
        },
        {"role": "assistant", "content": "你好，我是内容运营助手，可以帮你策划内容。"},
    ]


def test_chat_keeps_context_across_three_turns(
    monkeypatch: pytest.MonkeyPatch,
    trace_db: None,
) -> None:
    """同一会话的连续请求会把已完成的问答带入后续上下文。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("DEEPSEEK_MODEL", "test-model")
    store = RedisSessionStore(FakeRedis())
    monkeypatch.setattr(main, "session_store", store)
    captured_messages: list[list[dict[str, str]]] = []
    replies = ["记住了，你叫小明。", "你叫小明。", "你刚才说你叫小明。"]

    class FakeCompletions:
        def create(
            self,
            *,
            model: str,
            messages: list[dict[str, str]],
            stream: bool,
            stream_options: dict[str, bool],
        ) -> Iterable[ChatCompletionChunk]:
            assert model == "test-model"
            assert stream is True
            assert stream_options == {"include_usage": True}
            captured_messages.append(messages)
            reply = replies[len(captured_messages) - 1]
            return iter(
                [
                    ChatCompletionChunk.model_construct(
                        choices=[
                            {
                                "delta": {"content": reply},
                                "index": 0,
                                "finish_reason": None,
                            }
                        ]
                    )
                ]
            )

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAI:
        def __init__(self, *, api_key: str, base_url: str) -> None:
            assert api_key == "test-key"
            assert base_url == "https://example.invalid/v1"
            self.chat = FakeChat()

    monkeypatch.setattr("app.main.OpenAI", FakeOpenAI)

    for message, expected_reply in zip(
        ["我叫小明", "我叫什么", "我刚才说我叫什么"], replies, strict=True
    ):
        response = client.post(
            "/chat", json={"session_id": "session-1", "message": message}
        )
        assert response.status_code == 200
        frames = [frame for frame in response.text.split("\n\n") if frame]
        tokens = [
            json.loads(frame.removeprefix("data: "))["token"]
            for frame in frames
            if frame != "data: [DONE]"
        ]
        assert "".join(tokens) == expected_reply

    third_request = captured_messages[2]
    assert [message["role"] for message in third_request] == [
        "system",
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]
    assert third_request[-5:] == [
        {"role": "user", "content": "我叫小明"},
        {"role": "assistant", "content": "记住了，你叫小明。"},
        {"role": "user", "content": "我叫什么"},
        {"role": "assistant", "content": "你叫小明。"},
        {"role": "user", "content": "我刚才说我叫什么"},
    ]


def test_truncate_history_keeps_recent_complete_turns() -> None:
    """五轮窗口填满后，连续十轮请求的消息数保持不变。"""
    store = RedisSessionStore(FakeRedis())
    model_message_counts: list[int] = []
    for index in range(1, 11):
        user_message: Message = {"role": "user", "content": f"user {index}"}
        history = store.get("session-1")
        model_messages = main.truncate_history(
            history, user_message, recent_turns=4
        )
        # 再加上一条由后端置顶的 system 消息。
        model_message_counts.append(1 + len(model_messages))
        store.append(
            "session-1",
            user_message,
            {"role": "assistant", "content": f"assistant {index}"},
        )

    assert model_message_counts == [2, 4, 6, 8, 10, 10, 10, 10, 10, 10]
    assert main.truncate_history(
        store.get("session-1"), {"role": "user", "content": "current"}, 4
    ) == [
        {"role": "user", "content": "user 7"},
        {"role": "assistant", "content": "assistant 7"},
        {"role": "user", "content": "user 8"},
        {"role": "assistant", "content": "assistant 8"},
        {"role": "user", "content": "user 9"},
        {"role": "assistant", "content": "assistant 9"},
        {"role": "user", "content": "user 10"},
        {"role": "assistant", "content": "assistant 10"},
        {"role": "user", "content": "current"},
    ]


def test_session_store_keeps_histories_isolated() -> None:
    """向一个会话追加轮次不会影响其他会话。"""
    store = RedisSessionStore(FakeRedis())
    store.append(
        "session-a",
        {"role": "user", "content": "我叫小明"},
        {"role": "assistant", "content": "你好，小明"},
    )

    assert store.get("session-b") == []
    assert store.get("session-a") == [
        {"role": "user", "content": "我叫小明"},
        {"role": "assistant", "content": "你好，小明"},
    ]


def test_session_store_factory_defaults_to_memory_and_selects_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backend selection is explicit, and memory is the safe default."""
    monkeypatch.delenv("SESSION_BACKEND", raising=False)
    assert isinstance(create_session_store(), InMemorySessionStore)

    monkeypatch.setenv("SESSION_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    assert isinstance(create_session_store(), RedisSessionStore)

    monkeypatch.setenv("SESSION_BACKEND", "unsupported")
    with pytest.raises(ValueError, match="SESSION_BACKEND"):
        create_session_store()


def test_in_memory_store_preserves_summary_and_renews_ttl() -> None:
    """Memory backend mirrors the shared state interface and sliding TTL."""
    current_time = [100.0]
    store = InMemorySessionStore(
        ttl_seconds=10, clock=lambda: current_time[0]
    )
    store.append(
        "session-a",
        {"role": "user", "content": "我叫小明"},
        {"role": "assistant", "content": "你好，小明"},
    )
    store.set_state(
        "session-a",
        {
            "summary": "用户叫小明。",
            "history": store.get("session-a"),
        },
    )

    current_time[0] = 109.0
    assert store.get_state("session-a")["summary"] == "用户叫小明。"
    current_time[0] = 118.0
    store.append(
        "session-a",
        {"role": "user", "content": "我喜欢简洁回答"},
        {"role": "assistant", "content": "记住了"},
    )
    assert len(store.get("session-a")) == 4

    current_time[0] = 129.0
    expired_state = store.get_state("session-a")
    assert expired_state["summary"] == ""
    assert expired_state["history"] == []


def test_in_memory_store_commits_summary_without_losing_recent_turns() -> None:
    """The memory backend implements the same summary commit operation."""
    store = InMemorySessionStore()
    first_turn: list[Message] = [
        {"role": "user", "content": "我叫小明"},
        {"role": "assistant", "content": "你好，小明"},
    ]
    recent_turn: list[Message] = [
        {"role": "user", "content": "我喜欢简洁回答"},
        {"role": "assistant", "content": "记住了"},
    ]
    store.set_state(
        "session-a",
        {"summary": "", "history": [*first_turn, *recent_turn]},
    )

    committed = store.commit_summary(
        "session-a",
        expected_summary="",
        moved_messages=first_turn,
        new_summary="用户叫小明。",
    )

    assert committed is True
    assert store.get_state("session-a") == {
        "summary": "用户叫小明。",
        "history": recent_turn,
    }


def test_in_memory_store_sweeps_expired_sessions_not_revisited() -> None:
    """An ordinary later access reclaims all expired in-memory sessions."""
    current_time = [100.0]
    store = InMemorySessionStore(ttl_seconds=10, clock=lambda: current_time[0])
    for session_id in ("session-a", "session-b"):
        store.append(
            session_id,
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        )

    current_time[0] += 61.0
    store.get_state("never-created")

    assert store._sessions == {}


def test_redis_store_state_survives_store_recreation() -> None:
    """A new Redis store instance reads the same persisted summary and turns."""
    redis = FakeRedis()
    first_store = RedisSessionStore(redis, ttl_seconds=3600)
    first_store.append(
        "session-a",
        {"role": "user", "content": "我叫小明"},
        {"role": "assistant", "content": "你好，小明"},
    )
    first_store.set_state(
        "session-a",
        {
            "summary": "用户叫小明。",
            "history": first_store.get("session-a"),
        },
    )

    restarted_store = RedisSessionStore(redis, ttl_seconds=3600)

    assert restarted_store.get_state("session-a") == {
        "summary": "用户叫小明。",
        "history": [
            {"role": "user", "content": "我叫小明"},
            {"role": "assistant", "content": "你好，小明"},
        ],
    }
    assert redis.ttls["session:session-a"] == 3600


def test_session_store_persists_summary_history_and_refreshes_ttl() -> None:
    """Summary, raw turns, isolation, and sliding expiration persist together."""
    redis = FakeRedis()
    store = RedisSessionStore(redis, ttl_seconds=60)
    state: SessionState = {
        "summary": "用户叫小明。",
        "history": [{"role": "user", "content": "最近问题"}],
    }
    store.set_state("session-a", state)
    redis.ttls["session:session-a"] = 5

    assert store.get_state("session-a") == state
    assert redis.ttls["session:session-a"] == 60
    empty_state = store.get_state("session-b")
    assert empty_state["summary"] == ""
    assert empty_state["history"] == []


def test_concurrent_appends_retry_without_losing_either_turn() -> None:
    """A watched-key conflict retries against the latest stored state."""
    redis = FakeRedis()
    store = RedisSessionStore(redis)
    store.append(
        "session-a",
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "first reply"},
    )
    redis.after_pipeline_read = lambda: store.append(
        "session-a",
        {"role": "user", "content": "concurrent"},
        {"role": "assistant", "content": "concurrent reply"},
    )

    store.append(
        "session-a",
        {"role": "user", "content": "outer"},
        {"role": "assistant", "content": "outer reply"},
    )

    assert [
        message["content"] for message in store.get("session-a")
    ] == [
        "first",
        "first reply",
        "concurrent",
        "concurrent reply",
        "outer",
        "outer reply",
    ]


def test_compression_summarizes_only_a_bounded_oldest_batch(
    monkeypatch: pytest.MonkeyPatch,
    trace_db: None,
) -> None:
    """A backlog is compressed in oldest-first batches of at most four turns."""
    store = RedisSessionStore(FakeRedis())
    monkeypatch.setattr(main, "session_store", store)
    initial_history: list[Message] = []
    for index in range(1, 11):
        initial_history.extend(
            (
                {"role": "user", "content": f"user {index}"},
                {"role": "assistant", "content": f"assistant {index}"},
            )
        )
    store.set_state(
        "session-a", {"summary": "old summary", "history": initial_history}
    )
    captured: list[dict[str, object]] = []

    class FakeCompletions:
        def create(
            self,
            *,
            model: str,
            messages: list[dict[str, str]],
            stream: bool = False,
        ) -> ChatCompletion:
            captured.append(json.loads(messages[1]["content"]))
            return ChatCompletion.model_validate(
                {
                    "id": "summary-call",
                    "object": "chat.completion",
                    "created": 1,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "rolled"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 2,
                        "total_tokens": 12,
                    },
                }
            )

    class FakeClient:
        class Chat:
            completions = FakeCompletions()

        chat = Chat()

    main.compress_session(
        session_id="session-a",
        client=FakeClient(),  # type: ignore[arg-type]
        model="test-model",
        api_key="test-key",
        recent_turns=4,
    )

    moved = captured[0]["new_turns"]
    assert isinstance(moved, list)
    assert len(moved) == 8
    assert moved[0]["content"] == "user 1"
    assert moved[-1]["content"] == "assistant 4"
    state = store.get_state("session-a")
    assert state["summary"] == "rolled"
    assert len(state["history"]) == 12
    assert state["history"][0]["content"] == "user 5"


def test_summary_commit_preserves_turns_appended_during_llm_call(
    monkeypatch: pytest.MonkeyPatch,
    trace_db: None,
) -> None:
    """The summary transaction removes its prefix but keeps concurrent appends."""
    store = RedisSessionStore(FakeRedis())
    monkeypatch.setattr(main, "session_store", store)
    initial_history: list[Message] = []
    for index in range(1, 6):
        initial_history.extend(
            (
                {"role": "user", "content": f"user {index}"},
                {"role": "assistant", "content": f"assistant {index}"},
            )
        )
    store.set_state("session-a", {"summary": "", "history": initial_history})

    class FakeCompletions:
        def create(
            self,
            *,
            model: str,
            messages: list[dict[str, str]],
            stream: bool = False,
        ) -> ChatCompletion:
            store.append(
                "session-a",
                {"role": "user", "content": "concurrent"},
                {"role": "assistant", "content": "concurrent reply"},
            )
            return ChatCompletion.model_validate(
                {
                    "id": "summary-call",
                    "object": "chat.completion",
                    "created": 1,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "rolled"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 2,
                        "total_tokens": 12,
                    },
                }
            )

    class FakeClient:
        class Chat:
            completions = FakeCompletions()

        chat = Chat()

    main.compress_session(
        session_id="session-a",
        client=FakeClient(),  # type: ignore[arg-type]
        model="test-model",
        api_key="test-key",
        recent_turns=4,
    )

    state = store.get_state("session-a")
    assert state["summary"] == "rolled"
    assert state["history"][0]["content"] == "user 2"
    assert state["history"][-2:] == [
        {"role": "user", "content": "concurrent"},
        {"role": "assistant", "content": "concurrent reply"},
    ]


def test_chat_rolls_early_turns_into_summary_and_traces_usage(
    monkeypatch: pytest.MonkeyPatch,
    trace_db: None,
) -> None:
    """Older turns become a tracked summary while recent turns remain verbatim."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("DEEPSEEK_MODEL", "test-model")
    store = RedisSessionStore(FakeRedis())
    monkeypatch.setattr(main, "session_store", store)
    chat_requests: list[list[dict[str, str]]] = []
    summary_inputs: list[dict[str, object]] = []

    class FakeCompletions:
        def create(
            self,
            *,
            model: str,
            messages: list[dict[str, str]],
            stream: bool = False,
            stream_options: dict[str, bool] | None = None,
        ) -> object:
            assert model == "test-model"
            if stream:
                assert stream_options == {"include_usage": True}
                chat_requests.append(messages)
                reply = (
                    "你叫小明。"
                    if "我叫什么" in messages[-1]["content"]
                    else "我记住了。"
                )
                return iter(
                    [
                        ChatCompletionChunk.model_construct(
                            choices=[
                                {
                                    "delta": {"content": reply},
                                    "index": 0,
                                    "finish_reason": None,
                                }
                            ]
                        )
                    ]
                )
            summary_inputs.append(json.loads(messages[1]["content"]))
            return ChatCompletion.model_validate(
                {
                    "id": "summary-call",
                    "object": "chat.completion",
                    "created": 1,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "用户叫小明，偏好简洁回答。",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 28,
                        "completion_tokens": 9,
                        "total_tokens": 37,
                    },
                }
            )

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAI:
        def __init__(self, *, api_key: str, base_url: str) -> None:
            assert api_key == "test-key"
            assert base_url == "https://example.invalid/v1"
            self.chat = FakeChat()

    monkeypatch.setattr("app.main.OpenAI", FakeOpenAI)

    sixth_response = None
    for index in range(1, 7):
        if index == 1:
            user_message = "我叫小明"
        elif index == 6:
            user_message = "我叫什么？"
        else:
            user_message = f"第 {index} 轮问题"
        response = client.post(
            "/chat", json={"session_id": "session-1", "message": user_message}
        )
        assert response.status_code == 200
        assert response.text.endswith("data: [DONE]\n\n")
        if index == 6:
            sixth_response = response

    sixth_request = chat_requests[5]
    assert sixth_request[0]["role"] == "system"
    assert sixth_request[1] == {
        "role": "user",
        "content": "[历史摘要] 用户叫小明，偏好简洁回答。",
    }
    assert sixth_request[-1] == {"role": "user", "content": "我叫什么？"}
    assert [message["content"] for message in sixth_request[2:-1:2]] == [
        "第 2 轮问题",
        "第 3 轮问题",
        "第 4 轮问题",
        "第 5 轮问题",
    ]
    assert summary_inputs[0]["existing_summary"] == ""
    first_new_turns = summary_inputs[0]["new_turns"]
    assert isinstance(first_new_turns, list)
    assert isinstance(first_new_turns[0], dict)
    assert first_new_turns[0]["content"] == "我叫小明"
    assert summary_inputs[1]["existing_summary"] == "用户叫小明，偏好简洁回答。"
    second_new_turns = summary_inputs[1]["new_turns"]
    assert isinstance(second_new_turns, list)
    assert isinstance(second_new_turns[0], dict)
    assert second_new_turns[0]["content"] == "第 2 轮问题"
    assert sixth_response is not None
    assert '"token": "你叫小明。"' in sixth_response.text

    summary_trace = next(
        trace
        for trace in observability.list_traces()
        if trace["call_type"] == "summarize"
    )
    assert summary_trace["prompt_tokens"] == 28
    assert summary_trace["completion_tokens"] == 9


def test_summary_failure_does_not_fail_chat_or_discard_raw_history(
    monkeypatch: pytest.MonkeyPatch,
    trace_db: None,
) -> None:
    """A failed summary leaves overflow turns available for a later retry."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("DEEPSEEK_MODEL", "test-model")
    store = RedisSessionStore(FakeRedis())
    monkeypatch.setattr(main, "session_store", store)
    for index in range(1, 5):
        store.append(
            "session-1",
            {"role": "user", "content": f"user {index}"},
            {"role": "assistant", "content": f"assistant {index}"},
        )

    class FakeCompletions:
        def create(
            self,
            *,
            model: str,
            messages: list[dict[str, str]],
            stream: bool = False,
            stream_options: dict[str, bool] | None = None,
        ) -> object:
            if stream:
                return iter(
                    [
                        ChatCompletionChunk.model_construct(
                            choices=[
                                {
                                    "delta": {"content": "正常回复"},
                                    "index": 0,
                                    "finish_reason": None,
                                }
                            ]
                        )
                    ]
                )
            raise RuntimeError("summary service unavailable")

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAI:
        def __init__(self, *, api_key: str, base_url: str) -> None:
            self.chat = FakeChat()

    monkeypatch.setattr("app.main.OpenAI", FakeOpenAI)
    response = client.post(
        "/chat", json={"session_id": "session-1", "message": "第五轮"}
    )

    assert response.status_code == 200
    assert response.text.endswith("data: [DONE]\n\n")
    state = store.get_state("session-1")
    assert state["summary"] == ""
    assert len(state["history"]) == 10
    summary_trace = next(
        trace
        for trace in observability.list_traces()
        if trace["call_type"] == "summarize"
    )
    assert summary_trace["status"] == "error"


def test_redis_read_failure_returns_503_before_streaming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redis outages are reported before the SSE response starts."""
    from redis.exceptions import ConnectionError as RedisConnectionError

    class BrokenRedis(FakeRedis):
        def get(self, name: str) -> str | None:
            raise RedisConnectionError("redis unavailable")

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("DEEPSEEK_MODEL", "test-model")
    monkeypatch.setattr(main, "session_store", RedisSessionStore(BrokenRedis()))

    response = client.post(
        "/chat", json={"session_id": "session-1", "message": "hello"}
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Session storage is unavailable"


def test_trace_database_migrates_legacy_table(
    trace_db: None,
) -> None:
    """An existing SQLite trace table gains call_type without losing rows."""
    with closing(sqlite3.connect(observability.DB_PATH)) as connection:
        with connection:
            connection.execute(
                """
            CREATE TABLE llm_traces (
                id INTEGER PRIMARY KEY,
                trace_id TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                model TEXT NOT NULL,
                messages TEXT NOT NULL,
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                latency_ms INTEGER NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('success', 'error')),
                error TEXT
            )
                """
            )

    def fail() -> object:
        raise RuntimeError("expected test failure")

    with pytest.raises(RuntimeError):
        observability.track_llm_call(
            fail,
            model="test-model",
            messages=[{"role": "user", "content": "hello"}],
        )

    trace = observability.list_traces()[0]
    assert trace["call_type"] == "llm"
    assert trace["status"] == "error"


def test_failed_llm_call_is_recorded(
    monkeypatch: pytest.MonkeyPatch,
    trace_db: None,
) -> None:
    """An LLM exception is recorded with a sanitized error and re-raised."""
    def fail() -> object:
        raise RuntimeError("request failed with sk-testcredential123456")

    with pytest.raises(RuntimeError):
        observability.track_llm_call(
            fail,
            model="test-model",
            messages=[{"role": "user", "content": "hello"}],
            stream=False,
        )

    trace = observability.list_traces()[0]
    detail = observability.get_trace(trace["trace_id"])
    assert trace["status"] == "error"
    assert detail is not None
    assert detail["error"] == "request failed with [REDACTED]"


def test_closing_stream_records_error(
    trace_db: None,
) -> None:
    """Closing a partially consumed stream still persists its trace."""
    chunks = observability.track_llm_call(
        lambda: iter([object(), object()]),
        model="test-model",
        messages=[{"role": "user", "content": "hello"}],
        stream=True,
    )

    next(chunks)
    chunks.close()

    trace = observability.list_traces()[0]
    assert trace["status"] == "error"
    detail = observability.get_trace(trace["trace_id"])
    assert detail is not None
    assert detail["error"] == "GeneratorExit"
