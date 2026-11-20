"""Tests for the FastAPI application."""

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
            stream: bool,
        ) -> Iterable[ChatCompletionChunk]:
            assert model == "test-model"
            assert messages == [{"role": "user", "content": "夏天"}]
            assert stream is True
            return iter(
                [
                    ChatCompletionChunk.model_construct(
                        choices=[
                            {
                                "delta": {"content": "夏日"},
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
                                "delta": {"content": "微风"},
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
        json={"messages": [{"role": "user", "content": "夏天"}]},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text == (
        'data: {"token": "夏日"}\n\n'
        'data: {"token": "微风"}\n\n'
        "data: [DONE]\n\n"
    )
