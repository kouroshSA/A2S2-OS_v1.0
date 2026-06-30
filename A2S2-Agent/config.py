"""A2S2-Agent configuration.

All knobs are environment-driven so the agent behaves the same whether it is
run from a CLI, embedded in the OS as an app, or smoke-tested. Defaults are
chosen so that `python cli.py` works out of the box against a local A2S2-OS
with a Claude key in `.env` (default model: Claude Haiku).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from env_loader import load_env

# Load .env once on import (does not override real exports).
load_env()

# Default model id per provider. Anthropic default is Claude Haiku 4.5.
_DEFAULT_MODEL = {
    "anthropic": "claude-haiku-4-5-20251001",
    "ollama": "llama3.2",
    "openai": "gpt-4o-mini",
}


@dataclass
class Config:
    # --- LLM provider ---
    provider: str = field(
        default_factory=lambda: os.environ.get("A2S2_AGENT_PROVIDER", "anthropic").lower()
    )
    model: str = field(default_factory=lambda: os.environ.get("A2S2_AGENT_MODEL", ""))
    temperature: float = field(
        default_factory=lambda: float(os.environ.get("A2S2_AGENT_TEMPERATURE", "0"))
    )

    # --- endpoints ---
    os_url: str = field(
        default_factory=lambda: os.environ.get("A2S2_OS_URL", "http://localhost:8770").rstrip("/")
    )
    ollama_base_url: str = field(
        default_factory=lambda: os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    )
    openai_base_url: str = field(
        default_factory=lambda: os.environ.get("OPENAI_BASE_URL", "")
    )

    # --- behavior ---
    # When True, run-style tools require explicit confirmation before they
    # dispatch anything to the OS (safe default for a kiosk driving hardware).
    confirm_runs: bool = field(
        default_factory=lambda: os.environ.get("A2S2_AGENT_CONFIRM", "1") not in ("0", "false", "no")
    )
    request_timeout_s: float = field(
        default_factory=lambda: float(os.environ.get("A2S2_AGENT_HTTP_TIMEOUT", "15"))
    )

    def __post_init__(self):
        if not self.model:
            self.model = _DEFAULT_MODEL.get(self.provider, _DEFAULT_MODEL["anthropic"])

    def describe(self) -> str:
        return f"provider={self.provider} model={self.model} os_url={self.os_url}"


CONFIG = Config()
