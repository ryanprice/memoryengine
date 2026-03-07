"""
LLM Provider Abstraction
Supports: Anthropic, OpenAI, Ollama, OpenAI-compatible endpoints
"""

import os
from abc import ABC, abstractmethod
from typing import Optional


class LLMProvider(ABC):
    @abstractmethod
    def complete(self, system: str, user: str, max_tokens: int = 2000) -> str:
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        pass


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: Optional[str] = None, model: str = "claude-opus-4-5-20251101"):
        try:
            import anthropic
        except ImportError:
            raise ImportError("Run: pip install anthropic")
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ValueError("ANTHROPIC_API_KEY not set")
        self.client = anthropic.Anthropic(api_key=key)
        self.model = model

    @property
    def name(self) -> str:
        return f"anthropic/{self.model}"

    def complete(self, system: str, user: str, max_tokens: int = 2000) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return response.content[0].text


class OpenAIProvider(LLMProvider):
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gpt-4o",
        base_url: Optional[str] = None,
    ):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("Run: pip install openai")
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ValueError("OPENAI_API_KEY not set")
        self.client = OpenAI(api_key=key, base_url=base_url)
        self.model = model
        self._base_url = base_url

    @property
    def name(self) -> str:
        prefix = "openai_compatible" if self._base_url else "openai"
        return f"{prefix}/{self.model}"

    def complete(self, system: str, user: str, max_tokens: int = 2000) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content


class OllamaProvider(LLMProvider):
    def __init__(
        self,
        model: str = "llama3.2",
        base_url: str = "http://localhost:11434",
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")

    @property
    def name(self) -> str:
        return f"ollama/{self.model}"

    def complete(self, system: str, user: str, max_tokens: int = 2000) -> str:
        try:
            import requests
        except ImportError:
            raise ImportError("Run: pip install requests")
        response = requests.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "stream": False,
                "options": {"num_predict": max_tokens},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=120,
        )
        response.raise_for_status()
        return response.json()["message"]["content"]


def get_provider(config: dict) -> LLMProvider:
    """Instantiate the correct provider from config dict."""
    provider_type = config.get("provider", "openai").lower()

    if provider_type == "anthropic":
        return AnthropicProvider(
            api_key=config.get("api_key"),
            model=config.get("model", "claude-opus-4-5-20251101"),
        )
    elif provider_type == "openai":
        return OpenAIProvider(
            api_key=config.get("api_key"),
            model=config.get("model", "gpt-4o"),
        )
    elif provider_type == "ollama":
        return OllamaProvider(
            model=config.get("model", "llama3.2"),
            base_url=config.get("base_url", "http://localhost:11434"),
        )
    elif provider_type == "openai_compatible":
        return OpenAIProvider(
            api_key=config.get("api_key"),
            model=config.get("model", "gpt-4o"),
            base_url=config.get("base_url"),
        )
    else:
        raise ValueError(
            f"Unknown provider '{provider_type}'. "
            "Valid: anthropic, openai, ollama, openai_compatible"
        )
