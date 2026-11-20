"""Tests for the FastAPI application."""

import json
from collections.abc import Iterable

import pytest
from fastapi.testclient import TestClient
from openai.types.chat import ChatCompletionChunk

from app.main import app

client = TestClient(app)


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
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "POST" in response.headers["access-control-allow-methods"]


def test_chat_streams_sse_tokens_and_done(
    monkeypatch: pytest.MonkeyPatch,
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
                {"role": "user", "content": "夏天"},
            ]
            assert response_format == {"type": "json_object"}
            assert stream is True
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
                {"role": "user", "content": "夏天"},
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
