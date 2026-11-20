"""Make a single DeepSeek chat-completions request for a connectivity check."""

import os
import sys

from openai import OpenAI

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
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "你好"}],
    )
    text = response.choices[0].message.content
    if text is None:
        raise RuntimeError("DeepSeek returned an empty response.")
    print(text)


if __name__ == "__main__":
    main()
