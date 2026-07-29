import asyncio
from pathlib import Path
from typing import Any

from app.config import Settings
from app.prompts import PROMPTS, PromptDefinition
from app.providers import (
    AnthropicProvider,
    ModelOutputTruncatedError,
    OpenAICompatibleProvider,
    anthropic_messages_url,
    build_provider,
)


def anthropic_settings() -> Settings:
    data_dir = Path("/tmp/ai-book-studio-provider-test")
    return Settings(
        data_dir=data_dir,
        database_path=data_dir / "studio.sqlite3",
        provider="anthropic",
        api_base="http://gateway.local/model/api",
        api_key="test-key",
        model="test-claude",
    )


def test_album_outline_prompt_uses_engaging_topic_editorial_structure() -> None:
    prompt = PROMPTS["album_outline"]
    template = prompt.user_template

    assert prompt.version == "2026-07-29.1"
    assert "没有阅读过原书" in template
    assert "连续收听" in template
    assert "听众钩子" in template
    assert "核心主题" in template
    assert "核心要点" in template
    assert "CHAPTER" in template
    assert "不要 JSON" in template
    assert "知识资产 ID" in template
    assert "段落索引" in template


class FakeResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {
            "content": [
                {"type": "text", "text": "第一段"},
                {"type": "tool_use", "name": "ignored"},
                {"type": "text", "text": "第二段"},
            ]
        }


class FakeClient:
    def __init__(self, capture: dict[str, Any], **kwargs: Any):
        capture["client_kwargs"] = kwargs
        self.capture = capture

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def post(
        self, url: str, *, json: dict[str, Any], headers: dict[str, str]
    ) -> FakeResponse:
        self.capture.update({"url": url, "json": json, "headers": headers})
        return FakeResponse()


class OpenAIFakeResponse(FakeResponse):
    finish_reason = None

    def json(self) -> dict[str, Any]:
        choice: dict[str, Any] = {"message": {"content": "豆包响应"}}
        if self.finish_reason is not None:
            choice["finish_reason"] = self.finish_reason
        return {"choices": [choice]}


class OpenAIFakeClient(FakeClient):
    async def post(
        self, url: str, *, json: dict[str, Any], headers: dict[str, str]
    ) -> OpenAIFakeResponse:
        self.capture.update({"url": url, "json": json, "headers": headers})
        return OpenAIFakeResponse()


def test_anthropic_provider_uses_messages_contract(monkeypatch) -> None:
    capture: dict[str, Any] = {}
    monkeypatch.setattr(
        "app.providers.httpx.AsyncClient",
        lambda **kwargs: FakeClient(capture, **kwargs),
    )
    provider = AnthropicProvider(anthropic_settings())
    prompt = PromptDefinition(
        id="test",
        version="v1",
        system="只输出中文。",
        user_template="处理以下内容：{source}",
    )

    result = asyncio.run(provider.generate(prompt, "原始文本"))

    assert result == "第一段\n第二段"
    assert capture["url"] == "http://gateway.local/model/api/v1/messages"
    assert capture["client_kwargs"]["trust_env"] is False
    assert capture["headers"]["x-api-key"] == "test-key"
    assert capture["headers"]["anthropic-version"] == "2023-06-01"
    assert "system" not in capture["json"]
    user_message = capture["json"]["messages"][0]["content"]
    assert "只输出中文。" in user_message
    assert "原始文本" in user_message


def test_build_provider_selects_anthropic() -> None:
    provider = build_provider(anthropic_settings())
    assert isinstance(provider, AnthropicProvider)
    assert provider.name == "anthropic"
    assert provider.model == "test-claude"


def test_anthropic_messages_url_accepts_api_and_api_v1_bases() -> None:
    assert (
        anthropic_messages_url("http://gateway.local/model/api")
        == "http://gateway.local/model/api/v1/messages"
    )
    assert (
        anthropic_messages_url("http://gateway.local/model/api/v1/")
        == "http://gateway.local/model/api/v1/messages"
    )


def test_doubao_openai_compatible_request_uses_confirmed_endpoint(
    monkeypatch,
) -> None:
    capture: dict[str, Any] = {}
    monkeypatch.setattr(
        "app.providers.httpx.AsyncClient",
        lambda **kwargs: OpenAIFakeClient(capture, **kwargs),
    )
    data_dir = Path("/tmp/ai-book-studio-doubao-test")
    provider = OpenAICompatibleProvider(
        Settings(
            data_dir=data_dir,
            database_path=data_dir / "studio.sqlite3",
            provider="openai-compatible",
            api_base=(
                "http://deepgate.ximalaya.local/"
                "doubao-seed-2.0-pro/api/v1"
            ),
            api_key="test-key",
            model="doubao-seed-2.0-pro",
        )
    )
    prompt = PromptDefinition(
        id="test",
        version="v1",
        system="只输出中文。",
        user_template="处理：{source}",
    )

    result = asyncio.run(provider.generate(prompt, "原始文本"))

    assert result == "豆包响应"
    assert capture["url"] == (
        "http://deepgate.ximalaya.local/"
        "doubao-seed-2.0-pro/api/v1/chat/completions"
    )
    assert capture["json"]["model"] == "doubao-seed-2.0-pro"
    assert capture["headers"]["Authorization"] == "Bearer test-key"


