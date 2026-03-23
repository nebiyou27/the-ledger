# Event Schema Reference

This document is the human-readable companion to `ledger/schema/events.py`.
It summarizes the canonical event contracts used by the ledger so engineers
and auditors can see what each event contains and how historical versions are
handled.

## Canonical Source

- `ledger/schema/events.py` defines the Pydantic event models.
- `EVENT_REGISTRY` maps each `event_type` to its model class.
- `BaseEvent.to_store_dict()` persists events as:
  - `event_type`
  - `event_version`
  - `payload`
- `ledger/upcasting/upcasters.py` applies read-time compatibility fixes for
  historical events.

## Event Naming Conventions

Event names are permanent. Once an event is written to the log, its name
becomes part of the audit trail and should never need cleanup later.

Use these rules for every new event type:

- Use `PascalCase`.
- Start with a stable domain noun prefix.
- End with a past-tense verb.
- Keep the name concise and specific to the business fact being recorded.
- Do not use underscores, spaces, `Was`, or auxiliary verbs such as `has` or
  `did`.
- Do not rename an existing event type just to improve style; introduce a new
  event only when the business fact is genuinely different.

Approved pattern:

- `DomainNounPastTenseVerb`

Examples:

- `ApplicationSubmitted`
- `ComplianceRulePassed`
- `AgentNodeExecuted`

Rejected examples:

- `LoanWasApproved`
- `approve_loan_event`
- `loanSubmitted`
- `ApplicationSubmit`
- `Agent_Node_Executed`

## Shared Event Envelope

Every stored event uses the same outer shape:

- `event_type`: the string discriminator for the event class.
- `event_version`: the schema version written to storage.
- `payload`: the validated model fields for that event, excluding the envelope.

The payload is validated by Pydantic before it is written or re-hydrated.

## Versioned Event Contracts

### `DecisionGenerated`

Current live schema: `event_version = 2`

| Field | Type | Meaning |
|---|---|---|
| `application_id` | `str` | Application the decision belongs to. |
| `orchestrator_session_id` | `str` | Decision orchestrator session that produced the event. |
| `recommendation` | `str` | Final recommendation, such as approve, decline, or refer. |
| `confidence` | `float` | Confidence in the recommendation. |
| `approved_amount_usd` | `Decimal or None` | Approved amount when the recommendation supports approval. |
| `conditions` | `list[str]` | Conditions attached to the decision. |
| `executive_summary` | `str` | Short explanation for humans and downstream systems. |
| `key_risks` | `list[str]` | Main risk factors used to justify the decision. |
| `contributing_sessions` | `list[str]` | Agent session IDs that contributed to the decision. |
| `model_versions` | `dict[str, str]` | Version map for contributing sessions. |
| `generated_at` | `datetime` | Timestamp the decision was generated. |

Field notes:

- `model_versions` is the lineage map for the decision.
- `contributing_sessions` and `model_versions` should be read together when
  auditing how the decision was assembled.
- An empty `model_versions` map means no lineage was recorded for that event.

Legacy compatibility:

- Historical v1 payloads did not carry `model_versions`.
- The v1 -> v2 upcaster reconstructs `model_versions` from the legacy session
  hints when they exist, otherwise it uses an empty map.

### `CreditAnalysisCompleted`

Current live schema: `event_version = 2`

| Field | Type | Meaning |
|---|---|---|
| `application_id` | `str` | Application being analyzed. |
| `session_id` | `str` | Credit-analysis session that produced the event. |
| `decision` | `CreditDecision` | Structured credit decision summary. |
| `model_version` | `str` | Exact model identifier used for the analysis. |
| `model_deployment_id` | `str` | Deployment or release identifier for the model run. |
| `input_data_hash` | `str` | Hash of the input data used by the analysis. |
| `analysis_duration_ms` | `int` | End-to-end runtime in milliseconds. |
| `regulatory_basis` | `list[str]` | Regulation or rule identifiers that informed the analysis. |
| `completed_at` | `datetime` | Timestamp the analysis completed. |

Nested value object: `CreditDecision`

| Field | Type | Meaning |
|---|---|---|
| `risk_tier` | `RiskTier` | `LOW`, `MEDIUM`, or `HIGH`. |
| `recommended_limit_usd` | `Decimal` | Suggested exposure limit. |
| `confidence` | `float` | Confidence in the credit decision. |
| `rationale` | `str` | Human-readable explanation. |
| `key_concerns` | `list[str]` | Main concerns that influenced the decision. |
| `data_quality_caveats` | `list[str]` | Data-quality warnings or limitations. |
| `policy_overrides_applied` | `list[str]` | Policy overrides that affected the outcome. |

Field notes:

- `regulatory_basis` is a list of regulation or policy identifiers, not free
  prose.
- In live v2 events, an empty list means the producer recorded no basis.
- For legacy v1 reads, the upcaster may return `None` because the basis was
  not captured at all.

