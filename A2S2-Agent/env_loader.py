"""Minimal .env loader (mirrors the twin/OS pattern).

Parses a `.env` next to this file into os.environ without adding a hard
dependency on python-dotenv. Values already present in the real environment
win — a `.env` never clobbers an explicit export. Run this module directly to
sanity-check what it sees (values are masked):

    python env_loader.py
"""
from __future__ import annotations

import os
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent / ".env"


def load_env(path: Path = ENV_PATH, *, override: bool = False) -> list[str]:
    """Load KEY=VALUE lines from `path` into os.environ.

    Returns the list of keys declared in the file (whether or not they were
    applied). Missing file -> empty list, no error.
    """
    if not path.is_file():
        return []
    declared: list[str] = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if not key:
            continue
        declared.append(key)
        if override or key not in os.environ:
            os.environ[key] = val
    return declared


def _mask(val: str) -> str:
    if len(val) <= 10:
        return "*" * len(val)
    return f"{val[:8]}...{val[-4:]}  (len={len(val)})"


if __name__ == "__main__":
    keys = load_env()
    if not keys:
        print(f"[env_loader] No .env found at {ENV_PATH}.")
    else:
        print(f"[env_loader] {len(keys)} variable(s) declared in .env:")
        for k in keys:
            print(f"  {k} = {_mask(os.environ.get(k, ''))}")
