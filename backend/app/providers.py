from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol

import httpx

from .config import Settings
from .prompts import PromptDefinition


class ModelGenerationError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        category: str,
        diagnostics: dict[str, object] | None = None,
    ):
        super().__init__(message)
        self.category = category
        self.diagnostics = diagnostics or {}


class ModelOutputTruncatedError(ModelGenerationError):
    def __init__(self, response_chars: int, finish_reason: str):
        super().__init__(
            f"模型输出因长度上限被截断（已返回 {response_chars} 字符），请重试",
            category="output_truncated",
            diagnostics={
                "response_chars": response_chars,
                "finish_reason": finish_reason,
            },
        )


class ModelProvider(Protocol):
    name: str
    model: str

    async def generate(self, prompt: PromptDefinition, source: str) -> str: ...


@dataclass
class DemoProvider:
    name: str = "demo"
    model: str = "deterministic-editor-v1"

    async def generate(self, prompt: PromptDefinition, source: str) -> str:
        cleaned = re.sub(r"\s+", " ", source).strip()
        excerpt = cleaned[:520]
        indexes = re.findall(r"content_[0-9a-f]{8,40}", source)
        unique_indexes = list(dict.fromkeys(indexes))
        knowledge_ids = re.findall(r"knowledge_[0-9a-f]{24}", source)
        unique_knowledge_ids = list(dict.fromkeys(knowledge_ids))
        if prompt.id == "book_analysis":
            fragment_matches = re.findall(
                r"\[content_index: (content_[0-9a-f]{8,40})\]\n"
                r"\[章节路径: [^\]]*\]\n"
                r"(.*?)(?=\n\[content_index: |\n#{1,6} |\Z)",
                source,
                flags=re.S,
            )
            index, fragment_text = (
                fragment_matches[-1]
                if fragment_matches
                else ("content_demo", excerpt or "演示原文。")
            )
            sentences = [
                item.strip()
                for item in re.split(r"(?<=[。！？!?])", fragment_text)
                if item.strip()
            ]
            exact_text = sentences[0] if sentences else fragment_text.strip()
            return json.dumps(
                {
                    "chapter_title": "测试章节",
                    "chapter_theme": "本章围绕核心概念及其现实影响展开。",
                    "subtopics": [
                        {
                            "title": "核心问题",
                            "definitions": [],
                            "quotes": [
                                {
                                    "text": exact_text,
                                    "source_content_indexes": [index],
                                }
                            ],
                            "viewpoints": [
                                {
                                    "text": exact_text,
                                    "source_content_indexes": [index],
                                    "arguments": [
                                        {
                                            "text": exact_text,
                                            "source_content_indexes": [index],
                                        }
                                    ],
                                    "case": None,
                                }
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
            )
        if prompt.id == "chapter_compression":
            return source
        if prompt.id == "json_repair":
            return source
        if prompt.id == "mind_map":
            return "# 测试书\n- 核心知识\n  - 主要观点\n  - 关键案例"
        if prompt.id == "album_outline":
            if unique_knowledge_ids:
                asset_indexes: dict[str, list[str]] = {}
                for position, knowledge_id in enumerate(unique_knowledge_ids):
                    start = source.find(knowledge_id)
                    next_id = (
                        unique_knowledge_ids[position + 1]
                        if position + 1 < len(unique_knowledge_ids)
                        else ""
                    )
                    end = source.find(next_id, start + len(knowledge_id)) if next_id else -1
                    segment = source[start:end if end >= 0 else None]
                    asset_indexes[knowledge_id] = list(
                        dict.fromkeys(
                            re.findall(r"content_[0-9a-f]{8,40}", segment)
                        )
                    )
                outline = [
                    {
                        "title": f"声音 {position}",
                        "main_points": (
                            "核心主题：解释这一知识资产要解决的关键问题。\n"
                            "核心要点：\n"
                            "1. 从具体现象进入；\n"
                            "2. 梳理作者的解释机制；\n"
                            "3. 说明它对现实理解的意义。"
                        ),
                        "section_identifier": (
                            "章节：测试章节 子主题：核心知识 原文索引："
                            + "、".join(
                                asset_indexes.get(knowledge_id)
                                or unique_indexes[:1]
                            )
                        ),
                        "knowledge_item_ids": [knowledge_id],
                        "content_type": "解读类",
                    }
                    for position, knowledge_id in enumerate(
                        unique_knowledge_ids[:12], start=1
                    )
                ]
            else:
                outline = [
                    {
                        "title": f"声音 {position}",
                        "main_points": (
                            "核心主题：解释该原文内容提出的关键问题。\n"
                            "核心要点：\n"
                            "1. 呈现具体现象；\n"
                            "2. 梳理原因与机制；\n"
                            "3. 说明现实影响。"
                        ),
                        "section_identifier": index,
                        "content_type": "解读类",
                    }
                    for position, index in enumerate(unique_indexes[:12], start=1)
                ]
            return json.dumps({"album_outline": outline}, ensure_ascii=False)
        if prompt.id == "character_relationships":
            return (
                '{"relationships":[{"characters":["人物甲","人物乙"],'
                '"relationship":"人物甲与人物乙在当前事件中相互影响",'
                '"evidence":"当前原文块中的人物互动"}]}'
            )
        if prompt.id in {"episode_outline_narrative", "episode_outline_non_narrative"}:
            return (
                "# 声音细纲\n\n"
                "## 开篇\n以一个具体问题进入主题，让听众先看到它与自身经验的关系。\n\n"
                "## 第一部分：原书提出了什么\n"
                f"- 核心材料：{excerpt[:180]}\n\n"
                "## 第二部分：作者如何论证\n"
                "- 梳理论据、案例与概念之间的关系，并保留来源标识。\n\n"
                "## 第三部分：对现实有什么启发\n"
                "- 明确哪些是作者观点，哪些是编辑解释。\n\n"
                "## 结尾\n回到开篇问题，给出克制而可执行的总结。"
            )
        if prompt.id == "episode_draft":
            return (
                "很多时候，我们以为自己面对的是一个简单的是非题，但原书真正提醒我们的，"
                "是先把问题放回它的完整背景里。\n\n"
                f"{excerpt}\n\n"
                "这段材料的重要之处，不只是给出了一个结论，而是展示了判断形成的过程："
                "先区分概念，再寻找事实与理由，最后才决定应当采取什么立场。"
                "如果跳过这个过程，表达就很容易变成立场先行。\n\n"
                "因此，理解一本书不是记住几句漂亮的话，而是能够复原作者的论证结构，"
                "也知道它的边界在哪里。"
            )
        if prompt.id == "episode_final":
            return (
                "我们常把复杂问题压缩成一句“到底谁对谁错”。可真正困难的地方，"
                "往往不在表态，而在判断之前。\n\n"
                f"{excerpt}\n\n"
                "你会发现，作者并没有急着给出一个情绪化答案。他先把概念分开，"
                "再把事实、理由和可能的后果摆在一起。只有这样，结论才不是一句口号。\n\n"
                "讲书也是一样。真正值得带走的，不只是作者说了什么，"
                "而是他如何一步步走到这个结论，以及这个结论在哪些地方仍需要保持谨慎。"
            )
        return f"# {prompt.id}\n\n{excerpt}"


@dataclass
class OpenAICompatibleProvider:
    settings: Settings
    name: str = "openai-compatible"

    @property
    def model(self) -> str:
        return self.settings.model

    async def generate(self, prompt: PromptDefinition, source: str) -> str:
        if not self.settings.api_key:
            raise RuntimeError("尚未配置 AI_BOOK_STUDIO_API_KEY")
        url = f"{self.settings.api_base.rstrip('/')}/chat/completions"
        structured_output = prompt.id in {
            "book_analysis",
            "json_repair",
            "album_outline",
        }
        high_output = prompt.id in {
            "book_analysis",
            "json_repair",
            "chapter_compression",
            "mind_map",
            "album_outline",
        }
        payload: dict[str, object] = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": prompt.system},
                {
                    "role": "user",
                    "content": prompt.user_template.format(source=source),
                },
            ],
            "temperature": 0.1 if structured_output else 0.7,
        }
        if high_output:
            payload["max_tokens"] = (
                32768 if prompt.id == "album_outline" else 16384
            )
        if structured_output:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.settings.api_key}"}
        timeout_seconds = (
            900
            if prompt.id == "album_outline"
            else 600
            if prompt.id == "mind_map"
            else 300
        )
        try:
            async with httpx.AsyncClient(
                timeout=timeout_seconds, trust_env=False
            ) as client:
                response = await client.post(url, json=payload, headers=headers)
                if structured_output and self._response_format_rejected(response):
                    payload.pop("response_format", None)
                    response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException as error:
            raise ModelGenerationError(
                f"模型请求超过 {timeout_seconds} 秒，已超时",
                category="model_timeout",
                diagnostics={"timeout_seconds": timeout_seconds},
            ) from error
        except httpx.HTTPStatusError as error:
            raise ModelGenerationError(
                f"模型网关请求失败（HTTP {error.response.status_code}）",
                category="gateway_http_error",
                diagnostics={"http_status": error.response.status_code},
            ) from error
        except httpx.RequestError as error:
            raise ModelGenerationError(
                "模型网关连接失败",
                category="gateway_connection_error",
                diagnostics={"error_type": type(error).__name__},
            ) from error
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise ModelGenerationError(
                "模型网关返回结构无效",
                category="invalid_gateway_response",
            ) from error
        finish_reason = choice.get("finish_reason")
        if finish_reason == "length":
            raise ModelOutputTruncatedError(len(content or ""), finish_reason)
        if finish_reason not in {None, "stop"}:
            raise ModelGenerationError(
                f"模型未正常完成输出（finish_reason={finish_reason}）",
                category="abnormal_finish_reason",
                diagnostics={
                    "response_chars": len(content or ""),
                    "finish_reason": finish_reason,
                },
            )
        if not isinstance(content, str) or not content.strip():
            raise ModelGenerationError(
                "模型未返回文本内容",
                category="empty_model_output",
                diagnostics={
                    "response_chars": 0,
                    "finish_reason": finish_reason,
                },
            )
        return content

    @staticmethod
    def _response_format_rejected(response: httpx.Response) -> bool:
        if response.status_code not in {400, 422}:
            return False
        try:
            detail = json.dumps(response.json(), ensure_ascii=False).lower()
        except Exception:
            detail = getattr(response, "text", "").lower()
        return any(
            marker in detail
            for marker in ("response_format", "json_object", "unsupported")
        )


@dataclass
class AnthropicProvider:
    settings: Settings
    name: str = "anthropic"

    @property
    def model(self) -> str:
        return self.settings.model

    async def generate(self, prompt: PromptDefinition, source: str) -> str:
        if not self.settings.api_key:
            raise RuntimeError("尚未配置 AI_BOOK_STUDIO_API_KEY")
        url = anthropic_messages_url(self.settings.api_base)
        user_message = (
            f"【系统要求】\n{prompt.system.strip()}\n\n"
            f"【任务】\n{prompt.user_template.format(source=source).strip()}"
        )
        payload = {
            "model": self.settings.model,
            "max_tokens": 16384,
            "messages": [{"role": "user", "content": user_message}],
        }
        headers = {
            "x-api-key": self.settings.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        timeout_seconds = (
            900
            if prompt.id == "album_outline"
            else 600
            if prompt.id == "mind_map"
            else 300
        )
        try:
            async with httpx.AsyncClient(
                timeout=timeout_seconds, trust_env=False
            ) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise RuntimeError(
                f"Anthropic 网关请求失败（HTTP {error.response.status_code}）"
            ) from error
        except httpx.RequestError as error:
            raise RuntimeError("Anthropic 网关连接失败") from error
        data = response.json()
        text_blocks = [
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text" and block.get("text")
        ]
        if not text_blocks:
            raise RuntimeError("Anthropic 网关未返回文本内容")
        return "\n".join(text_blocks)


def anthropic_messages_url(api_base: str) -> str:
    normalized = api_base.rstrip("/")
    if normalized.endswith("/v1"):
        return f"{normalized}/messages"
    return f"{normalized}/v1/messages"


def build_provider(settings: Settings) -> ModelProvider:
    if settings.provider == "demo":
        return DemoProvider()
    if settings.provider == "anthropic":
        return AnthropicProvider(settings)
    return OpenAICompatibleProvider(settings)
