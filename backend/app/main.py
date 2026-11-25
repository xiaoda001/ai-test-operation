"""FastAPI application entry point."""

import json
import os
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Literal, cast

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from openai import OpenAI
from openai.types.chat import ChatCompletionChunk, ChatCompletionMessageParam
from pydantic import BaseModel
from redis.exceptions import RedisError

from app.observability import (
    TraceDetail,
    get_trace,
    list_traces,
    track_llm_call,
)
from app.session_store import Message, SessionStore, create_session_store

app = FastAPI()
CHAT_PROMPT_TEMPLATE_PATH = Path(__file__).parent / "prompts" / "chat_general.txt"
SUMMARY_PROMPT_TEMPLATE_PATH = (
    Path(__file__).parent / "prompts" / "conversation_summary.txt"
)
SUMMARY_BATCH_TURNS = 4
RECENT_TURNS = int(os.getenv("RECENT_TURNS", "4"))
if RECENT_TURNS < 1:
    raise ValueError("RECENT_TURNS must be a positive integer")
session_store: SessionStore = create_session_store()
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

    session_id: str
    message: str


class TraceListItem(BaseModel):
    """One trace summary returned by the list endpoint."""

    id: int
    trace_id: str
    created_at: str
    model: str
    call_type: str
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
    call_type: str
    messages: list[ChatMessage]
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    status: str
    error: str | None


def truncate_history(
    history: list[Message], user_message: Message, recent_turns: int
) -> list[Message]:
    """保留最近的完整历史轮次和当前用户消息。"""
    kept_history = history[-2 * recent_turns :] if recent_turns else []
    return [*kept_history, user_message]


def compress_session(
    *,
    session_id: str,
    client: OpenAI,
    model: str,
    api_key: str,
    recent_turns: int,
) -> None:
    """Roll older complete turns into a summary without failing the chat stream."""
    try:
        state = session_store.get_state(session_id)
        overflow_messages = max(0, len(state["history"]) - 2 * recent_turns)
        batch_size = min(overflow_messages, 2 * SUMMARY_BATCH_TURNS)
        if batch_size == 0:
            return

        moved_messages = state["history"][:batch_size]
        summary_input = {
            "existing_summary": state["summary"],
            "new_turns": moved_messages,
        }
        messages = [
            {
                "role": "system",
                "content": SUMMARY_PROMPT_TEMPLATE_PATH.read_text(
                    encoding="utf-8"
                ).strip(),
            },
            {
                "role": "user",
                "content": json.dumps(summary_input, ensure_ascii=False),
            },
        ]
        response = track_llm_call(
            lambda: client.chat.completions.create(
                model=model,
                messages=cast(list[ChatCompletionMessageParam], messages),
                stream=False,
            ),
            model=model,
            messages=messages,
            api_key=api_key,
            call_type="summarize",
        )
        if not response.choices or response.choices[0].message.content is None:
            raise ValueError("摘要模型未返回内容")

        new_summary = response.choices[0].message.content.strip()
        if not new_summary:
            raise ValueError("摘要模型返回了空摘要")
        committed = session_store.commit_summary(
            session_id,
            expected_summary=state["summary"],
            moved_messages=moved_messages,
            new_summary=new_summary,
        )
        if not committed:
            print("Session summary skipped after concurrent update", file=sys.stderr)
    except Exception as summary_error:
        print(
            f"Session summary failed ({type(summary_error).__name__})",
            file=sys.stderr,
        )


def stream_tokens(
    chunks: Iterable[ChatCompletionChunk],
    *,
    session_id: str,
    user_message: Message,
    client: OpenAI,
    model: str,
    api_key: str,
    recent_turns: int,
) -> Iterator[str]:
    """编码流式消息帧，并在上游流正常结束后保存本轮对话。"""
    assistant_content: list[str] = []
    for chunk in chunks:
        if not chunk.choices:
            continue
        content = chunk.choices[0].delta.content
        if content is not None:
            assistant_content.append(content)
            token = json.dumps({"token": content}, ensure_ascii=False)
            yield f"data: {token}\n\n"
    session_store.append(
        session_id,
        user_message,
        {"role": "assistant", "content": "".join(assistant_content)},
    )
    compress_session(
        session_id=session_id,
        client=client,
        model=model,
        api_key=api_key,
        recent_turns=recent_turns,
    )
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
    user_message: Message = {"role": "user", "content": request.message}
    try:
        state = session_store.get_state(request.session_id)
    except RedisError as redis_error:
        raise HTTPException(
            status_code=503, detail="Session storage is unavailable"
        ) from redis_error
    conversation = truncate_history(state["history"], user_message, RECENT_TURNS)
    system_prompt = CHAT_PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8").strip()
    messages = [
        {"role": "system", "content": system_prompt},
        *(
            [{"role": "user", "content": f"[历史摘要] {state['summary']}"}]
            if state["summary"]
            else []
        ),
        *conversation,
    ]
    print(f"Sending messages to model: {messages}")
    chunks = track_llm_call(
        lambda: client.chat.completions.create(
            model=cast(str, config["DEEPSEEK_MODEL"]),
            messages=cast(list[ChatCompletionMessageParam], messages),
            stream=True,
            stream_options={"include_usage": True},
        ),
        model=cast(str, config["DEEPSEEK_MODEL"]),
        messages=messages,
        api_key=cast(str, config["DEEPSEEK_API_KEY"]),
        call_type="chat",
        stream=True,
    )
    return StreamingResponse(
        stream_tokens(
            chunks,
            session_id=request.session_id,
            user_message=user_message,
            client=client,
            model=cast(str, config["DEEPSEEK_MODEL"]),
            api_key=cast(str, config["DEEPSEEK_API_KEY"]),
            recent_turns=RECENT_TURNS,
        ),
        media_type="text/event-stream",
    )
