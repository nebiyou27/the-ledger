from __future__ import annotations

import json
import os
from typing import Any

import requests


OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3:8b")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "5"))


class OllamaClient:
    """Thin synchronous HTTP client for local Ollama chat calls."""

    def __init__(
        self,
        base_url: str = OLLAMA_BASE_URL,
        model: str = OLLAMA_MODEL,
        timeout: int = OLLAMA_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def ask(self, system_prompt: str, user_message: str) -> dict[str, Any]:
        """Call Ollama chat and return structured output when possible."""
        try:
            response = requests.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            content = response.json()["message"]["content"]
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                return {"raw": content, "parse_error": True}
            return parsed
        except requests.exceptions.ConnectionError:
            return {"error": "ollama_unavailable", "fallback": True}
        except requests.exceptions.Timeout:
            return {"error": "ollama_timeout", "fallback": True}
        except Exception as exc:
            return {"error": str(exc), "fallback": True}

    def is_available(self) -> bool:
        """Return True when Ollama responds to /api/tags within 2 seconds."""
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=2)
            response.raise_for_status()
            return True
        except Exception:
            return False
