"""Tests for DeepSeek smoke-script configuration handling."""

import pytest

from scripts.deepseek_smoke import main


def test_missing_api_key_has_clear_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The script stops with a clear message when no API key is configured."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(SystemExit):
        main()

    assert "DEEPSEEK_API_KEY" in capsys.readouterr().err
