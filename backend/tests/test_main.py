"""Tests for the FastAPI application."""

import json
from collections.abc import Iterable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openai.types.chat import ChatCompletionChunk

from app import observability
from app.main import app

client = TestClient(app)


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

    class FakeCompletions:
        def create(
            self,
            *,
            model: str,
            messages: list[dict[str, str]],
            response_format: dict[str, str],
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
            assert messages[0]["content"].startswith("你是 AI 内容运营助手")
            assert '"hook_type"' in messages[0]["content"]
            assert "结果承诺" in messages[0]["content"]
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
            assert response_format == {"type": "json_object"}
            assert stream is True
            assert stream_options == {"include_usage": True}
            return iter(
                [
                    ChatCompletionChunk.model_construct(
                        choices=[
                            {
                                "delta": {"content": '{"hook_type":"反常' },
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
                                "delta": {"content": '识提问","score":82}'},
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
            "messages": [
                {"role": "system", "content": "Return XML, not JSON."},
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
    assert json.loads(token_text) == {"hook_type": "反常识提问", "score": 82}
    assert frames[-1] == "data: [DONE]"

    traces = client.get("/traces")
    assert traces.status_code == 200
    trace = traces.json()["items"][0]
    assert trace["model"] == "test-model"
    assert trace["prompt_tokens"] == 37
    assert trace["completion_tokens"] == 12
    assert trace["status"] == "success"
    assert "messages" not in trace

    detail = client.get(f"/traces/{trace['trace_id']}")
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["messages"][-1]["content"] == (
        "夏天 [REDACTED] [REDACTED] [REDACTED] api_key=[REDACTED]"
    )
    assert "test-key" not in str(detail_body["messages"])
    assert "sk-testcredential123456" not in str(detail_body["messages"])
    assert "sk_live_abcdefghijklmnop" not in str(detail_body["messages"])
    assert "other-secret-value" not in str(detail_body["messages"])


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
