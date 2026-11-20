"""FastAPI application entry point."""

import json
import os
from collections.abc import Iterable, Iterator
from typing import Literal, cast

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from openai import OpenAI
from openai.types.chat import ChatCompletionChunk, ChatCompletionMessageParam
from pydantic import BaseModel

app = FastAPI()


class ChatMessage(BaseModel):
    """A message accepted by the chat endpoint."""

    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    """The request body for a streaming chat completion."""

    messages: list[ChatMessage]


def stream_tokens(chunks: Iterable[ChatCompletionChunk]) -> Iterator[str]:
    """Encode non-empty completion chunks as SSE token frames."""
    for chunk in chunks:
        if not chunk.choices:
            continue
        content = chunk.choices[0].delta.content
        if content is not None:
            token = json.dumps({"token": content}, ensure_ascii=False)
            yield f"data: {token}\n\n"
    yield "data: [DONE]\n\n"


@app.get("/health")
def health() -> dict[str, str]:
    """Return the service health status."""
    return {"status": "ok"}


@app.post("/chat")
def chat(request: ChatRequest) -> StreamingResponse:
    """Stream a DeepSeek chat completion as server-sent events."""
    config = {
        "DEEPSEEK_API_KEY": os.getenv("DEEPSEEK_API_KEY"),
        "DEEPSEEK_BASE_URL": os.getenv("DEEPSEEK_BASE_URL"),
        "DEEPSEEK_MODEL": os.getenv("DEEPSEEK_MODEL"),
    }
    missing = [name for name, value in config.items() if not value]
    if missing:
        raise HTTPException(
            status_code=503,
            detail=f"Missing required environment variables: {', '.join(missing)}",
        )

    client = OpenAI(
        api_key=cast(str, config["DEEPSEEK_API_KEY"]),
        base_url=cast(str, config["DEEPSEEK_BASE_URL"]),
    )
    chunks = client.chat.completions.create(
        model=cast(str, config["DEEPSEEK_MODEL"]),
        messages=cast(
            list[ChatCompletionMessageParam],
            [message.model_dump() for message in request.messages],
        ),
        stream=True,
    )
    return StreamingResponse(stream_tokens(chunks), media_type="text/event-stream")
