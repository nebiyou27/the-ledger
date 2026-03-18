from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ComplianceRecordAggregate:
    application_id: str
    version: int = -1
    session_id: str | None = None
    regulation_set_version: str | None = None
    required_rules: set[str] = field(default_factory=set)
    passed_rules: set[str] = field(default_factory=set)
    failed_rules: set[str] = field(default_factory=set)
    noted_rules: set[str] = field(default_factory=set)
    hard_block: bool = False
    verdict: str | None = None
    completed: bool = False
    events: list[dict] = field(default_factory=list)

    @classmethod
    async def load(cls, store, application_id: str) -> "ComplianceRecordAggregate":
        agg = cls(application_id=application_id)
        stream_events = await store.load_stream(f"compliance-{application_id}")
        for event in stream_events:
            agg.apply(event)
        return agg

    def apply(self, event: dict) -> None:
        event_type = event.get("event_type")
        payload = event.get("payload") or {}
        self.events.append(event)

        stream_position = event.get("stream_position")
        if isinstance(stream_position, int):
            self.version = stream_position
        else:
            self.version += 1

        if event_type == "ComplianceCheckInitiated":
            if self.session_id is not None:
                raise ValueError("Compliance check already initiated")
            self.session_id = payload.get("session_id")
            self.regulation_set_version = payload.get("regulation_set_version")
            self.required_rules = set(payload.get("rules_to_evaluate") or [])
            return

        self._require_initiated(event_type)

        if event_type == "ComplianceRulePassed":
            self._record_rule(payload.get("rule_id"), "passed")
            return

        if event_type == "ComplianceRuleFailed":
            rule_id = payload.get("rule_id")
            self._record_rule(rule_id, "failed")
            if bool(payload.get("is_hard_block")):
                self.hard_block = True
            return

        if event_type == "ComplianceRuleNoted":
            rule_id = payload.get("rule_id")
            self._record_rule(rule_id, "noted")
            return

        if event_type == "ComplianceCheckCompleted":
            self._finalize(payload)
            return

    def _require_initiated(self, event_type: str) -> None:
        if self.session_id is None:
            raise ValueError(f"{event_type} requires ComplianceCheckInitiated first")
        if self.completed:
            raise ValueError(f"Cannot append {event_type} after ComplianceCheckCompleted")

    def _record_rule(self, rule_id: str | None, bucket: str) -> None:
        if not rule_id:
            raise ValueError("Compliance rule events require rule_id")
        if rule_id in self.passed_rules or rule_id in self.failed_rules or rule_id in self.noted_rules:
            raise ValueError(f"Rule {rule_id} already evaluated")
        if bucket == "passed":
            self.passed_rules.add(rule_id)
        elif bucket == "failed":
            self.failed_rules.add(rule_id)
        else:
            self.noted_rules.add(rule_id)

    def _finalize(self, payload: dict) -> None:
        evaluated = self.passed_rules | self.failed_rules | self.noted_rules

        if not self.hard_block and self.required_rules and not self.required_rules.issubset(evaluated):
            missing = sorted(self.required_rules - evaluated)
            raise ValueError(f"Cannot complete compliance check, missing required rules: {missing}")

        if int(payload.get("rules_passed", -1)) != len(self.passed_rules):
            raise ValueError("rules_passed count mismatch")
        if int(payload.get("rules_failed", -1)) != len(self.failed_rules):
            raise ValueError("rules_failed count mismatch")
        if int(payload.get("rules_noted", -1)) != len(self.noted_rules):
            raise ValueError("rules_noted count mismatch")

        verdict = str(payload.get("overall_verdict") or "")
        if self.hard_block and verdict != "BLOCKED":
            raise ValueError("Hard block requires BLOCKED verdict")

        self.verdict = verdict
        self.completed = True
