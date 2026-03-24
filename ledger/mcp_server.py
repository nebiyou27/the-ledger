from __future__ import annotations

import argparse
import asyncio
import json
import os
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastmcp import FastMCP

from ledger.exceptions import OptimisticConcurrencyError
from ledger.integrity import run_integrity_check
from ledger.event_store import EventStore, InMemoryEventStore
from ledger.metrics import build_event_throughput_snapshot, build_manual_review_backlog_snapshot
from ledger.projections import (
    AgentSessionFailureProjection,
    AgentPerformanceProjection,
    ApplicationSummaryProjection,
    ComplianceAuditProjection,
    ProjectionDaemon,
)
from ledger.projections.manual_reviews import ManualReviewsProjection
from src.models.events import DomainError
from src.commands.handlers import (
    handle_compliance_check,
    handle_credit_analysis_completed,
    handle_fraud_screening_completed,
    handle_generate_decision,
    handle_human_review_completed,
    handle_start_agent_session,
    handle_submit_application,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(mode="json"))
    if hasattr(value, "value") and not isinstance(value, (str, bytes)):
        return getattr(value, "value")
    return value


def _structured_ok(tool: str, **payload: Any) -> dict[str, Any]:
    return {"ok": True, "tool": tool, **{key: _json_safe(value) for key, value in payload.items()}}


def _suggested_action_for(exc: Exception) -> str:
    if isinstance(exc, OptimisticConcurrencyError):
        return "reload_stream_and_retry"
    if isinstance(exc, DomainError):
        return "fix_command_and_retry"
    return "inspect_error_and_retry"


def _structured_error(tool: str, exc: Exception) -> dict[str, Any]:
    error_type = exc.__class__.__name__
    error: dict[str, Any] = {
        "type": error_type,
        "error_type": error_type,
        "message": str(exc),
        "suggested_action": _suggested_action_for(exc),
    }
    if isinstance(exc, OptimisticConcurrencyError):
        error.update(
            {
                "stream_id": exc.stream_id,
                "expected_version": exc.expected,
                "actual_version": exc.actual,
                "suggested_action": "reload_stream_and_retry",
            }
        )
    return {
        "ok": False,
        "tool": tool,
        "error_type": error_type,
        "message": str(exc),
        "suggested_action": error["suggested_action"],
        **{key: error[key] for key in ("stream_id", "expected_version", "actual_version") if key in error},
        "error": error,
    }


def _json_text(value: Any) -> str:
    return json.dumps(_json_safe(value), default=str)


@dataclass
class MCPRuntime:
    store: Any
    application_summary: ApplicationSummaryProjection
    agent_session_failures: AgentSessionFailureProjection
    agent_performance: AgentPerformanceProjection
    compliance_audit: ComplianceAuditProjection
    manual_reviews: ManualReviewsProjection
    daemon: ProjectionDaemon
    owns_store: bool = False

    async def start(self) -> None:
        if isinstance(self.store, EventStore):
            await self.store.connect()
            await self.store.initialize_schema()

    async def close(self) -> None:
        if self.owns_store and hasattr(self.store, "close"):
            await self.store.close()

    async def sync_projections(self, max_rounds: int = 32) -> dict[str, Any]:
        processed = 0
        rounds = 0
        while rounds < max_rounds:
            rounds += 1
            batch = await self.daemon._process_batch()
            processed += batch
            if batch == 0:
                break
        return {
            "processed_events": processed,
            "rounds": rounds,
            "lag": self.get_lag_snapshot(),
            "errors": self.daemon.get_error_counts(),
            "dead_letters": self.daemon.get_dead_letter_counts(),
        }

    def get_lag_snapshot(self) -> dict[str, Any]:
        return {
            name: {
                "positions_behind": lag.positions_behind,
                "millis": lag.millis,
            }
            for name, lag in self.daemon.get_all_lags().items()
        }

    async def _execute_command(self, tool_name: str, handler, command: dict[str, Any]) -> dict[str, Any]:
        try:
            positions = await handler(self.store, command)
            sync_report = await self.sync_projections()
            return _structured_ok(
                tool_name,
                application_id=command.get("application_id"),
                written_positions=positions,
                sync=sync_report,
            )
        except Exception as exc:  # pragma: no cover - exercised through tool wrappers
            return _structured_error(tool_name, exc)


