import asyncio
from pathlib import Path
from typing import Any

from app.config import Settings
from app.prompts import PromptDefinition
from app.providers import AnthropicProvider, anthropic_messages_url, build_provider


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
