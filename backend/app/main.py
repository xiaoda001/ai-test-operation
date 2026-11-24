"""FastAPI application entry point."""

import json
import os
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Literal, cast

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from openai import OpenAI
from openai.types.chat import ChatCompletionChunk, ChatCompletionMessageParam
from pydantic import BaseModel

from app.observability import (
    TraceDetail,
    get_trace,
    list_traces,
    track_llm_call,
)

app = FastAPI()
PROMPT_TEMPLATE_PATH = Path(__file__).parent / "prompts" / "hook_analysis.txt"
frontend_origins = os.getenv("FRONTEND_ORIGINS", "http://localhost:10111")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip() for origin in frontend_origins.split(",") if origin.strip()
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
    allow_credentials=False,
)


class ChatMessage(BaseModel):
    """A message accepted by the chat endpoint."""

    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    """The request body for a streaming chat completion."""

    messages: list[ChatMessage]


class TraceListItem(BaseModel):
    """One trace summary returned by the list endpoint."""

    id: int
    trace_id: str
    created_at: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    status: str
    error: str | None


class TraceListResponse(BaseModel):
    """Response model for recent LLM traces."""

    items: list[TraceListItem]


class TraceDetailResponse(BaseModel):
    """Response model for one LLM trace."""

    id: int
    trace_id: str
    created_at: str
    model: str
    messages: list[ChatMessage]
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    status: str
    error: str | None


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


@app.get("/traces", response_model=TraceListResponse)
def traces(limit: int = 100) -> dict[str, list[TraceListItem]]:
    """Return recent LLM traces without their message payloads."""
    if not 1 <= limit <= 500:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 500")
    return {"items": [TraceListItem(**trace) for trace in list_traces(limit)]}


@app.get("/traces/{trace_id}", response_model=TraceDetailResponse)
def trace_detail(trace_id: str) -> TraceDetail:
    """Return a trace with its messages for replay."""
    trace = get_trace(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Trace not found")
    return trace


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
    system_prompt = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8").strip()
    messages = [
        {"role": "system", "content": system_prompt},
        *[
            message.model_dump()
            for message in request.messages
            if message.role != "system"
        ],
    ]
    chunks = track_llm_call(
        lambda: client.chat.completions.create(
            model=cast(str, config["DEEPSEEK_MODEL"]),
            messages=cast(list[ChatCompletionMessageParam], messages),
            response_format={"type": "json_object"},
            stream=True,
            stream_options={"include_usage": True},
        ),
        model=cast(str, config["DEEPSEEK_MODEL"]),
        messages=messages,
        api_key=cast(str, config["DEEPSEEK_API_KEY"]),
        stream=True,
    )
    return StreamingResponse(stream_tokens(chunks), media_type="text/event-stream")
