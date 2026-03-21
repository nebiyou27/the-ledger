from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv()

from ledger.event_store import EventStore
from ledger.mcp_server import create_runtime
from src.integrity.audit_chain import run_integrity_check


def _default_db_url() -> str:
    return (
        os.environ.get("DATABASE_URL")
        or os.environ.get("TEST_DB_URL")
        or "postgresql://postgres:apex@localhost:5432/apex_ledger"
    )


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, str):
        return value
    return str(value)


def _payload_preview(payload: dict[str, Any]) -> str:
    interesting_keys = (
        "application_id",
        "applicant_id",
        "session_id",
        "reviewer_id",
        "final_decision",
        "recommendation",
        "overall_verdict",
        "risk_tier",
        "confidence",
        "override_reason",
    )
    preview = {key: payload[key] for key in interesting_keys if key in payload}
    if preview:
        return json.dumps(preview, sort_keys=True)
    return json.dumps(payload, sort_keys=True)[:240]


def _to_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


async def _collect_timeline(store: EventStore, application_id: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    async for event in store.load_all(from_position=0, application_id=application_id):
        payload = event.get("payload") or {}
        metadata = event.get("metadata") or {}
        events.append(
            {
                "timestamp": _iso(event.get("recorded_at")),
                "event_type": event.get("event_type"),
                "stream_id": event.get("stream_id"),
                "stream_position": event.get("stream_position"),
                "global_position": event.get("global_position"),
                "correlation_id": metadata.get("correlation_id"),
                "causation_id": metadata.get("causation_id"),
                "payload_preview": _payload_preview(payload),
            }
        )

    events.sort(key=lambda row: int(row["global_position"]))
    return events


def _agent_action_count(timeline: list[dict[str, Any]]) -> int:
    return sum(
        1
        for row in timeline
        if str(row["event_type"]) in {"AgentSessionStarted", "AgentOutputWritten", "AgentSessionCompleted"}
    )


def _compliance_event_count(timeline: list[dict[str, Any]]) -> int:
    return sum(1 for row in timeline if str(row["event_type"]).startswith("ComplianceCheck"))


def _human_review_request_count(timeline: list[dict[str, Any]]) -> int:
    return sum(1 for row in timeline if str(row["event_type"]) == "HumanReviewRequested")


def _human_review_completion_count(timeline: list[dict[str, Any]]) -> int:
    return sum(1 for row in timeline if str(row["event_type"]) == "HumanReviewCompleted")


def _reconstruct_compliance_state(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    state: dict[str, Any] | None = None
    for event in events:
        payload = event.get("payload") or {}
        etype = str(event.get("event_type"))
        application_id = str(payload.get("application_id") or "")
        if not application_id:
            continue

        if state is None:
            state = {
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
            }

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
            failed_rule = {
                "rule_id": payload.get("rule_id"),
                "rule_name": payload.get("rule_name"),
                "rule_version": payload.get("rule_version"),
                "failure_reason": payload.get("failure_reason"),
                "is_hard_block": bool(payload.get("is_hard_block", False)),
            }
            state["failed_rules"].append(failed_rule)
            if failed_rule["is_hard_block"]:
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

    return state


async def _load_compliance_fallback(
    store: EventStore,
    application_id: str,
    as_of: str | None = None,
) -> dict[str, Any] | None:
    compliance_events = await store.load_stream(f"compliance-{application_id}")
    if not compliance_events:
        return None
    if as_of is not None:
        target = _to_dt(as_of)
        compliance_events = [event for event in compliance_events if _to_dt(event.get("recorded_at")) <= target]
        if not compliance_events:
            return None
    return _reconstruct_compliance_state(compliance_events)


def _build_summary(
    application_id: str,
    timeline: list[dict[str, Any]],
    integrity: Any,
    compliance_source: str,
) -> dict[str, Any]:
    if not timeline:
        return {
            "application_id": application_id,
            "event_count": 0,
            "first_event_at": None,
            "last_event_at": None,
            "final_event_type": None,
            "agent_actions": 0,
            "compliance_checks": 0,
            "human_review_requests": 0,
            "human_review_completions": 0,
            "integrity": "valid" if integrity.chain_valid else "invalid",
            "compliance_source": compliance_source,
        }

    counts = Counter(str(row["event_type"]) for row in timeline)
    return {
        "application_id": application_id,
        "event_count": len(timeline),
        "first_event_at": timeline[0]["timestamp"],
        "last_event_at": timeline[-1]["timestamp"],
        "final_event_type": timeline[-1]["event_type"],
        "agent_actions": _agent_action_count(timeline),
        "compliance_checks": counts.get("ComplianceCheckCompleted", 0),
        "human_review_requests": _human_review_request_count(timeline),
        "human_review_completions": _human_review_completion_count(timeline),
        "integrity": "valid" if integrity.chain_valid else "invalid",
        "compliance_source": compliance_source,
    }


async def audit_application(
    application_id: str,
    db_url: str,
    as_of: str | None,
    json_output: bool,
    output_path: Path | None,
) -> int:
    store = EventStore(db_url)
    await store.connect()

    runtime = create_runtime(store)
    try:
        await runtime.sync_projections(max_rounds=32)

        timeline = await _collect_timeline(store, application_id)
        integrity = await run_integrity_check(store, entity_type="loan", entity_id=application_id)
        current_compliance = runtime.compliance_audit.get_current_compliance(application_id)
        compliance_as_of = runtime.compliance_audit.get_compliance_at(application_id, as_of) if as_of else None
        compliance_source = "projection"

        if current_compliance is None:
            current_compliance = await _load_compliance_fallback(store, application_id)
            if current_compliance is not None:
                compliance_source = "event-stream fallback"

        if as_of and compliance_as_of is None:
            compliance_as_of = await _load_compliance_fallback(store, application_id, as_of=as_of)
            if compliance_as_of is not None:
                compliance_source = f"{compliance_source} (as-of fallback)"

        report = {
            "summary": _build_summary(application_id, timeline, integrity, compliance_source),
            "timeline": timeline,
            "integrity": {
                "chain_valid": integrity.chain_valid,
                "tamper_detected": integrity.tamper_detected,
                "events_verified": integrity.events_verified,
                "integrity_hash": integrity.integrity_hash,
                "previous_hash": integrity.previous_hash,
                "audit_stream_version": integrity.audit_stream_version,
            },
            "application_id": application_id,
            "current_compliance": current_compliance,
            "compliance_as_of": compliance_as_of,
        }

        target_path = output_path or Path("artifacts") / f"audit-{application_id}.json"
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

        if json_output:
            print(json.dumps(report, indent=2, default=str))
            return 0

        summary = report["summary"]
        print("Executive summary")
        print("-----------------")
        print(f"Application: {application_id}")
        print(f"Events: {summary['event_count']}")
        print(f"Window: {summary['first_event_at']} -> {summary['last_event_at']}")
        print(f"Final event: {summary['final_event_type']}")
        print(f"AI agent actions: {summary['agent_actions']}")
        print(f"Compliance checks: {summary['compliance_checks']}")
        print(f"Human review requests: {summary['human_review_requests']}")
        print(f"Human review completions: {summary['human_review_completions']}")
        print(f"Integrity: {summary['integrity']}")
        print(f"Compliance source: {summary['compliance_source']}")
        print(f"Report written to: {target_path}")
        print()
        print("Timeline")
        print("--------")
        for row in timeline:
            print(
                f"{row['timestamp']} | {row['event_type']} | {row['stream_id']}@{row['stream_position']} "
                f"| corr={row['correlation_id']} caus={row['causation_id']}"
            )
            print(f"  {row['payload_preview']}")

        print()
        print("Current compliance")
        print("------------------")
        print(json.dumps(current_compliance, indent=2, default=str))

        if as_of:
            print()
            print(f"Compliance as of {as_of}")
            print("------------------------")
            print(json.dumps(compliance_as_of, indent=2, default=str))

        return 0
    finally:
        await store.close()
        await runtime.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Show the complete decision history for an application and verify its integrity."
    )
    parser.add_argument("--application-id", required=True, help="Application ID to audit, for example APEX-0007.")
    parser.add_argument(
        "--db-url",
        default=None,
        help="Database URL, defaults to DATABASE_URL / TEST_DB_URL / the local postgres default.",
    )
    parser.add_argument(
        "--as-of",
        default=None,
        help="Optional ISO-8601 timestamp for a point-in-time compliance snapshot.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write the JSON report to this path. Defaults to artifacts/audit-<application-id>.json.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of a text report.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    db_url = args.db_url or _default_db_url()
    output_path = Path(args.output) if args.output else None
    raise SystemExit(asyncio.run(audit_application(args.application_id, db_url, args.as_of, args.json, output_path)))


if __name__ == "__main__":
    main()
