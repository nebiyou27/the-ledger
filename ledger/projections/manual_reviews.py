"""
ledger/projections/manual_reviews.py
====================================
Minimal human-in-the-loop projection for tracking pending manual reviews.
"""
import logging
from typing import Dict, Any

from ledger.projections.base import Projection

logger = logging.getLogger(__name__)

class ManualReviewsProjection(Projection):
    """
    Tracks loan applications pending human review or override.
    Projects state transitions from HumanReviewRequested to HumanReviewCompleted.
    """
    
    def __init__(self, name: str = "manual_reviews"):
        super().__init__(name)
        # In-memory store for demonstration; in prod, this is a distinct table.
        self.reviews: Dict[str, Dict[str, Any]] = {}

    def get_event_types(self) -> list[str]:
        return ["HumanReviewRequested", "HumanReviewCompleted"]

    async def process_event(self, event: dict[str, Any]) -> None:
        event_type = event["event_type"]
        payload = event["payload"]
        app_id = payload["application_id"]

        if event_type == "HumanReviewRequested":
            self.reviews[app_id] = {
                "application_id": app_id,
                "status": "PENDING",
                "reason": payload.get("reason"),
                "assigned_to": payload.get("assigned_to"),
                "requested_at": payload.get("requested_at"),
            }
            logger.info(f"Manual review requested for {app_id}.")

        elif event_type == "HumanReviewCompleted":
            if app_id in self.reviews:
                self.reviews[app_id]["status"] = "RESOLVED"
                self.reviews[app_id]["reviewer_id"] = payload.get("reviewer_id")
                self.reviews[app_id]["override"] = payload.get("override")
                self.reviews[app_id]["final_decision"] = payload.get("final_decision")
                self.reviews[app_id]["override_reason"] = payload.get("override_reason")
                self.reviews[app_id]["reviewed_at"] = payload.get("reviewed_at")
                logger.info(f"Manual review completed for {app_id} (Override: {payload.get('override')}).")