Legacy compatibility:

- Historical v1 payloads predate `regulatory_basis` and related model-tracking
  metadata.
- The v1 -> v2 upcaster adds compatibility fields on read so consumers can
  continue to process older events without mutating stored history.
- The legacy model-version inference uses the recorded timestamp to return a
  coarse era marker such as `legacy-pre-2026` or `legacy-2026`.
- The upcaster also adds `confidence_score` for historical compatibility
  payloads. That value is a read-time shim, not a separate storage schema.
- `confidence_score` does not exist in the current live Pydantic model, so it
  only appears in upcasted legacy reads and never in the write path.

## Other Event Families

The remaining event families are also defined in `ledger/schema/events.py` and
use the same envelope:

- `LoanApplication`
- `DocumentPackage`
- `AgentSession`
- `CreditRecord`
- `ComplianceRecord`
- `FraudScreening`
- `AuditLedger`

For the exact field lists, consult the canonical Pydantic models in
`ledger/schema/events.py`.

## Aggregate Boundary Reference

This section describes the write-side boundaries around the main aggregates in
the ledger. It complements the event contracts above by showing:

- which commands the aggregate boundary accepts
- which invariants are enforced before events are appended
- which events the command path emits
- which state transitions are considered valid

| Aggregate | Command accepted | Invariant enforced | Event emitted | Terminal states |
|---|---|---|---|---|
| `LoanApplicationAggregate` (`loan-{application_id}`) | `handle_submit_application` | The application must be brand new, and the requested amount must be positive. | `ApplicationSubmitted`, `DocumentUploadRequested` | Any state after `NEW`. |
| `LoanApplicationAggregate` (`loan-{application_id}`) | `handle_credit_analysis_completed` | The application must still be active, and the same credit result cannot be written twice unless a human review explicitly supersedes it. | `CreditAnalysisCompleted` on the loan stream; `CreditRecordOpened` is also written to the credit stream if that stream does not exist yet. | Final application states are terminal: `APPROVED`, `DECLINED`, `DECLINED_COMPLIANCE`, `REFERRED`. |
| `LoanApplicationAggregate` (`loan-{application_id}`) | `handle_fraud_screening_completed` | Fraud results are recorded as part of the normal loan workflow and must feed the compliance step that follows. | `FraudScreeningCompleted` on the fraud stream; `FraudScreeningCompleted` and `ComplianceCheckRequested` on the loan stream. | Not explicitly enforced by the handler; the upstream workflow controls when this step is called. |
| `LoanApplicationAggregate` (`loan-{application_id}`) | `handle_generate_decision` | Low-confidence decisions must be marked as refer, blocked compliance can only end in decline, and every contributing session must already have produced a decision-related event for the same application. | `DecisionGenerated` plus `ApplicationApproved`, `ApplicationDeclined`, or `HumanReviewRequested` on the loan stream. | Final application states are terminal: `APPROVED`, `DECLINED`, `DECLINED_COMPLIANCE`, `REFERRED`. |
| `LoanApplicationAggregate` (`loan-{application_id}`) | `handle_human_review_completed` | A human reviewer may approve only after compliance is complete and not blocked, and the reviewer decision must be recorded before the final outcome. | `HumanReviewCompleted` plus `ApplicationApproved` or `ApplicationDeclined`; if the reviewer refers the case, only `HumanReviewCompleted` is written. | Final application states are terminal: `APPROVED`, `DECLINED`, `DECLINED_COMPLIANCE`, `REFERRED`. |
| `AgentSessionAggregate` (`agent-{agent_type}-{session_id}`) | `handle_start_agent_session` | The session must start once, with context already declared, and later events must match the recorded session identity and model version. | `AgentSessionStarted` | `STARTED`, `COMPLETED`, `FAILED`. |
| `ComplianceRecordAggregate` (`compliance-{application_id}`) | `handle_compliance_check` | Compliance must start from scratch, every rule result must be recorded once, and completion is only allowed when the rule counts and verdict agree. A hard block may end the check early, but only with a blocked verdict. | `ComplianceCheckInitiated`, `ComplianceRulePassed`, `ComplianceRuleFailed`, `ComplianceRuleNoted`, `ComplianceCheckCompleted`; the loan stream also receives `DecisionRequested` or `ApplicationDeclined` afterward. | `COMPLETED`. |

Note: `AuditLedgerAggregate` is intentionally not included in the table because
the repository exposes integrity checks through `run_integrity_check` rather
than a write-side command handler. Its boundary is still documented in
`ledger/domain/aggregates/audit_ledger.py` and `src/integrity/audit_chain.py`.

## Why This Matters

These events are the permanent record of truth in a regulated financial system.
When a new engineer or auditor asks what a payload contains, this document gives
the answer without forcing them to infer meaning from code alone.

The important rule is simple:

- live schema definitions live in code
- historical compatibility rules live in the upcasters
- this document explains both in one place
