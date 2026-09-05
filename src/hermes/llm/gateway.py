"""Model Gateway — multi-provider LLM abstraction with fallback chain.

Hard-coding a single provider is the most brittle part of an agent runtime.
The gateway lets Hermes fail over across providers transparently:

    Hermes → Model Gateway → OpenAI → Anthropic → Cloudflare → HF → response

Providers are built from env vars; the chain tries them in order and only
advances on error. Deterministic stub mode (no keys) keeps tests token-free.
"""
from __future__ import annotations

import os
from typing import Protocol


class LLMProvider(Protocol):
    @property
    def name(self) -> str: ...
    def complete(self, prompt: str) -> str: ...


class OpenAIProvider:
    name: str = "openai"

    def __init__(self, api_key: str, model: str = "gpt-4o-mini", timeout: int = 60):
        self.api_key, self.model, self.timeout = api_key, model, timeout

    def complete(self, prompt: str) -> str:
        import httpx
        r = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={"model": self.model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 1024},
            timeout=self.timeout)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


class AnthropicProvider:
    name: str = "anthropic"

    def __init__(self, api_key: str, model: str = "claude-3-5-haiku-20241022", timeout: int = 60):
        self.api_key, self.model, self.timeout = api_key, model, timeout

    def complete(self, prompt: str) -> str:
        import httpx
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
            json={"model": self.model, "max_tokens": 1024, "messages": [{"role": "user", "content": prompt}]},
            timeout=self.timeout)
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")


class HFProvider:
    name: str = "hf"

    def __init__(self, api_key: str, model: str = "meta-llama/Llama-3.1-8B-Instruct", timeout: int = 60):
        self.api_key, self.model, self.timeout = api_key, model, timeout

    def complete(self, prompt: str) -> str:
        import httpx
        r = httpx.post(
            f"https://api-inference.huggingface.co/models/{self.model}",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"inputs": prompt, "parameters": {"max_new_tokens": 512}}, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list) and data:
            return data[0].get("generated_text", str(data[0]))
        return str(data)


class CloudflareProvider:
    name: str = "cloudflare"

    def __init__(self, account_id: str, api_token: str,
                 model: str = "@cf/meta/llama-3.1-8b-instruct", timeout: int = 60):
        from .cloudflare import CloudflareLLM
        self._llm = CloudflareLLM(account_id, api_token, model, timeout=timeout)

    def complete(self, prompt: str) -> str:
        return self._llm.complete(prompt)


class ModelGateway:
    """Tries each provider in order; first success wins."""

    def __init__(self, providers: list[LLMProvider] | None = None, chain: list[str] | None = None):
        self.providers: dict[str, LLMProvider] = {p.name: p for p in (providers or [])}
        self.chain: list[str] = chain or list(self.providers.keys())

    def complete(self, prompt: str) -> str:
        if not self.providers:
            return "[gateway: no providers configured — stub mode]"
        last_err: Exception | None = None
        for name in self.chain:
            provider = self.providers.get(name)
            if provider is None:
                continue
            try:
                return provider.complete(prompt)
            except Exception as e:  # provider down / rate-limited → fall through
                last_err = e
                continue
        raise RuntimeError(f"All providers failed ({self.chain}); last error: {last_err}")

    @property
    def active(self) -> list[str]:
        return [n for n in self.chain if n in self.providers]

    @classmethod
    def from_env(cls) -> "ModelGateway":
        providers: list[LLMProvider] = []
        chain: list[str] = []

        oai = os.environ.get("OPENAI_API_KEY", "")
        if oai:
            providers.append(OpenAIProvider(oai, os.environ.get("OPENAI_MODEL", "gpt-4o-mini")))
            chain.append("openai")

        ant = os.environ.get("ANTHROPIC_API_KEY", "")
        if ant:
            providers.append(AnthropicProvider(ant, os.environ.get("ANTHROPIC_MODEL", "claude-3-5-haiku-20241022")))
            chain.append("anthropic")

        hf = os.environ.get("HF_API_KEY", "")
        if hf:
            providers.append(HFProvider(hf, os.environ.get("HF_MODEL", "meta-llama/Llama-3.1-8B-Instruct")))
            chain.append("hf")

        acct = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
        tok = os.environ.get("CLOUDFLARE_API_TOKEN", "")
        if acct and tok:
            providers.append(CloudflareProvider(acct, tok, os.environ.get("CLOUDFLARE_MODEL",
                                                                          "@cf/meta/llama-3.1-8b-instruct")))
            chain.append("cloudflare")

        return cls(providers, chain)