def test_structured_openai_request_uses_json_mode_and_output_limit(
    monkeypatch,
) -> None:
    capture: dict[str, Any] = {}
    monkeypatch.setattr(
        "app.providers.httpx.AsyncClient",
        lambda **kwargs: OpenAIFakeClient(capture, **kwargs),
    )
    base = anthropic_settings()
    settings = Settings(
        data_dir=base.data_dir,
        database_path=base.database_path,
        provider="openai-compatible",
        api_base=base.api_base,
        api_key=base.api_key,
        model=base.model,
    )
    provider = OpenAICompatibleProvider(settings)
    prompt = PromptDefinition(
        id="book_analysis",
        version="v1",
        system="输出 JSON。",
        user_template="{source}",
    )

    asyncio.run(provider.generate(prompt, "原文"))

    assert capture["client_kwargs"]["trust_env"] is False
    assert capture["json"]["temperature"] == 0.1
    assert capture["json"]["max_tokens"] == 16384
    assert capture["json"]["response_format"] == {"type": "json_object"}


def test_album_outline_and_mind_map_request_high_output_limit(
    monkeypatch,
) -> None:
    captures: list[dict[str, Any]] = []
    client_options: list[dict[str, Any]] = []

    class RecordingClient(OpenAIFakeClient):
        async def post(self, url, *, json, headers):
            captures.append(json.copy())
            return OpenAIFakeResponse()

    monkeypatch.setattr(
        "app.providers.httpx.AsyncClient",
        lambda **kwargs: (
            client_options.append(kwargs) or RecordingClient({}, **kwargs)
        ),
    )
    base = anthropic_settings()
    provider = OpenAICompatibleProvider(
        Settings(
            data_dir=base.data_dir,
            database_path=base.database_path,
            provider="openai-compatible",
            api_base=base.api_base,
            api_key=base.api_key,
            model=base.model,
        )
    )

    for prompt_id in ("mind_map", "album_outline"):
        asyncio.run(
            provider.generate(
                PromptDefinition(
                    id=prompt_id,
                    version="v1",
                    system="输出内容。",
                    user_template="{source}",
                ),
                "原文",
            )
        )

    assert [capture["max_tokens"] for capture in captures] == [16384, 32768]
    assert [options["timeout"] for options in client_options] == [600, 900]
    assert "response_format" not in captures[0]
    assert "response_format" not in captures[1]

    asyncio.run(
        provider.generate(
            PromptDefinition(
                id="album_outline_structure",
                version="v1",
                system="只做格式转换。",
                user_template="{source}",
            ),
            "Markdown 大纲",
        )
    )
    assert captures[2]["response_format"] == {"type": "json_object"}


def test_openai_length_finish_reason_raises_explicit_truncation(
    monkeypatch,
) -> None:
    class LengthResponse(OpenAIFakeResponse):
        finish_reason = "length"

    class LengthClient(OpenAIFakeClient):
        async def post(self, url, *, json, headers):
            return LengthResponse()

    monkeypatch.setattr(
        "app.providers.httpx.AsyncClient",
        lambda **kwargs: LengthClient({}, **kwargs),
    )
    base = anthropic_settings()
    settings = Settings(
        data_dir=base.data_dir,
        database_path=base.database_path,
        provider="openai-compatible",
        api_base=base.api_base,
        api_key=base.api_key,
        model=base.model,
    )
    provider = OpenAICompatibleProvider(settings)
    prompt = PromptDefinition(
        id="book_analysis",
        version="v1",
        system="输出 JSON。",
        user_template="{source}",
    )

    try:
        asyncio.run(provider.generate(prompt, "原文"))
        raise AssertionError("length 应被识别为输出截断")
    except ModelOutputTruncatedError as error:
        assert error.category == "output_truncated"
        assert error.diagnostics["finish_reason"] == "length"


def test_structured_openai_request_falls_back_when_json_mode_is_rejected(
    monkeypatch,
) -> None:
    class RejectedJsonModeResponse(FakeResponse):
        status_code = 400

        def json(self) -> dict[str, Any]:
            return {"error": {"message": "response_format json_object unsupported"}}

    class FallbackClient(OpenAIFakeClient):
        async def post(self, url, *, json, headers):
            self.capture.setdefault("payloads", []).append(dict(json))
            if len(self.capture["payloads"]) == 1:
                return RejectedJsonModeResponse()
            return OpenAIFakeResponse()

    capture: dict[str, Any] = {}
    monkeypatch.setattr(
        "app.providers.httpx.AsyncClient",
        lambda **kwargs: FallbackClient(capture, **kwargs),
    )
    base = anthropic_settings()
    provider = OpenAICompatibleProvider(
        Settings(
            data_dir=base.data_dir,
            database_path=base.database_path,
            provider="openai-compatible",
            api_base=base.api_base,
            api_key=base.api_key,
            model=base.model,
        )
    )
    prompt = PromptDefinition(
        id="book_analysis",
        version="v1",
        system="输出 JSON。",
        user_template="{source}",
    )

    result = asyncio.run(provider.generate(prompt, "原文"))

    assert result == "豆包响应"
    assert len(capture["payloads"]) == 2
    assert capture["payloads"][0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in capture["payloads"][1]
