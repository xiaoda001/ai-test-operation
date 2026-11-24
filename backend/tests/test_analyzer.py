"""测试独立的钩子分析能力。"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from openai.types.chat import ChatCompletion

from app import analyzer, observability


@pytest.fixture
def trace_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """使用测试目录中的 SQLite 文件，并在测试结束后清理。"""
    path = Path(__file__).with_name(".test-analyzer-traces.sqlite3")
    path.unlink(missing_ok=True)
    monkeypatch.setattr(observability, "DB_PATH", path)
    yield
    path.unlink(missing_ok=True)


def test_analyze_hook_returns_validated_json_and_records_trace(
    monkeypatch: pytest.MonkeyPatch,
    trace_db: None,
) -> None:
    """钩子分析使用专属提示词和 JSON 模式，并记录调用埋点。"""
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
        ) -> ChatCompletion:
            assert model == "test-model"
            assert messages[0]["content"].startswith("你是 AI 内容运营助手")
            assert '"hook_type"' in messages[0]["content"]
            assert messages[1] == {
                "role": "user",
                "content": "三天改掉早起习惯",
            }
            assert response_format == {"type": "json_object"}
            return ChatCompletion.model_construct(
                choices=[
                    {
                        "message": {
                            "content": '{"hook_type":"结果承诺","score":78}'
                        }
                    }
                ],
                usage={"prompt_tokens": 12, "completion_tokens": 8},
            )

    class FakeChat:
        completions = FakeCompletions()

    class FakeOpenAI:
        def __init__(self, *, api_key: str, base_url: str) -> None:
            assert api_key == "test-key"
            assert base_url == "https://example.invalid/v1"
            self.chat = FakeChat()

    monkeypatch.setattr("app.analyzer.OpenAI", FakeOpenAI)

    result = analyzer.analyze_hook("三天改掉早起习惯")

    assert result == {"hook_type": "结果承诺", "score": 78}
    trace = observability.list_traces()[0]
    assert trace["model"] == "test-model"
    assert trace["prompt_tokens"] == 12
    assert trace["completion_tokens"] == 8
    assert trace["status"] == "success"
