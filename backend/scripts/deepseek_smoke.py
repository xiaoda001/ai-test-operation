"""Make a single DeepSeek chat-completions request for a connectivity check."""

import os
import sys
from pathlib import Path
from typing import cast

from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.observability import track_llm_call  # noqa: E402

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"


def main() -> None:
    """Send a greeting to DeepSeek and print the response text."""
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        print(
            "Missing required environment variable: DEEPSEEK_API_KEY",
            file=sys.stderr,
        )
        raise SystemExit(1)

    base_url = os.getenv("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL)
    # 从环境配置读取模型名称，未设置时使用默认模型。
    model = os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)
    client = OpenAI(api_key=api_key, base_url=base_url)
    messages = [{"role": "user", "content": "你好"}]
    response = track_llm_call(
        lambda: client.chat.completions.create(
            model=model,
            messages=cast(list[ChatCompletionMessageParam], messages),
        ),
        model=model,
        messages=messages,
        api_key=api_key,
    )
    text = response.choices[0].message.content
    if text is None:
        raise RuntimeError("DeepSeek returned an empty response.")
    print(text)


if __name__ == "__main__":
    main()
