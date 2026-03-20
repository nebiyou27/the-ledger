from __future__ import annotations

import json
from pathlib import Path

import pytest

from ledger.demo_scenarios import build_narr05_demo_store
from ledger.regulatory import generate_regulatory_package


@pytest.mark.asyncio
async def test_generate_regulatory_package_for_narr05(tmp_path: Path):
    store, application_id = await build_narr05_demo_store()
    output_path = tmp_path / "regulatory_package_NARR05.json"

    package = await generate_regulatory_package(store, application_id, output_path=output_path)

    assert package["application_id"] == "NARR-05"
    assert package["what_if"]["actual"]["final_decision"] == "APPROVED"
    assert package["underwriting"]["override_used"] is True
    assert package["underwriting"]["approved_amount_usd"] == 750000
    assert package["what_if"]["underwriting"]["conditions_count"] == 2
    assert package["what_if"]["counterfactuals"][0]["final_decision"] == "DECLINED"
    assert any(row["agent_id"] == "credit-agent-01" for row in package["agent_performance"])
    assert any(row["agent_id"] == "orchestrator-agent-01" for row in package["agent_performance"])

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["application_id"] == "NARR-05"
    assert written["what_if"]["actual"]["final_decision"] == "APPROVED"


def test_what_if_projector_counterfactuals():
    from ledger.projections.what_if import WhatIfProjector

    projector = WhatIfProjector()
    events = [
        {"event_type": "ApplicationSubmitted", "payload": {"application_id": "APP-1", "requested_amount_usd": 1000}},
        {
            "event_type": "DecisionGenerated",
            "payload": {"application_id": "APP-1", "recommendation": "DECLINE", "confidence": 0.64},
        },
        {
            "event_type": "HumanReviewCompleted",
            "payload": {"application_id": "APP-1", "reviewer_id": "LO-1", "override": True},
        },
        {
            "event_type": "ApplicationApproved",
            "payload": {"application_id": "APP-1", "approved_amount_usd": 1000, "conditions": ["c1", "c2"]},
        },
    ]

    result = projector.project("APP-1", events, application_summary={"state": "FINAL_APPROVED"})
    assert result["actual"]["final_decision"] == "APPROVED"
    assert result["underwriting"]["approval_ratio"] == 1.0
    assert result["counterfactuals"][0]["final_decision"] == "DECLINED"
