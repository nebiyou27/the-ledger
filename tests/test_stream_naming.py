from __future__ import annotations

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = (ROOT / "ledger", ROOT / "src", ROOT / "scripts")
STREAM_TEMPLATE_RE = re.compile(
    r'f"(?P<template>(?:loan|docpkg|credit|fraud|compliance|agent|audit)-[^"\n]+)'
)
PLACEHOLDER_RE = re.compile(r"\{[^}]+\}")
POLICY_REF = "ARCHITECTURE.md#stream-naming-conventions"

# Normalized stream families already used in the codebase.
APPROVED_TEMPLATES = {
    "loan-{id}",
    "docpkg-{id}",
    "credit-{id}",
    "fraud-{id}",
    "compliance-{id}",
    "agent-{id}-{id}",
    "audit-{id}",
    "audit-{id}-{id}",
    "audit-loan-{id}",
}


def _normalize(template: str) -> str:
    return PLACEHOLDER_RE.sub("{id}", template)


def _collect_stream_templates() -> list[tuple[Path, str]]:
    templates: list[tuple[Path, str]] = []
    for source_dir in SOURCE_DIRS:
        if not source_dir.exists():
            continue
        for path in source_dir.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for match in STREAM_TEMPLATE_RE.finditer(text):
                templates.append((path, match.group("template")))
    return templates


def test_stream_names_follow_policy():
    violations: list[str] = []

    seen: set[tuple[Path, str]] = set()
    for path, template in _collect_stream_templates():
        key = (path, template)
        if key in seen:
            continue
        seen.add(key)

        if "." in template:
            continue

        normalized = _normalize(template)
        if normalized not in APPROVED_TEMPLATES:
            violations.append(
                f"- {template} ({path.relative_to(ROOT)}): normalizes to {normalized}, "
                f"which is not one of the approved stream families; see {POLICY_REF}."
            )

    if violations:
        pytest.fail(
            "Stream naming policy violations detected:\n"
            + "\n".join(violations)
            + "\n\n"
            "The canonical stream families are documented in ARCHITECTURE.md, and this lint is "
            "intended to keep new stream IDs from colliding as aggregates are added."
        )
