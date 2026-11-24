"""Tests for DeepSeek smoke-script configuration handling."""

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from openai.types.chat import ChatCompletion

from app import observability
from scripts.deepseek_smoke import main


@pytest.fixture
def trace_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Use and remove a SQLite file inside the writable workspace."""
    path = Path(__file__).with_name(".test-traces.sqlite3")
    path.unlink(missing_ok=True)
    monkeypatch.setattr(observability, "DB_PATH", path)
    yield
    path.unlink(missing_ok=True)


def test_missing_api_key_has_clear_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The script stops with a clear message when no API key is configured."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(SystemExit):
        main()

    assert "DEEPSEEK_API_KEY" in capsys.readouterr().err


def test_smoke_script_can_run_directly_without_api_key() -> None:
    """Direct script execution reaches its missing-key check without import errors."""
    backend_dir = Path(__file__).resolve().parents[1]
    script_path = backend_dir / "scripts" / "deepseek_smoke.py"
    env = os.environ.copy()
    env["DEEPSEEK_API_KEY"] = ""

    result = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=backend_dir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "Missing required environment variable: DEEPSEEK_API_KEY" in result.stderr


def test_smoke_call_records_usage(
    monkeypatch: pytest.MonkeyPatch,
    trace_db: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The non-streaming smoke request uses the shared trace writer."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    class FakeCompletions:
        def create(
            self, *, model: str, messages: list[dict[str, str]]
        ) -> ChatCompletion:
            assert model == "deepseek-flash"
            assert messages == [{"role": "user", "content": "你好"}]
            return ChatCompletion.model_construct(
                choices=[{"message": {"content": "你好"}}],
                usage={"prompt_tokens": 3, "completion_tokens": 2},
            )

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAI:
        def __init__(self, *, api_key: str, base_url: str) -> None:
            assert api_key == "test-key"
            assert base_url == "https://api.deepseek.com"
            self.chat = FakeChat()

    monkeypatch.setattr("scripts.deepseek_smoke.OpenAI", FakeOpenAI)

    main()

    assert capsys.readouterr().out.strip() == "你好"
    trace = observability.list_traces()[0]
    assert trace["prompt_tokens"] == 3
    assert trace["completion_tokens"] == 2
    assert trace["status"] == "success"
