"""
ledger/agents/llm_adapter.py
=============================
LLM abstraction layer. Supports an OpenRouter-backed OpenAI SDK client and a deterministic
mock for tests.
"""
from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from typing import Any

try:
    from openai import AsyncOpenAI
except ModuleNotFoundError:  # pragma: no cover
    AsyncOpenAI = None


def _default_model() -> str:
    return os.getenv("OPENROUTER_MODEL", os.getenv("OLLAMA_MODEL", "google/gemini-2.5-flash"))


class LLMClient(ABC):
    """Protocol every LLM backend must implement."""

    @abstractmethod
    async def generate(
        self,
        system: str,
        user: str,
        max_tokens: int = 1024,
    ) -> tuple[str, int, int, float]:
        """Return (text, tokens_in, tokens_out, cost_usd)."""
        ...


class OpenRouterClient(LLMClient):
    """Calls OpenRouter via the OpenAI-compatible async SDK."""

    def __init__(
        self,
        model: str = _default_model(),
        base_url: str = "https://openrouter.ai/api/v1",
        api_key: str | None = None,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        self._client: AsyncOpenAI | None = None

    def _get_client(self) -> AsyncOpenAI:
        if AsyncOpenAI is None:
            raise RuntimeError("openai is required for OpenRouterClient: pip install openai")
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY is required for OpenRouterClient")
        if self._client is None:
            self._client = AsyncOpenAI(base_url=self.base_url, api_key=self.api_key)
        return self._client

    async def generate(
        self,
        system: str,
        user: str,
        max_tokens: int = 1024,
    ) -> tuple[str, int, int, float]:
        client = self._get_client()
        response = await client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=0.3,
        )

        content = ""
        if response.choices:
            message = response.choices[0].message
            content = getattr(message, "content", "") or ""
        text = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

        usage = getattr(response, "usage", None)
        tokens_in = getattr(usage, "prompt_tokens", 0) or 0
        tokens_out = getattr(usage, "completion_tokens", 0) or 0
        cost = 0.0
        return text, tokens_in, tokens_out, cost


# Backward-compatible alias used across the codebase.
OllamaClient = OpenRouterClient


class MockLLMClient(LLMClient):
    """Returns deterministic JSON for unit tests. No network calls."""

    def __init__(self, responses: dict[str, Any] | None = None):
        self._responses = responses or {}
        self._call_count = 0

    async def generate(
        self,
        system: str,
        user: str,
        max_tokens: int = 1024,
    ) -> tuple[str, int, int, float]:
        self._call_count += 1

        # Check if a custom response was registered for this system prompt keyword.
        for keyword, response in self._responses.items():
            if keyword.lower() in system.lower():
                text = json.dumps(response) if isinstance(response, dict) else str(response)
                return text, 100, 50, 0.0

        # Default fallback responses based on agent type detected in system prompt.
        if "credit analyst" in system.lower():
            return json.dumps({
                "risk_tier": "MEDIUM",
                "recommended_limit_usd": 350000,
                "confidence": 0.72,
                "rationale": "Mock credit analysis — adequate financials with moderate risk indicators.",
                "key_concerns": ["Limited operating history data"],
                "data_quality_caveats": [],
                "policy_overrides_applied": [],
            }), 200, 100, 0.0

        if "fraud" in system.lower():
            return json.dumps({
                "fraud_score": 0.12,
                "anomalies": [],
                "recommendation": "PROCEED",
                "summary": "No significant anomalies detected in mock screening.",
            }), 200, 80, 0.0

        if "quality analyst" in system.lower() or "document quality" in system.lower():
            return json.dumps({
                "overall_quality": "ACCEPTABLE",
                "consistency_score": 0.85,
                "critical_missing_fields": [],
                "warnings": [],
                "balance_sheet_balances": True,
            }), 150, 60, 0.0

        if "loan officer" in system.lower() or "orchestrat" in system.lower():
            return json.dumps({
                "recommendation": "APPROVE",
                "approved_amount_usd": 350000,
                "confidence": 0.75,
                "executive_summary": "Mock decision — moderate risk, adequate financials, clean compliance.",
                "key_risks": ["Limited history"],
                "conditions": [],
            }), 250, 120, 0.0

        # Generic fallback.
        return json.dumps({"result": "ok", "mock": True}), 50, 30, 0.0
