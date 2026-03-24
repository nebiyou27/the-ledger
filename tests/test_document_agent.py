from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ledger.agents.stub_agents import DocumentProcessingAgent
from ledger.event_store import InMemoryEventStore


class _NoopLLM:
    async def generate(self, **kwargs):
        return ("noop", 0, 0, 0.0)


def _event(event_type: str, **payload):
    return {"event_type": event_type, "event_version": 1, "payload": payload}


async def _seed_structured_input(store: InMemoryEventStore, application_id: str, **payload):
    await store.append(
        f"loan-{application_id}",
        [_event("ApplicationSubmitted", application_id=application_id, **payload)],
        expected_version=-1,
    )


def _make_refinery_result(doc_id: str = "DOC-REF-1", strategy_used: str = "strategy_b"):
    return SimpleNamespace(
        doc_id=doc_id,
        document_class="Class 2",
        strategy_used=strategy_used,
        escalation_count=1,
        pages=[
            SimpleNamespace(confidence=0.92, strategy_used=strategy_used),
            SimpleNamespace(confidence=0.88, strategy_used=strategy_used),
        ],
        fact_table=[
            {
                "field_name": "loan amount",
                "value": 250000,
                "doc_id": doc_id,
                "page_number": 1,
                "bbox": [1, 2, 3, 4],
                "content_hash": "hash-loan",
                "strategy_used": strategy_used,
            },
            {
                "field_name": "annual income",
                "value": 900000,
                "doc_id": doc_id,
                "page_number": 2,
                "bbox": [5, 6, 7, 8],
                "content_hash": "hash-income",
                "strategy_used": strategy_used,
            },
            {
                "field_name": "loan term months",
                "value": 36,
                "doc_id": doc_id,
                "page_number": 2,
                "bbox": [9, 10, 11, 12],
                "content_hash": "hash-term",
                "strategy_used": strategy_used,
            },
        ],
    )


@pytest.mark.asyncio
async def test_path_a_structured_input_emits_document_processed():
    store = InMemoryEventStore()
    await _seed_structured_input(
        store,
        "DOC-A-001",
        applicant_id="COMP-001",
        loan_amount=150000,
        annual_income=600000,
        loan_term_months=36,
    )
    agent = DocumentProcessingAgent(
        agent_id="doc-agent-01",
        agent_type="document_processing",
        store=store,
        registry=None,
        llm=_NoopLLM(),
    )

    await agent.process_application("DOC-A-001")

    events = await store.load_stream("docpkg-DOC-A-001")
    processed = next(event for event in events if event["event_type"] == "DOCUMENT_PROCESSED")

    assert processed["payload"]["applicant_id"] == "COMP-001"
    assert processed["payload"]["loan_amount"] == 150000
    assert processed["payload"]["annual_income"] == 600000
    assert processed["payload"]["loan_term_months"] == 36


@pytest.mark.asyncio
async def test_path_a_missing_required_field_emits_failure():
    store = InMemoryEventStore()
    await _seed_structured_input(
        store,
        "DOC-A-002",
        applicant_id="COMP-002",
        loan_amount=150000,
        loan_term_months=36,
    )
    agent = DocumentProcessingAgent(
        agent_id="doc-agent-02",
        agent_type="document_processing",
        store=store,
        registry=None,
        llm=_NoopLLM(),
    )

    with pytest.raises(ValueError):
        await agent.process_application("DOC-A-002")

    events = await store.load_stream("docpkg-DOC-A-002")
    failure = next(event for event in events if event["event_type"] == "DOCUMENT_PROCESSING_FAILED")
    assert failure["payload"]["reason"] == "required_field_not_extracted"
    assert "annual_income" in failure["payload"]["failed_fields"]


@pytest.mark.asyncio
async def test_path_b_missing_refinery_path_emits_refinery_unavailable():
    store = InMemoryEventStore()
    await _seed_structured_input(
        store,
        "DOC-B-001",
        applicant_id="COMP-003",
        loan_amount=150000,
        annual_income=600000,
        loan_term_months=36,
        pdf_path=r"C:\temp\sample.pdf",
    )
    agent = DocumentProcessingAgent(
        agent_id="doc-agent-03",
        agent_type="document_processing",
        store=store,
        registry=None,
        llm=_NoopLLM(),
    )

    with pytest.raises(RuntimeError):
        await agent.process_application("DOC-B-001")

    events = await store.load_stream("docpkg-DOC-B-001")
    failure = next(event for event in events if event["event_type"] == "DOCUMENT_PROCESSING_FAILED")
    assert failure["payload"]["reason"] == "refinery_unavailable"


