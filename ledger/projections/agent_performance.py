from __future__ import annotations

from copy import deepcopy
from typing import Any

from ledger.projections.base import Projection


class AgentPerformanceProjection(Projection):
    def __init__(self):
        super().__init__("agent_performance")
        self._session_meta: dict[str, dict[str, str]] = {}
        self._rows: dict[tuple[str, str], dict[str, Any]] = {}

    def handles(self, event_type: str) -> bool:
        return event_type in {
            "AgentSessionStarted",
            "CreditAnalysisCompleted",
            "DecisionGenerated",
            "HumanReviewCompleted",
        }

    async def process_event(self, event: dict[str, Any]) -> None:
        etype = str(event.get("event_type"))
        payload = event.get("payload") or {}

        if etype == "AgentSessionStarted":
            session_id = str(payload.get("session_id", ""))
            if session_id:
                self._session_meta[session_id] = {
                    "agent_id": str(payload.get("agent_id", "unknown-agent")),
                    "model_version": str(payload.get("model_version", "unknown-model")),
                }
            return

        if etype == "CreditAnalysisCompleted":
            session_id = str(payload.get("session_id", ""))
            meta = self._session_meta.get(session_id, {"agent_id": "unknown-agent", "model_version": "unknown-model"})
            row = self._ensure_row(meta["agent_id"], meta["model_version"], event.get("recorded_at"))
            row["analyses_completed"] += 1
            confidence = float((payload.get("decision") or {}).get("confidence", 0.0))
            duration = int(payload.get("analysis_duration_ms", 0))
            row["confidence_sum"] += confidence
            row["duration_sum"] += duration
            return

        if etype == "DecisionGenerated":
            session_id = str(payload.get("orchestrator_session_id", ""))
            meta = self._session_meta.get(session_id, {"agent_id": "unknown-agent", "model_version": "unknown-model"})
            row = self._ensure_row(meta["agent_id"], meta["model_version"], event.get("recorded_at"))
            row["decisions_generated"] += 1
            recommendation = str(payload.get("recommendation", "")).upper()
            if recommendation == "APPROVE":
                row["approve_count"] += 1
            elif recommendation == "DECLINE":
                row["decline_count"] += 1
            elif recommendation == "REFER":
                row["refer_count"] += 1
            confidence = float(payload.get("confidence", 0.0))
            row["confidence_sum"] += confidence
            return

        if etype == "HumanReviewCompleted":
            if bool(payload.get("override")):
                # Conservative attribution when override exists but no explicit model info.
                row = self._ensure_row("unknown-agent", "unknown-model", event.get("recorded_at"))
                row["human_override_count"] += 1

    def _ensure_row(self, agent_id: str, model_version: str, recorded_at: Any) -> dict[str, Any]:
        key = (agent_id, model_version)
        row = self._rows.get(key)
        if row is None:
            row = {
                "agent_id": agent_id,
                "model_version": model_version,
                "analyses_completed": 0,
                "decisions_generated": 0,
                "confidence_sum": 0.0,
                "duration_sum": 0,
                "approve_count": 0,
                "decline_count": 0,
                "refer_count": 0,
                "human_override_count": 0,
                "first_seen_at": recorded_at,
                "last_seen_at": recorded_at,
            }
            self._rows[key] = row
        else:
            row["last_seen_at"] = recorded_at
        return row

    def all_rows(self) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for row in self._rows.values():
            total_conf_count = row["analyses_completed"] + row["decisions_generated"]
            decision_total = max(1, row["decisions_generated"])
            output.append(
                {
                    "agent_id": row["agent_id"],
                    "model_version": row["model_version"],
                    "analyses_completed": row["analyses_completed"],
                    "decisions_generated": row["decisions_generated"],
                    "avg_confidence_score": row["confidence_sum"] / total_conf_count if total_conf_count else 0.0,
                    "avg_duration_ms": row["duration_sum"] / row["analyses_completed"] if row["analyses_completed"] else 0.0,
                    "approve_rate": row["approve_count"] / decision_total,
                    "decline_rate": row["decline_count"] / decision_total,
                    "refer_rate": row["refer_count"] / decision_total,
                    "human_override_rate": row["human_override_count"] / decision_total,
                    "first_seen_at": row["first_seen_at"],
                    "last_seen_at": row["last_seen_at"],
                }
            )
        return [deepcopy(r) for r in output]
