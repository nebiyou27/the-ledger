"""
ledger/agents/llm_adapter.py
=============================
LLM abstraction layer. Supports Ollama (local) and a deterministic mock for tests.
Replaces the hard Anthropic dependency in BaseApexAgent.
"""
from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from typing import Any

try:
    import httpx
except ModuleNotFoundError:  # pragma: no cover
    httpx = None


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


class OllamaClient(LLMClient):
    """Calls a local Ollama instance via its HTTP API."""

    def __init__(
        self,
        model: str = os.getenv("OLLAMA_MODEL", "llama3:8b"),
        base_url: str = "http://localhost:11434",
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")

    async def generate(
        self,
        system: str,
        user: str,
        max_tokens: int = 1024,
    ) -> tuple[str, int, int, float]:
        if httpx is None:
            raise RuntimeError("httpx is required for OllamaClient: pip install httpx")

        payload = {
            "model": self.model,
            "system": system,
            "prompt": user,
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "temperature": 0.3,
            },
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self.base_url}/api/generate",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        text = data.get("response", "")
        # DeepSeek-R1 wraps reasoning in <think>...</think> tags; strip them.
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

        tokens_in = data.get("prompt_eval_count", 0) or 0
        tokens_out = data.get("eval_count", 0) or 0
        # Local models have zero dollar cost.
        cost = 0.0
        return text, tokens_in, tokens_out, cost


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