@pytest.mark.asyncio
async def test_path_b_extraction_failure_emits_failed_event(monkeypatch):
    store = InMemoryEventStore()
    await _seed_structured_input(
        store,
        "DOC-B-002",
        applicant_id="COMP-004",
        loan_amount=150000,
        annual_income=600000,
        loan_term_months=36,
        pdf_path=r"C:\temp\sample.pdf",
    )
    agent = DocumentProcessingAgent(
        agent_id="doc-agent-04",
        agent_type="document_processing",
        store=store,
        registry=None,
        llm=_NoopLLM(),
    )

    monkeypatch.setenv("DOCUMENT_REFINERY_PATH", r"D:\TRP-1\Week-3\document-refinery")
    monkeypatch.setattr("ledger.agents.stub_agents.REFINERY_AVAILABLE", True)
    with patch("ledger.agents.stub_agents.run_extraction", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            await agent.process_application("DOC-B-002")

    events = await store.load_stream("docpkg-DOC-B-002")
    failure = next(event for event in events if event["event_type"] == "DOCUMENT_PROCESSING_FAILED")
    assert failure["payload"]["reason"] == "extraction_failed"


@pytest.mark.asyncio
async def test_path_b_low_confidence_emits_failure(monkeypatch):
    store = InMemoryEventStore()
    await _seed_structured_input(
        store,
        "DOC-B-003",
        applicant_id="COMP-005",
        loan_amount=150000,
        annual_income=600000,
        loan_term_months=36,
        pdf_path=r"C:\temp\sample.pdf",
    )
    agent = DocumentProcessingAgent(
        agent_id="doc-agent-05",
        agent_type="document_processing",
        store=store,
        registry=None,
        llm=_NoopLLM(),
    )

    low_confidence_result = SimpleNamespace(
        doc_id="DOC-LOW",
        document_class="Class 3",
        strategy_used="strategy_c",
        escalation_count=2,
        pages=[
            SimpleNamespace(confidence=0.40, strategy_used="strategy_c"),
            SimpleNamespace(confidence=0.45, strategy_used="strategy_c"),
        ],
        fact_table=[],
    )
    monkeypatch.setenv("DOCUMENT_REFINERY_PATH", r"D:\TRP-1\Week-3\document-refinery")
    monkeypatch.setattr("ledger.agents.stub_agents.REFINERY_AVAILABLE", True)
    with patch("ledger.agents.stub_agents.run_extraction", return_value=low_confidence_result):
        with pytest.raises(ValueError):
            await agent.process_application("DOC-B-003")

    events = await store.load_stream("docpkg-DOC-B-003")
    failure = next(event for event in events if event["event_type"] == "DOCUMENT_PROCESSING_FAILED")
    assert failure["payload"]["reason"] == "confidence_too_low"
    assert "0.43" in failure["payload"]["details"]


@pytest.mark.asyncio
async def test_path_b_success_emits_ingested_and_processed_with_provenance(monkeypatch):
    store = InMemoryEventStore()
    await _seed_structured_input(
        store,
        "DOC-B-004",
        applicant_id="COMP-006",
        loan_amount=150000,
        annual_income=600000,
        loan_term_months=36,
        pdf_path=r"C:\temp\sample.pdf",
    )
    agent = DocumentProcessingAgent(
        agent_id="doc-agent-06",
        agent_type="document_processing",
        store=store,
        registry=None,
        llm=_NoopLLM(),
    )

    result = _make_refinery_result()
    monkeypatch.setenv("DOCUMENT_REFINERY_PATH", r"D:\TRP-1\Week-3\document-refinery")
    monkeypatch.setattr("ledger.agents.stub_agents.REFINERY_AVAILABLE", True)
    with patch("ledger.agents.stub_agents.run_extraction", return_value=result) as mock_run:
        await agent.process_application("DOC-B-004")

    mock_run.assert_called_once()
    args, kwargs = mock_run.call_args
    assert args[0] == r"C:\temp\sample.pdf"
    assert kwargs == {}
    events = await store.load_stream("docpkg-DOC-B-004")
    assert any(event["event_type"] == "DOCUMENT_INGESTED" for event in events)
    processed = next(event for event in events if event["event_type"] == "DOCUMENT_PROCESSED")
    assert processed["payload"]["applicant_id"] == "DOC-REF-1"
    assert processed["payload"]["loan_amount"] == 250000
    assert processed["payload"]["annual_income"] == 900000
    assert processed["payload"]["loan_term_months"] == 36
    assert processed["payload"]["loan_amount_provenance"]["doc_id"] == "DOC-REF-1"
    assert processed["payload"]["annual_income_provenance"]["page_number"] == 2


@pytest.mark.asyncio
async def test_path_b_missing_field_lists_failed_field(monkeypatch):
    store = InMemoryEventStore()
    await _seed_structured_input(
        store,
        "DOC-B-005",
        applicant_id="COMP-007",
        loan_amount=150000,
        annual_income=600000,
        loan_term_months=36,
        pdf_path=r"C:\temp\sample.pdf",
    )
    agent = DocumentProcessingAgent(
        agent_id="doc-agent-07",
        agent_type="document_processing",
        store=store,
        registry=None,
        llm=_NoopLLM(),
    )

    incomplete_result = SimpleNamespace(
        doc_id="DOC-INCOMPLETE",
        document_class="Class 2",
        strategy_used="strategy_b",
        escalation_count=0,
        pages=[SimpleNamespace(confidence=0.91, strategy_used="strategy_b")],
        fact_table=[
            {
                "field_name": "loan amount",
                "value": 250000,
                "doc_id": "DOC-INCOMPLETE",
                "page_number": 1,
                "bbox": [1, 2, 3, 4],
                "content_hash": "hash-loan",
                "strategy_used": "strategy_b",
            },
            {
                "field_name": "loan term months",
                "value": 36,
                "doc_id": "DOC-INCOMPLETE",
                "page_number": 1,
                "bbox": [5, 6, 7, 8],
                "content_hash": "hash-term",
                "strategy_used": "strategy_b",
            },
        ],
    )
    monkeypatch.setenv("DOCUMENT_REFINERY_PATH", r"D:\TRP-1\Week-3\document-refinery")
    monkeypatch.setattr("ledger.agents.stub_agents.REFINERY_AVAILABLE", True)
    with patch("ledger.agents.stub_agents.run_extraction", return_value=incomplete_result):
        with pytest.raises(ValueError):
            await agent.process_application("DOC-B-005")

    events = await store.load_stream("docpkg-DOC-B-005")
    failure = next(event for event in events if event["event_type"] == "DOCUMENT_PROCESSING_FAILED")
    assert failure["payload"]["reason"] == "required_field_not_extracted"
    assert "annual_income" in failure["payload"]["failed_fields"]


@pytest.mark.asyncio
async def test_refinery_config_is_passed_explicitly(monkeypatch):
    store = InMemoryEventStore()
    await _seed_structured_input(
        store,
        "DOC-B-006",
        applicant_id="COMP-008",
        loan_amount=150000,
        annual_income=600000,
        loan_term_months=36,
        pdf_path=r"C:\temp\sample.pdf",
    )
    agent = DocumentProcessingAgent(
        agent_id="doc-agent-08",
        agent_type="document_processing",
        store=store,
        registry=None,
        llm=_NoopLLM(),
    )

    monkeypatch.setenv("DOCUMENT_REFINERY_PATH", r"D:\TRP-1\Week-3\document-refinery")
    monkeypatch.setattr("ledger.agents.stub_agents.REFINERY_AVAILABLE", True)
    with patch("ledger.agents.stub_agents.run_extraction", return_value=_make_refinery_result()) as mock_run:
        await agent.process_application("DOC-B-006")

    _, kwargs = mock_run.call_args
    assert kwargs == {}
    config = mock_run.call_args.args[1]
    assert config["lang"] == "amh+eng"
    assert config["psm"] == 3
    assert config["table_density_threshold"] == 0.3
