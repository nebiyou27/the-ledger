from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from ledger.projections.base import Projection


def _to_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


class ComplianceAuditProjection(Projection):
    def __init__(self):
        super().__init__("compliance_audit")
        self._current: dict[str, dict[str, Any]] = {}
        self._history: dict[str, list[dict[str, Any]]] = {}

    def handles(self, event_type: str) -> bool:
        return event_type in {
            "ComplianceCheckInitiated",
            "ComplianceRulePassed",
            "ComplianceRuleFailed",
            "ComplianceRuleNoted",
            "ComplianceCheckCompleted",
        }

    async def process_event(self, event: dict[str, Any]) -> None:
        payload = event.get("payload") or {}
        application_id = str(payload.get("application_id", ""))
        if not application_id:
            return

        state = self._current.setdefault(
            application_id,
            {
                "application_id": application_id,
                "session_id": None,
                "regulation_set_version": None,
                "rules_to_evaluate": [],
                "passed_rules": [],
                "failed_rules": [],
                "noted_rules": [],
                "overall_verdict": None,
                "has_hard_block": False,
                "last_updated_at": None,
            },
        )

        etype = str(event.get("event_type"))
        if etype == "ComplianceCheckInitiated":
            state["session_id"] = payload.get("session_id")
            state["regulation_set_version"] = payload.get("regulation_set_version")
            state["rules_to_evaluate"] = list(payload.get("rules_to_evaluate") or [])
        elif etype == "ComplianceRulePassed":
            state["passed_rules"].append(
                {
                    "rule_id": payload.get("rule_id"),
                    "rule_name": payload.get("rule_name"),
                    "rule_version": payload.get("rule_version"),
                    "evidence_hash": payload.get("evidence_hash"),
                }
            )
        elif etype == "ComplianceRuleFailed":
            state["failed_rules"].append(
                {
                    "rule_id": payload.get("rule_id"),
                    "rule_name": payload.get("rule_name"),
                    "rule_version": payload.get("rule_version"),
                    "failure_reason": payload.get("failure_reason"),
                    "is_hard_block": bool(payload.get("is_hard_block", False)),
                }
            )
            if bool(payload.get("is_hard_block", False)):
                state["has_hard_block"] = True
        elif etype == "ComplianceRuleNoted":
            state["noted_rules"].append(
                {
                    "rule_id": payload.get("rule_id"),
                    "rule_name": payload.get("rule_name"),
                    "note_type": payload.get("note_type"),
                    "note_text": payload.get("note_text"),
                }
            )
        elif etype == "ComplianceCheckCompleted":
            state["overall_verdict"] = payload.get("overall_verdict")
            state["has_hard_block"] = bool(payload.get("has_hard_block", state["has_hard_block"]))

        state["last_updated_at"] = event.get("recorded_at")
        snapshot = deepcopy(state)
        snapshot["_at"] = event.get("recorded_at")
        snapshot["_global_position"] = int(event.get("global_position", -1))
        self._history.setdefault(application_id, []).append(snapshot)

    def get_current_compliance(self, application_id: str) -> dict[str, Any] | None:
        state = self._current.get(application_id)
        return deepcopy(state) if state else None

    def get_compliance_at(self, application_id: str, timestamp: datetime | str) -> dict[str, Any] | None:
        target = _to_utc(timestamp)
        snapshots = self._history.get(application_id, [])
        candidate = None
        for snap in snapshots:
            snap_at = _to_utc(snap.get("_at"))
            if snap_at <= target:
                candidate = snap
            else:
                break
        return deepcopy(candidate) if candidate else None

    async def rebuild_from_scratch(self, store) -> None:
        self._current = {}
        self._history = {}
        self._last_processed_position = -1
        self._latest_seen_position = -1
        self._last_processed_at = None
        self._latest_seen_at = None

        async for event in store.load_all(from_position=0):
            self.note_latest(event)
            if self.handles(str(event.get("event_type"))):
                await self.process_event(event)
            self.mark_progress(event)
