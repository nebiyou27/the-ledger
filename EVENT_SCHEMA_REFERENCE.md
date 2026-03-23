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

## Why This Matters

These events are the permanent record of truth in a regulated financial system.
When a new engineer or auditor asks what a payload contains, this document gives
the answer without forcing them to infer meaning from code alone.

The important rule is simple:

- live schema definitions live in code
- historical compatibility rules live in the upcasters
- this document explains both in one place