def create_runtime(store: Any | None = None) -> MCPRuntime:
    owns_store = False
    if store is None:
        db_url = os.environ.get("DATABASE_URL")
        if db_url:
            store = EventStore(db_url)
            owns_store = True
        else:
            store = InMemoryEventStore()
    application_summary = ApplicationSummaryProjection()
    agent_session_failures = AgentSessionFailureProjection()
    agent_performance = AgentPerformanceProjection()
    compliance_audit = ComplianceAuditProjection()
    manual_reviews = ManualReviewsProjection()
    daemon = ProjectionDaemon(
        store,
        [application_summary, agent_session_failures, agent_performance, compliance_audit, manual_reviews],
    )
    return MCPRuntime(
        store=store,
        application_summary=application_summary,
        agent_session_failures=agent_session_failures,
        agent_performance=agent_performance,
        compliance_audit=compliance_audit,
        manual_reviews=manual_reviews,
        daemon=daemon,
        owns_store=owns_store,
    )


def create_server(runtime: MCPRuntime | None = None) -> FastMCP:
    runtime = runtime or create_runtime()
    server = FastMCP(
        "the-ledger",
        instructions=(
            "MCP surface for The Ledger. "
            "Use command tools to write lifecycle events and resources to read the projections."
        ),
    )

    @server.tool(name="submit_application", description="Append an application submission and document request.")
    async def submit_application(
        application_id: str,
        applicant_id: str,
        requested_amount_usd: float,
        loan_purpose: str = "working_capital",
        loan_term_months: int = 36,
        submission_channel: str = "web",
        contact_email: str = "unknown@example.com",
        contact_name: str = "Unknown",
        submitted_at: str | None = None,
        deadline: str | None = None,
        required_document_types: list[str] = ["application_proposal", "income_statement", "balance_sheet"],
        requested_by: str = "system",
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> dict[str, Any]:
        return await runtime._execute_command(
            "submit_application",
            handle_submit_application,
            {
                "application_id": application_id,
                "applicant_id": applicant_id,
                "requested_amount_usd": requested_amount_usd,
                "loan_purpose": loan_purpose,
                "loan_term_months": loan_term_months,
                "submission_channel": submission_channel,
                "contact_email": contact_email,
                "contact_name": contact_name,
                "submitted_at": submitted_at,
                "deadline": deadline,
                "required_document_types": required_document_types,
                "requested_by": requested_by,
                "correlation_id": correlation_id,
                "causation_id": causation_id,
            },
        )

    @server.tool(name="start_agent_session", description="Append an agent session start event.")
    async def start_agent_session(
        application_id: str,
        session_id: str,
        agent_id: str,
        agent_type: str,
        model_version: str,
        langgraph_graph_version: str = "unknown",
        context_source: str = "fresh",
        context_token_count: int = 0,
        started_at: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> dict[str, Any]:
        return await runtime._execute_command(
            "start_agent_session",
            handle_start_agent_session,
            {
                "application_id": application_id,
                "session_id": session_id,
                "agent_id": agent_id,
                "agent_type": agent_type,
                "model_version": model_version,
                "langgraph_graph_version": langgraph_graph_version,
                "context_source": context_source,
                "context_token_count": context_token_count,
                "started_at": started_at,
                "correlation_id": correlation_id,
                "causation_id": causation_id,
            },
        )

    @server.tool(name="record_credit_analysis", description="Append a completed credit analysis event.")
    async def record_credit_analysis(
        application_id: str,
        session_id: str,
        agent_type: str = "credit_analysis",
        model_version: str = "unknown-model",
        risk_tier: str = "LOW",
        recommended_limit_usd: float = 0.0,
        confidence: float = 0.0,
        rationale: str = "",
        key_concerns: list[str] | None = None,
        data_quality_caveats: list[str] | None = None,
        policy_overrides_applied: list[str] | None = None,
        model_deployment_id: str = "unknown-deployment",
        input_data_hash: str = "unknown-hash",
        analysis_duration_ms: int = 0,
        regulatory_basis: list[str] | None = None,
        completed_at: str | None = None,
        superseded_by_human_review: bool = False,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> dict[str, Any]:
        return await runtime._execute_command(
            "record_credit_analysis",
            handle_credit_analysis_completed,
            {
                "application_id": application_id,
                "session_id": session_id,
                "agent_type": agent_type,
                "model_version": model_version,
                "risk_tier": risk_tier,
                "recommended_limit_usd": recommended_limit_usd,
                "confidence": confidence,
                "rationale": rationale,
                "key_concerns": key_concerns or [],
                "data_quality_caveats": data_quality_caveats or [],
                "policy_overrides_applied": policy_overrides_applied or [],
                "model_deployment_id": model_deployment_id,
                "input_data_hash": input_data_hash,
                "analysis_duration_ms": analysis_duration_ms,
                "regulatory_basis": regulatory_basis or [],
                "completed_at": completed_at,
                "superseded_by_human_review": superseded_by_human_review,
                "correlation_id": correlation_id,
                "causation_id": causation_id,
            },
        )

    @server.tool(name="record_fraud_screening", description="Append a completed fraud screening event.")
    async def record_fraud_screening(
        application_id: str,
        session_id: str,
        fraud_score: float,
        risk_level: str = "LOW",
        anomalies_found: int = 0,
        recommendation: str = "PROCEED",
        screening_model_version: str = "unknown",
        input_data_hash: str = "unknown",
        completed_at: str | None = None,
        triggered_by_event_id: str = "fraud-screening-completed",
        regulation_set_version: str = "2026-Q1",
        rules_to_evaluate: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> dict[str, Any]:
        return await runtime._execute_command(
            "record_fraud_screening",
            handle_fraud_screening_completed,
            {
                "application_id": application_id,
                "session_id": session_id,
                "fraud_score": fraud_score,
                "risk_level": risk_level,
                "anomalies_found": anomalies_found,
                "recommendation": recommendation,
                "screening_model_version": screening_model_version,
                "input_data_hash": input_data_hash,
                "completed_at": completed_at,
                "triggered_by_event_id": triggered_by_event_id,
                "regulation_set_version": regulation_set_version,
                "rules_to_evaluate": rules_to_evaluate or [],
                "correlation_id": correlation_id,
                "causation_id": causation_id,
            },
        )

    @server.tool(name="record_compliance_check", description="Append a completed compliance check.")
    async def record_compliance_check(
        application_id: str,
        session_id: str,
        overall_verdict: str = "CLEAR",
        rules_evaluated: int = 0,
        rules_passed_count: int = 0,
        rules_failed_count: int = 0,
        rules_noted_count: int = 0,
        has_hard_block: bool = False,
        regulation_set_version: str = "2026-Q1",
        rules_to_evaluate: list[str] | None = None,
        rules_passed: list[dict[str, Any]] | None = None,
        rules_failed: list[dict[str, Any]] | None = None,
        rules_noted: list[dict[str, Any]] | None = None,
        completed_at: str | None = None,
        triggered_by_event_id: str = "compliance-check-completed",
        decline_reasons: list[str] | None = None,
        adverse_action_notice_required: bool = True,
        adverse_action_codes: list[str] | None = None,
        declined_by: str = "compliance_agent",
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> dict[str, Any]:
        return await runtime._execute_command(
            "record_compliance_check",
            handle_compliance_check,
            {
                "application_id": application_id,
                "session_id": session_id,
                "overall_verdict": overall_verdict,
                "rules_evaluated": rules_evaluated,
                "rules_passed_count": rules_passed_count,
                "rules_failed_count": rules_failed_count,
                "rules_noted_count": rules_noted_count,
                "has_hard_block": has_hard_block,
                "regulation_set_version": regulation_set_version,
                "rules_to_evaluate": rules_to_evaluate or [],
                "rules_passed": rules_passed or [],
                "rules_failed": rules_failed or [],
                "rules_noted": rules_noted or [],
                "completed_at": completed_at,
                "triggered_by_event_id": triggered_by_event_id,
                "decline_reasons": decline_reasons or [],
                "adverse_action_notice_required": adverse_action_notice_required,
                "adverse_action_codes": adverse_action_codes or [],
                "declined_by": declined_by,
                "correlation_id": correlation_id,
                "causation_id": causation_id,
            },
        )

    @server.tool(name="generate_decision", description="Append an orchestrator decision and optional downstream event.")
    async def generate_decision(
        application_id: str,
        orchestrator_session_id: str,
        recommendation: str,
        confidence: float,
        approved_amount_usd: float | None = None,
        conditions: list[str] | None = None,
        executive_summary: str = "",
        key_risks: list[str] | None = None,
        contributing_sessions: list[str] | None = None,
        model_versions: dict[str, str] | None = None,
        generated_at: str | None = None,
        review_reason: str = "Recommendation REFER or low confidence",
        decline_reasons: list[str] | None = None,
        adverse_action_notice_required: bool = True,
        adverse_action_codes: list[str] | None = None,
        approved_by: str = "auto",
        interest_rate_pct: float = 12.5,
        term_months: int = 36,
        effective_date: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> dict[str, Any]:
        return await runtime._execute_command(
            "generate_decision",
            handle_generate_decision,
            {
                "application_id": application_id,
                "orchestrator_session_id": orchestrator_session_id,
                "recommendation": recommendation,
                "confidence": confidence,
                "approved_amount_usd": approved_amount_usd,
                "conditions": conditions or [],
                "executive_summary": executive_summary,
                "key_risks": key_risks or [],
                "contributing_sessions": contributing_sessions or [],
                "model_versions": model_versions or {},
                "generated_at": generated_at,
                "review_reason": review_reason,
                "decline_reasons": decline_reasons or [],
                "adverse_action_notice_required": adverse_action_notice_required,
                "adverse_action_codes": adverse_action_codes or [],
                "approved_by": approved_by,
                "interest_rate_pct": interest_rate_pct,
                "term_months": term_months,
                "effective_date": effective_date,
                "correlation_id": correlation_id,
                "causation_id": causation_id,
            },
        )

    @server.tool(name="record_human_review", description="Append a completed human review event.")
    async def record_human_review(
        application_id: str,
        reviewer_id: str,
        final_decision: str,
        override: bool = False,
        original_recommendation: str = "REFER",
        override_reason: str | None = None,
        reviewed_at: str | None = None,
        approved_amount_usd: float | None = None,
        conditions: list[str] | None = None,
        interest_rate_pct: float = 12.5,
        term_months: int = 36,
        effective_date: str | None = None,
        decline_reasons: list[str] | None = None,
        adverse_action_notice_required: bool = True,
        adverse_action_codes: list[str] | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> dict[str, Any]:
        return await runtime._execute_command(
            "record_human_review",
            handle_human_review_completed,
            {
                "application_id": application_id,
                "reviewer_id": reviewer_id,
                "final_decision": final_decision,
                "override": override,
                "original_recommendation": original_recommendation,
                "override_reason": override_reason,
                "reviewed_at": reviewed_at,
                "approved_amount_usd": approved_amount_usd,
                "conditions": conditions or [],
                "interest_rate_pct": interest_rate_pct,
                "term_months": term_months,
                "effective_date": effective_date,
                "decline_reasons": decline_reasons or [],
                "adverse_action_notice_required": adverse_action_notice_required,
                "adverse_action_codes": adverse_action_codes or [],
                "correlation_id": correlation_id,
                "causation_id": causation_id,
            },
        )

    @server.tool(name="run_integrity_check", description="Run a read-only audit-chain integrity check.")
    async def run_integrity_check_tool(
        entity_type: str,
        entity_id: str,
    ) -> dict[str, Any]:
        try:
            result = await run_integrity_check(runtime.store, entity_type=entity_type, entity_id=entity_id)
            audit_stream = f"audit-{entity_type}-{entity_id}"
            audit_event = {
                "event_type": "AuditIntegrityCheckRun",
                "event_version": 1,
                "payload": {
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "check_timestamp": _utcnow(),
                    "events_verified_count": result.events_verified,
                    "integrity_hash": result.integrity_hash,
                    "previous_hash": result.previous_hash,
                    "chain_valid": result.chain_valid,
                    "tamper_detected": result.tamper_detected,
                },
            }
            current_version = await runtime.store.stream_version(audit_stream)
            await runtime.store.append(audit_stream, [audit_event], expected_version=current_version)
            return _structured_ok("run_integrity_check", result=result)
        except Exception as exc:  # pragma: no cover - defensive wrapper
            return _structured_error("run_integrity_check", exc)

    @server.tool(name="refresh_projections", description="Drain pending events into all projections.")
    async def refresh_projections(max_rounds: int = 32) -> dict[str, Any]:
        try:
            report = await runtime.sync_projections(max_rounds=max_rounds)
            return _structured_ok("refresh_projections", **report)
        except Exception as exc:  # pragma: no cover - defensive wrapper
            return _structured_error("refresh_projections", exc)

    @server.resource(
        "ledger://applications/{application_id}",
        name="application_summary",
        mime_type="application/json",
        description="Single application summary row from the projection.",
    )
    async def application_summary(application_id: str) -> dict[str, Any] | None:
        return _json_text(runtime.application_summary.get_application(application_id))

    @server.resource(
        "ledger://applications/{application_id}/compliance",
        name="application_compliance",
        mime_type="application/json",
        description="Current compliance audit state for an application.",
    )
    @server.resource(
        "ledger://applications/{application_id}/compliance?as_of={as_of}",
        name="application_compliance_at",
        mime_type="application/json",
        description="Historical compliance audit snapshot for an application at a point in time.",
    )
    async def application_compliance(application_id: str, as_of: str | None = None) -> dict[str, Any] | None:
        if as_of:
            return _json_text(runtime.compliance_audit.get_compliance_at(application_id, as_of))
        return _json_text(runtime.compliance_audit.get_current_compliance(application_id))

    @server.resource(
        "ledger://applications/{application_id}/audit-trail",
        name="application_audit_trail",
        mime_type="application/json",
        description="Full audit-trail history for an application.",
    )
    @server.resource(
        "ledger://applications/{application_id}/audit-trail?from={from_}&to={to}",
        name="application_audit_trail_range",
        mime_type="application/json",
        description="Range-limited audit-trail history for an application.",
    )
    async def application_audit_trail(
        application_id: str,
        from_: int | None = None,
        to: int | None = None,
    ) -> list[dict[str, Any]]:
        audit_stream = f"audit-loan-{application_id}"
        events = await runtime.store.load_stream(
            audit_stream,
            from_position=int(from_ or 0),
            to_position=int(to) if to is not None else None,
        )
        return _json_text(events)

    @server.resource(
        "ledger://agents/{agent_id}/performance",
        name="agent_performance",
        mime_type="application/json",
        description="Aggregate performance metrics for one agent.",
    )
    async def agent_performance(agent_id: str) -> list[dict[str, Any]]:
        rows = [row for row in runtime.agent_performance.all_rows() if row.get("agent_id") == agent_id]
        return _json_text(rows)

    @server.resource(
        "ledger://agents/stuck-sessions/{timeout_ms}",
        name="agent_stuck_sessions",
        mime_type="application/json",
        description="Agent sessions that have not completed within the timeout window.",
    )
    async def agent_stuck_sessions(timeout_ms: int = 600000) -> list[dict[str, Any]]:
        rows = runtime.agent_session_failures.get_stuck_sessions(int(timeout_ms), now=_utcnow())
        return _json_text(rows)

    @server.resource(
        "ledger://agents/{agent_type}/sessions/{session_id}",
        name="agent_session_replay",
        mime_type="application/json",
        description="Full replay for one agent session stream.",
    )
    async def agent_session_replay(agent_type: str, session_id: str) -> list[dict[str, Any]]:
        stream_id = f"agent-{agent_type}-{session_id}"
        return _json_text(await runtime.store.load_stream(stream_id))

    @server.resource(
        "ledger://ledger/health",
        name="ledger_health",
        mime_type="application/json",
        description="Projection lag snapshot for the ledger runtime.",
    )
    async def ledger_health() -> dict[str, Any]:
        lag_snapshot = runtime.get_lag_snapshot()
        return _json_text(
            {
                "ok": True,
                "p99_target_ms": 10,
                "projections": lag_snapshot,
            }
        )

    @server.resource(
        "ledger://projections/application-summaries",
        name="application_summaries",
        mime_type="application/json",
        description="All application summary rows from the projection.",
    )
    async def application_summaries() -> list[dict[str, Any]]:
        return _json_text(runtime.application_summary.all_rows())

    @server.resource(
        "ledger://projections/manual-reviews",
        name="manual_reviews",
        mime_type="application/json",
        description="All manual review rows from the projection.",
    )
    async def manual_reviews() -> list[dict[str, Any]]:
        return _json_text(runtime.manual_reviews.all_rows())

    @server.resource(
        "ledger://metrics/manual-review-backlog",
        name="manual_review_backlog",
        mime_type="application/json",
        description="Aggregate backlog metrics for pending manual reviews.",
    )
    async def manual_review_backlog() -> dict[str, Any]:
        return _json_text(build_manual_review_backlog_snapshot(runtime.manual_reviews.all_rows()))

    @server.resource(
        "ledger://metrics/event-throughput/{window_minutes}?bucket_minutes={bucket_minutes}",
        name="event_throughput",
        mime_type="application/json",
        description="Event throughput snapshot over the most recent window.",
    )
    async def event_throughput(window_minutes: int = 60, bucket_minutes: int = 5) -> dict[str, Any]:
        return _json_text(
            await build_event_throughput_snapshot(
                runtime.store,
                window_minutes=window_minutes,
                bucket_minutes=bucket_minutes,
            )
        )

    return server


async def _run_server() -> None:
    runtime = create_runtime()
    await runtime.start()
    server = create_server(runtime)
    try:
        await server.run_stdio_async(show_banner=True)
    finally:
        await runtime.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run The Ledger MCP server over stdio.")
    parser.add_argument("--no-banner", action="store_true", help="Suppress the MCP banner.")
    args = parser.parse_args()

    async def _runner() -> None:
        runtime = create_runtime()
        await runtime.start()
        server = create_server(runtime)
        try:
            await server.run_stdio_async(show_banner=not args.no_banner)
        finally:
            await runtime.close()

    asyncio.run(_runner())


if __name__ == "__main__":
    main()
