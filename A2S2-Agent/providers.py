"""Provider-agnostic chat-model factory.

The whole point of A2S2-Agent's model layer: one function that returns a
LangChain chat model for Claude (cloud) **or** a local/non-local model reachable
through an OpenAI-compatible or Ollama endpoint — selected purely by config, so
the agent loop never knows or cares which is behind it. Default: Claude Haiku.

Supported providers (`A2S2_AGENT_PROVIDER`):
  - "anthropic"  -> ChatAnthropic   (needs ANTHROPIC_API_KEY)
  - "ollama"     -> ChatOllama      (local; OLLAMA_BASE_URL, default :11434)
  - "openai"     -> ChatOpenAI      (any OpenAI-compatible endpoint: vLLM,
                                      LM Studio, llama.cpp server, or OpenAI;
                                      OPENAI_BASE_URL + OPENAI_API_KEY)
"""
from __future__ import annotations

import os

from config import CONFIG, Config


class ProviderError(RuntimeError):
    pass


def make_model(cfg: Config = CONFIG):
    """Return a LangChain chat model instance for the configured provider."""
    provider = cfg.provider

    if provider == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise ProviderError(
                "ANTHROPIC_API_KEY is not set. Put it in A2S2-Agent/.env or export it."
            )
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=cfg.model, temperature=cfg.temperature)

    if provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(
            model=cfg.model,
            base_url=cfg.ollama_base_url,
            temperature=cfg.temperature,
        )

    if provider == "openai":
        from langchain_openai import ChatOpenAI
        # base_url empty -> the real OpenAI API; set it for local servers.
        kwargs = {"model": cfg.model, "temperature": cfg.temperature}
        if cfg.openai_base_url:
            kwargs["base_url"] = cfg.openai_base_url
        # Local OpenAI-compatible servers often ignore the key but the client
        # still requires one to be present.
        if not os.environ.get("OPENAI_API_KEY"):
            os.environ["OPENAI_API_KEY"] = "not-needed-for-local"
        return ChatOpenAI(**kwargs)

    raise ProviderError(
        f"Unknown provider {provider!r}. Use one of: anthropic, ollama, openai."
    )
