from __future__ import annotations

import inspect
import re

import pytest

from ledger.schema.events import BaseEvent


APPROVED_SUFFIXES = (
    "Submitted",
    "Requested",
    "Uploaded",
    "Failed",
    "Generated",
    "Completed",
    "Approved",
    "Declined",
    "Created",
    "Added",
    "Validated",
    "Rejected",
    "Started",
    "Executed",
    "Called",
    "Written",
    "Recovered",
    "Snapshotted",
    "Opened",
    "Consumed",
    "Deferred",
    "Initiated",
    "Passed",
    "Noted",
    "Detected",
    "Run",
    "ReadyForAnalysis",
)

EVENT_NAME_PATTERN = re.compile(r"^[A-Z][A-Za-z0-9]*(?:[A-Z][A-Za-z0-9]*)*$")
POLICY_REF = "EVENT_SCHEMA_REFERENCE.md#event-naming-conventions"


def _iter_event_classes():
    seen: set[type[BaseEvent]] = set()
    stack = list(BaseEvent.__subclasses__())

    while stack:
        cls = stack.pop()
        if cls in seen:
            continue
        seen.add(cls)
        yield cls
        stack.extend(cls.__subclasses__())


def _event_name(cls: type[BaseEvent]) -> str:
    field = cls.model_fields.get("event_type")
    if field is not None and isinstance(field.default, str):
        return field.default
    return cls.__name__


def test_domain_event_names_follow_policy():
    violations: list[str] = []

    for cls in sorted(_iter_event_classes(), key=lambda event_cls: _event_name(event_cls)):
        if not inspect.isclass(cls) or not issubclass(cls, BaseEvent) or cls is BaseEvent:
            continue

        event_name = _event_name(cls)

        if not EVENT_NAME_PATTERN.fullmatch(event_name):
            violations.append(
                f"- {event_name} ({cls.__module__}.{cls.__name__}): violates PascalCase; "
                f"see {POLICY_REF}."
            )

        if "_" in event_name or " " in event_name:
            violations.append(
                f"- {event_name} ({cls.__module__}.{cls.__name__}): contains underscores or spaces; "
                f"see {POLICY_REF}."
            )

        if not event_name.endswith(APPROVED_SUFFIXES):
            suffixes = ", ".join(APPROVED_SUFFIXES)
            violations.append(
                f"- {event_name} ({cls.__module__}.{cls.__name__}): does not end with an approved "
                f"past-tense suffix from the current codebase ({suffixes}); see {POLICY_REF}."
            )

    if violations:
        pytest.fail(
            "Event naming policy violations detected:\n"
            + "\n".join(violations)
            + "\n\n"
            "The canonical policy is documented in EVENT_SCHEMA_REFERENCE.md, and this lint is "
            "intended to keep new event names aligned with the existing event log."
        )
