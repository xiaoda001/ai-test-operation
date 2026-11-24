"""Lightweight SQLite tracing for LLM calls."""

import json
import re
import sqlite3
import sys
import time
import uuid
from collections.abc import Callable, Generator, Iterable, Mapping, Sequence
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypedDict, TypeVar, cast, overload

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "traces.sqlite3"
_SECRET_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]+=*"),
    re.compile(
        r"(?i)((?:sk[-_](?:live|test)?[-_]?|ghp_|github_pat_|"
        r"xox[baprs]-|AIza|AKIA)"
        r"[A-Za-z0-9_-]{8,})"
    ),
    re.compile(
        r"(?i)(['\"]?(?:api[_-]?key|access[_-]?token|secret(?:[_-]?key)?|"
        r"password|token)['\"]?\s*[:=]\s*['\"]?)"
        r"[A-Za-z0-9._~+/-]+=*"
    ),
)

T = TypeVar("T")


class TraceSummary(TypedDict):
    """Fields returned for a trace list item."""

    id: int
    trace_id: str
    created_at: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    status: str
    error: str | None


class TraceDetail(TraceSummary):
    """Fields returned for a trace detail item."""

    messages: list[dict[str, str]]


def _redact(value: str, api_key: str | None) -> str:
    if api_key:
        value = value.replace(api_key, "[REDACTED]")
    value = _SECRET_PATTERNS[0].sub(r"\1[REDACTED]", value)
    value = _SECRET_PATTERNS[1].sub("[REDACTED]", value)
    return _SECRET_PATTERNS[2].sub(r"\1[REDACTED]", value)


def _safe_messages(
    messages: Sequence[Mapping[str, object]], api_key: str | None
) -> str:
    serialized = json.dumps(messages, ensure_ascii=False)
    return _redact(serialized, api_key)


def _usage_counts(response: object) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0
    prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
    completion_tokens = getattr(usage, "completion_tokens", 0) or 0
    return int(prompt_tokens), int(completion_tokens)


def _initialize_database() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(DB_PATH)) as connection:
        with connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_traces (
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
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_llm_traces_created_at "
                "ON llm_traces (created_at DESC)"
            )


def _persist_trace(
    *,
    trace_id: str,
    created_at: str,
    model: str,
    messages: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: int,
    status: Literal["success", "error"],
    error: str | None,
) -> None:
    _initialize_database()
    with closing(sqlite3.connect(DB_PATH)) as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO llm_traces (
                    trace_id, created_at, model, messages, prompt_tokens,
                    completion_tokens, latency_ms, status, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace_id,
                    created_at,
                    model,
                    messages,
                    prompt_tokens,
                    completion_tokens,
                    latency_ms,
                    status,
                    error,
                ),
            )


def _record_safely(
    *,
    trace_id: str,
    created_at: str,
    model: str,
    messages: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: int,
    status: Literal["success", "error"],
    error: str | None,
) -> None:
    try:
        _persist_trace(
            trace_id=trace_id,
            created_at=created_at,
            model=model,
            messages=messages,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            status=status,
            error=error,
        )
    except Exception as write_error:
        print(
            f"Failed to persist LLM trace {trace_id}: {write_error}",
            file=sys.stderr,
        )


def _trace_stream(
    chunks: Iterable[T],
    *,
    trace_id: str,
    started_at: float,
    created_at: str,
    model: str,
    messages: str,
    api_key: str | None,
) -> Generator[T, None, None]:
    prompt_tokens = 0
    completion_tokens = 0
    try:
        for chunk in chunks:
            chunk_prompt_tokens, chunk_completion_tokens = _usage_counts(chunk)
            if getattr(chunk, "usage", None) is not None:
                prompt_tokens = chunk_prompt_tokens
                completion_tokens = chunk_completion_tokens
            yield chunk
    except BaseException as call_error:
        _record_safely(
            trace_id=trace_id,
            created_at=created_at,
            model=model,
            messages=messages,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=int((time.perf_counter() - started_at) * 1000),
            status="error",
            error=_redact(str(call_error) or type(call_error).__name__, api_key),
        )
        raise
    else:
        _record_safely(
            trace_id=trace_id,
            created_at=created_at,
            model=model,
            messages=messages,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=int((time.perf_counter() - started_at) * 1000),
            status="success",
            error=None,
        )


@overload
def track_llm_call(
    call: Callable[[], T],
    *,
    model: str,
    messages: Sequence[Mapping[str, object]],
    api_key: str | None = None,
    stream: Literal[False] = False,
) -> T: ...


@overload
def track_llm_call(
    call: Callable[[], Iterable[T]],
    *,
    model: str,
    messages: Sequence[Mapping[str, object]],
    api_key: str | None = None,
    stream: Literal[True],
) -> Generator[T, None, None]: ...


def track_llm_call(
    call: Callable[[], object],
    *,
    model: str,
    messages: Sequence[Mapping[str, object]],
    api_key: str | None = None,
    stream: bool = False,
) -> object:
    """Run and record a normal or streaming LLM call without changing its output."""
    trace_id = str(uuid.uuid4())
    created_at = datetime.now(UTC).isoformat()
    started_at = time.perf_counter()
    safe_messages = _safe_messages(messages, api_key)
    try:
        response = call()
    except Exception as call_error:
        _record_safely(
            trace_id=trace_id,
            created_at=created_at,
            model=model,
            messages=safe_messages,
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=int((time.perf_counter() - started_at) * 1000),
            status="error",
            error=_redact(str(call_error), api_key),
        )
        raise

    if stream:
        return _trace_stream(
            cast(Iterable[object], response),
            trace_id=trace_id,
            started_at=started_at,
            created_at=created_at,
            model=model,
            messages=safe_messages,
            api_key=api_key,
        )

    prompt_tokens, completion_tokens = _usage_counts(response)
    _record_safely(
        trace_id=trace_id,
        created_at=created_at,
        model=model,
        messages=safe_messages,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=int((time.perf_counter() - started_at) * 1000),
        status="success",
        error=None,
    )
    return response


def _summary_from_row(row: sqlite3.Row) -> TraceSummary:
    return {
        "id": int(row["id"]),
        "trace_id": str(row["trace_id"]),
        "created_at": str(row["created_at"]),
        "model": str(row["model"]),
        "prompt_tokens": int(row["prompt_tokens"]),
        "completion_tokens": int(row["completion_tokens"]),
        "latency_ms": int(row["latency_ms"]),
        "status": str(row["status"]),
        "error": cast(str | None, row["error"]),
    }


def list_traces(limit: int = 100) -> list[TraceSummary]:
    """Return recent traces, newest first."""
    _initialize_database()
    with closing(sqlite3.connect(DB_PATH)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT id, trace_id, created_at, model, prompt_tokens,
                   completion_tokens, latency_ms, status, error
            FROM llm_traces
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_summary_from_row(row) for row in rows]


def get_trace(trace_id: str) -> TraceDetail | None:
    """Return one trace, including its replay messages, when it exists."""
    _initialize_database()
    with closing(sqlite3.connect(DB_PATH)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM llm_traces WHERE trace_id = ?",
            (trace_id,),
        ).fetchone()
    if row is None:
        return None
    messages = cast(list[dict[str, str]], json.loads(str(row["messages"])))
    return {**_summary_from_row(row), "messages": messages}
