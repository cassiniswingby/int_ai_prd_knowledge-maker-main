"""OpenAI / Azure OpenAI / Anthropic / AWS Bedrock client factory.

This module provides a unified interface for creating AI clients.
It automatically detects which API to use based on environment variables (priority order):
1. If OPENAI_API_KEY is set, use OpenAI
2. If AWS_BEARER_TOKEN_BEDROCK is set, use AWS Bedrock (Claude via Bearer Token)
3. If ANTHROPIC_API_KEY is set, use Anthropic (Claude) via OpenAI-compatible endpoint
4. If AZURE_OPENAI_API_KEY is set, use Azure OpenAI

Environment variables for AWS Bedrock:
- AWS_BEARER_TOKEN_BEDROCK: AWS Bearer Token（共通フォールバック）
- AWS_BEARER_TOKEN_BEDROCK_CHAT: Chat用Bearer Token（省略時はAWS_BEARER_TOKEN_BEDROCKを使用）
- AWS_BEARER_TOKEN_BEDROCK_VISION: Vision用Bearer Token（省略時はAWS_BEARER_TOKEN_BEDROCKを使用）
- AWS_DEFAULT_REGION: AWS region (default: ap-northeast-1)
- AWS_DEFAULT_REGION_CHAT: Chat用リージョン（省略時はAWS_DEFAULT_REGIONを使用）
- AWS_DEFAULT_REGION_VISION: Vision用リージョン（省略時はAWS_DEFAULT_REGIONを使用）
- BEDROCK_ENHANCE_MODEL: Chat model ID (default: jp.anthropic.claude-sonnet-4-6)
- BEDROCK_VISION_MODEL: Vision model ID (default: jp.anthropic.claude-sonnet-4-6)

Environment variables for Anthropic (Claude):
- ANTHROPIC_API_KEY: Anthropic API key
- ANTHROPIC_ENHANCE_MODEL: Chat model name (default: claude-sonnet-4-6)
- ANTHROPIC_VISION_MODEL: Vision model name (default: claude-sonnet-4-6)

Environment variables for Azure OpenAI:

共通設定（用途別設定がない場合のフォールバック）:
- AZURE_OPENAI_ENDPOINT: Azure OpenAI resource endpoint URL
- AZURE_OPENAI_API_KEY: Azure OpenAI API key
- AZURE_OPENAI_API_VERSION: API version (default: 2024-10-21)

Vision（AI-OCR）用設定:
- AZURE_OPENAI_ENDPOINT_VISION: Vision用エンドポイント（省略時はAZURE_OPENAI_ENDPOINTを使用）
- AZURE_OPENAI_API_KEY_VISION: Vision用APIキー（省略時はAZURE_OPENAI_API_KEYを使用）
- AZURE_OPENAI_API_VERSION_VISION: Vision用APIバージョン
- AZURE_OPENAI_DEPLOYMENT_VISION: Vision用デプロイメント名 (e.g., gpt5-mini)

Chat（きれい化・統合）用設定:
- AZURE_OPENAI_ENDPOINT_CHAT: Chat用エンドポイント（省略時はAZURE_OPENAI_ENDPOINTを使用）
- AZURE_OPENAI_API_KEY_CHAT: Chat用APIキー（省略時はAZURE_OPENAI_API_KEYを使用）
- AZURE_OPENAI_API_VERSION_CHAT: Chat用APIバージョン
- AZURE_OPENAI_DEPLOYMENT_CHAT: Chat用デプロイメント名 (e.g., gpt5.1)

プロキシ設定:
- HTTPS_PROXY: HTTPSプロキシURL (e.g., http://proxy.example.com:8080)
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Anthropic (OpenAI-compatible endpoint) wrapper
# ---------------------------------------------------------------------------

class _AnthropicCompletions:
    """max_completion_tokens → max_tokens を変換して Anthropic 互換エンドポイントに委譲するラッパー."""

    def __init__(self, real_completions: Any) -> None:
        self._real = real_completions

    def create(self, **kwargs: Any) -> Any:
        if "max_completion_tokens" in kwargs:
            kwargs["max_tokens"] = kwargs.pop("max_completion_tokens")
        return self._real.create(**kwargs)


class _AnthropicChat:
    def __init__(self, real_chat: Any) -> None:
        self.completions = _AnthropicCompletions(real_chat.completions)


class _AnthropicCompatClient:
    """openai.OpenAI を Anthropic 互換エンドポイント向けにラップするクライアント."""

    def __init__(self, real_client: Any) -> None:
        self.chat = _AnthropicChat(real_client.chat)


# ---------------------------------------------------------------------------
# AWS Bedrock (Converse API) wrapper
# ---------------------------------------------------------------------------

class _BedrockCompatMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _BedrockCompatChoice:
    def __init__(self, content: str) -> None:
        self.message = _BedrockCompatMessage(content)


class _BedrockCompatResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_BedrockCompatChoice(content)]


class _BedrockCompletions:
    """AWS Bedrock Converse API を OpenAI chat.completions.create() インターフェースでラップ."""

    def __init__(self, bearer_token: str, region: str, timeout: float) -> None:
        self._token = bearer_token
        self._region = region
        self._timeout = timeout

    def create(
        self,
        model: str,
        messages: List[Any],
        max_completion_tokens: Optional[int] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        response_format: Optional[Any] = None,
        **kwargs: Any,
    ) -> _BedrockCompatResponse:
        try:
            import requests as req_lib
        except ImportError:
            raise RuntimeError("requests package is required for Bedrock. Run: pip install requests")

        # システムメッセージを分離し、残りを Bedrock 形式に変換
        system_parts = []
        bedrock_messages = []
        for msg in messages:
            role = msg["role"]
            content = msg["content"]

            if role == "system":
                text = content if isinstance(content, str) else " ".join(
                    p["text"] for p in content if p.get("type") == "text"
                )
                system_parts.append({"text": text})
                continue

            if isinstance(content, str):
                bedrock_content = [{"text": content}]
            else:
                bedrock_content = []
                for part in content:
                    if part.get("type") == "text":
                        bedrock_content.append({"text": part["text"]})
                    elif part.get("type") == "image_url":
                        url = part["image_url"]["url"]
                        m = re.match(r"data:image/(\w+);base64,(.+)", url, re.DOTALL)
                        if m:
                            fmt = m.group(1).lower()
                            if fmt == "jpg":
                                fmt = "jpeg"
                            bedrock_content.append({
                                "image": {
                                    "format": fmt,
                                    "source": {"bytes": m.group(2)},
                                }
                            })

            bedrock_messages.append({"role": role, "content": bedrock_content})

        # response_format={"type": "json_object"} の場合、JSON 出力をプロンプトで指示する
        # OpenAI/Azure における response_format={"type":"json_object"} の代わり
        if isinstance(response_format, dict) and response_format.get("type") == "json_object":
            json_instruction = {"text": "必ずJSON形式のみで回答してください。前後の説明文やコードブロック記法は不要です。"}
            if system_parts:
                system_parts.append(json_instruction)
            else:
                system_parts = [json_instruction]

        body: dict = {"messages": bedrock_messages}
        if system_parts:
            body["system"] = system_parts

        inference_config: dict = {}
        tokens = max_completion_tokens or max_tokens
        if tokens:
            inference_config["maxTokens"] = tokens
        if temperature is not None:
            inference_config["temperature"] = temperature
        if inference_config:
            body["inferenceConfig"] = inference_config

        url = f"https://bedrock-runtime.{self._region}.amazonaws.com/model/{model}/converse"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._token}",
        }

        resp = req_lib.post(url, headers=headers, json=body, timeout=self._timeout)
        resp.raise_for_status()

        data = resp.json()
        text = data["output"]["message"]["content"][0]["text"]
        return _BedrockCompatResponse(text)


class _BedrockChat:
    def __init__(self, bearer_token: str, region: str, timeout: float) -> None:
        self.completions = _BedrockCompletions(bearer_token, region, timeout)


class _BedrockCompatClient:
    """AWS Bedrock Converse API を OpenAI 互換インターフェースでラップするクライアント."""

    def __init__(self, bearer_token: str, region: str, timeout: float) -> None:
        self.chat = _BedrockChat(bearer_token, region, timeout)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _get_http_client(timeout: float) -> Optional[Any]:
    """
    Get httpx client with proxy settings if HTTPS_PROXY is configured.

    Args:
        timeout: Request timeout in seconds

    Returns:
        httpx.Client with proxy configured, or None if no proxy
    """
    proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if not proxy_url:
        return None

    try:
        import httpx
        logger.info(f"Using proxy: {proxy_url}")
        return httpx.Client(proxy=proxy_url, timeout=timeout)
    except ImportError:
        logger.warning("httpx not installed, proxy settings ignored. Run: pip install httpx")
        return None


def load_env() -> None:
    """Load .env file if exists."""
    env_path = Path(".env")
    if env_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path)
        except ImportError:
            # dotenvがなければ手動で読み込み
            with open(env_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        key = key.strip()
                        value = value.strip()
                        # クォートを除去
                        if value and value[0] in ('"', "'") and value[-1] == value[0]:
                            value = value[1:-1]
                        os.environ[key] = value


def get_openai_client(timeout: float = 1500.0, purpose: str = "chat") -> Tuple[Any, bool]:
    """
    Get AI client (OpenAI / Bedrock / Anthropic / Azure OpenAI).

    Automatically detects which API to use based on environment variables.

    Args:
        timeout: Request timeout in seconds (default: 1500 = 25 minutes)
        purpose: "chat" for text generation, "vision" for image analysis

    Returns:
        Tuple of (client, is_azure)
        - client: client instance with chat.completions.create() interface
        - is_azure: True if using Azure OpenAI, False otherwise

    Raises:
        RuntimeError: If no API key is found
    """
    load_env()

    # 1. OpenAI
    if os.environ.get("OPENAI_API_KEY"):
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError("openai package is required. Run: pip install openai")

        http_client = _get_http_client(timeout)
        client = OpenAI(timeout=timeout, http_client=http_client)
        logger.info("Using OpenAI API")
        return client, False

    # 2. AWS Bedrock (Bearer Token)
    elif os.environ.get("AWS_BEARER_TOKEN_BEDROCK") or os.environ.get("AWS_BEARER_TOKEN_BEDROCK_VISION") or os.environ.get("AWS_BEARER_TOKEN_BEDROCK_CHAT"):
        token_key = "AWS_BEARER_TOKEN_BEDROCK_VISION" if purpose == "vision" else "AWS_BEARER_TOKEN_BEDROCK_CHAT"
        bearer_token = (
            os.environ.get(token_key) or
            os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
        )
        if not bearer_token:
            raise RuntimeError(
                f"{token_key} or AWS_BEARER_TOKEN_BEDROCK is required for Bedrock."
            )
        region_key = "AWS_DEFAULT_REGION_VISION" if purpose == "vision" else "AWS_DEFAULT_REGION_CHAT"
        region = (
            os.environ.get(region_key) or
            os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-1")
        )
        logger.info(f"Using AWS Bedrock API ({purpose}, region: {region})")
        return _BedrockCompatClient(bearer_token, region, timeout), False

    # 3. Anthropic (OpenAI-compatible endpoint)
    elif os.environ.get("ANTHROPIC_API_KEY"):
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError("openai package is required. Run: pip install openai")

        http_client = _get_http_client(timeout)
        real_client = OpenAI(
            api_key=os.environ["ANTHROPIC_API_KEY"],
            base_url="https://api.anthropic.com/v1/",
            default_headers={"anthropic-version": "2023-06-01"},
            timeout=timeout,
            http_client=http_client,
        )
        logger.info("Using Anthropic API (OpenAI-compatible endpoint)")
        return _AnthropicCompatClient(real_client), False

    # 4. Azure OpenAI
    elif os.environ.get("AZURE_OPENAI_API_KEY") or os.environ.get("AZURE_OPENAI_API_KEY_VISION") or os.environ.get("AZURE_OPENAI_API_KEY_CHAT"):
        try:
            from openai import AzureOpenAI
        except ImportError:
            raise RuntimeError("openai package is required. Run: pip install openai")

        suffix = "_VISION" if purpose == "vision" else "_CHAT"

        endpoint = (
            os.environ.get(f"AZURE_OPENAI_ENDPOINT{suffix}") or
            os.environ.get("AZURE_OPENAI_ENDPOINT")
        )
        api_key = (
            os.environ.get(f"AZURE_OPENAI_API_KEY{suffix}") or
            os.environ.get("AZURE_OPENAI_API_KEY")
        )
        api_version = (
            os.environ.get(f"AZURE_OPENAI_API_VERSION{suffix}") or
            os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")
        )

        if not endpoint:
            raise RuntimeError(
                f"AZURE_OPENAI_ENDPOINT{suffix} or AZURE_OPENAI_ENDPOINT is required. "
                "Set it to your Azure OpenAI resource endpoint URL."
            )
        if not api_key:
            raise RuntimeError(
                f"AZURE_OPENAI_API_KEY{suffix} or AZURE_OPENAI_API_KEY is required."
            )

        http_client = _get_http_client(timeout)
        client = AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
            timeout=timeout,
            http_client=http_client,
        )
        logger.info(f"Using Azure OpenAI API ({purpose}) - endpoint: {endpoint[:50]}...")
        return client, True

    else:
        raise RuntimeError(
            "No API key found. Set one of:\n"
            "  - OPENAI_API_KEY for OpenAI, or\n"
            "  - AWS_BEARER_TOKEN_BEDROCK for AWS Bedrock, or\n"
            "  - ANTHROPIC_API_KEY for Anthropic (Claude), or\n"
            "  - AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT for Azure OpenAI"
        )


def get_model_name(purpose: str = "chat", is_azure: bool = False) -> str:
    """
    Get model or deployment name based on the API being used.

    Args:
        purpose: "chat" for text generation, "vision" for image analysis
        is_azure: Whether using Azure OpenAI (determines which env vars to check)

    Returns:
        Model name (OpenAI/Anthropic) or deployment/model ID (Azure/Bedrock)
    """
    if os.environ.get("OPENAI_API_KEY"):
        if purpose == "vision":
            return os.environ.get("OPENAI_VISION_MODEL", "gpt-5-mini")
        else:
            return os.environ.get("OPENAI_ENHANCE_MODEL", "gpt-5.1")
    elif (
        os.environ.get("AWS_BEARER_TOKEN_BEDROCK") or
        os.environ.get("AWS_BEARER_TOKEN_BEDROCK_CHAT") or
        os.environ.get("AWS_BEARER_TOKEN_BEDROCK_VISION")
    ):
        if purpose == "vision":
            return os.environ.get("BEDROCK_VISION_MODEL", "jp.anthropic.claude-sonnet-4-6")
        else:
            return os.environ.get("BEDROCK_ENHANCE_MODEL", "jp.anthropic.claude-sonnet-4-6")
    elif os.environ.get("ANTHROPIC_API_KEY"):
        if purpose == "vision":
            return os.environ.get("ANTHROPIC_VISION_MODEL", "claude-sonnet-4-6")
        else:
            return os.environ.get("ANTHROPIC_ENHANCE_MODEL", "claude-sonnet-4-6")
    elif is_azure:
        if purpose == "vision":
            return os.environ.get("AZURE_OPENAI_DEPLOYMENT_VISION", "gpt5-mini")
        else:
            return os.environ.get("AZURE_OPENAI_DEPLOYMENT_CHAT", "gpt5.1")
    else:
        if purpose == "vision":
            return os.environ.get("OPENAI_VISION_MODEL", "gpt-5-mini")
        else:
            return os.environ.get("OPENAI_ENHANCE_MODEL", "gpt-5.1")
