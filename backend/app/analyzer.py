"""提供独立的短视频开场钩子分析能力。"""

import os
from pathlib import Path
from typing import TypedDict, cast

from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel, ConfigDict, Field

from app.observability import track_llm_call

HOOK_PROMPT_TEMPLATE_PATH = Path(__file__).parent / "prompts" / "hook_analysis.txt"


class HookAnalysis(TypedDict):
    """钩子分析结果，包含类型和吸引力评分。"""

    hook_type: str
    score: int


class HookAnalysisResponse(BaseModel):
    """用于校验模型钩子分析 JSON 的结构。"""

    model_config = ConfigDict(extra="forbid")

    hook_type: str
    score: int = Field(ge=0, le=100)


def analyze_hook(text: str) -> HookAnalysis:
    """调用模型分析短视频开场钩子，并返回经过校验的字段字典。"""
    config = {
        "DEEPSEEK_API_KEY": os.getenv("DEEPSEEK_API_KEY"),
        "DEEPSEEK_BASE_URL": os.getenv("DEEPSEEK_BASE_URL"),
        "DEEPSEEK_MODEL": os.getenv("DEEPSEEK_MODEL"),
    }
    missing = [name for name, value in config.items() if not value]
    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}"
        )

    model = cast(str, config["DEEPSEEK_MODEL"])
    api_key = cast(str, config["DEEPSEEK_API_KEY"])
    messages = [
        {
            "role": "system",
            "content": HOOK_PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8").strip(),
        },
        {"role": "user", "content": text},
    ]
    client = OpenAI(
        api_key=api_key,
        base_url=cast(str, config["DEEPSEEK_BASE_URL"]),
    )
    response = track_llm_call(
        lambda: client.chat.completions.create(
            model=model,
            messages=cast(list[ChatCompletionMessageParam], messages),
            response_format={"type": "json_object"},
        ),
        model=model,
        messages=messages,
        api_key=api_key,
    )
    if not response.choices or response.choices[0].message.content is None:
        raise ValueError("钩子分析模型未返回内容")
    result = HookAnalysisResponse.model_validate_json(
        response.choices[0].message.content
    )
    return {"hook_type": result.hook_type, "score": result.score}
