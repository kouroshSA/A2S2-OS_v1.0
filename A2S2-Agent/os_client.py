"""Thin HTTP client for the A2S2-OS bridge API.

This is the agent's *live* link to the running OS — the same endpoints the shell
(index.html) uses. Read endpoints (apps/modes/sessions/health/job) are safe to
call anytime; `run` dispatches a job and is gated behind confirmation at the
tool layer. Nothing here knows about LLMs — it's a plain API wrapper so it can
be tested on its own and reused by an MCP server later.
"""
from __future__ import annotations

from typing import Any

import requests

from config import CONFIG, Config


class OSClient:
    def __init__(self, cfg: Config = CONFIG):
        self.base = cfg.os_url
        self.timeout = cfg.request_timeout_s

    # ---- low-level ----
    def _get(self, path: str) -> Any:
        r = requests.get(f"{self.base}{path}", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict) -> Any:
        r = requests.post(f"{self.base}{path}", json=body, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    # ---- read ----
    def health(self) -> dict:
        return self._get("/api/health")

    def apps(self) -> list[dict]:
        return self._get("/api/apps")

    def modes(self) -> list[dict]:
        return self._get("/api/modes")

    def library(self) -> list[dict]:
        return self._get("/api/library")

    def sessions(self) -> list[dict]:
        return self._get("/api/sessions")

    def job(self, job_id: str) -> dict:
        return self._get(f"/api/job/{job_id}")

    # ---- write ----
    def set_active_app(self, app_id: str) -> dict:
        return self._post("/api/active", {"app": app_id})

    def run(self, mode: str, task: str = "", params: dict | None = None,
            app: str | None = None) -> dict:
        body = {"mode": mode, "task": task, "params": params or {}}
        if app:
            body["app"] = app   # target this app without flipping the active one
        return self._post("/api/run", body)

    def stop(self, job_id: str) -> dict:
        return self._post(f"/api/job/{job_id}/stop", {})

    def reachable(self) -> bool:
        try:
            self.health()
            return True
        except Exception:
            return False
