from ledger.upcasters import UpcasterRegistry, build_default_upcaster_registry


def test_credit_analysis_upcast_v1_to_v2_adds_regulatory_basis():
    registry = UpcasterRegistry()
    raw = {
        "event_type": "CreditAnalysisCompleted",
        "event_version": 1,
        "payload": {
            "application_id": "APEX-1",
            "session_id": "sess-cre-1",
            "decision": {"risk_tier": "MEDIUM"},
        },
    }

    upcasted = registry.upcast(raw)

    assert upcasted["event_version"] == 2
    assert upcasted["payload"]["regulatory_basis"] == []
    assert "regulatory_basis" not in raw["payload"]


def test_decision_generated_upcast_v1_to_v2_adds_model_versions():
    registry = build_default_upcaster_registry()
    raw = {
        "event_type": "DecisionGenerated",
        "event_version": 1,
        "payload": {
            "application_id": "APEX-1",
            "recommendation": "APPROVE",
            "confidence": 0.82,
        },
    }

    upcasted = registry.upcast(raw)

    assert upcasted["event_version"] == 2
    assert upcasted["payload"]["model_versions"] == {}
    assert "model_versions" not in raw["payload"]


def test_non_target_event_is_returned_unchanged():
    registry = UpcasterRegistry()
    raw = {
        "event_type": "ApplicationSubmitted",
        "event_version": 1,
        "payload": {"application_id": "APEX-1"},
    }

    upcasted = registry.upcast(raw)

    assert upcasted["event_type"] == "ApplicationSubmitted"
    assert upcasted["event_version"] == 1
    assert upcasted["payload"] == {"application_id": "APEX-1"}
