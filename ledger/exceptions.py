from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class OptimisticConcurrencyError(Exception):
    """Raised when expected_version doesn't match the current stream version."""

    stream_id: str
    expected: int
    actual: int

    @classmethod
    def from_versions(cls, stream_id: str, expected: int, actual: int) -> "OptimisticConcurrencyError":
        return cls(stream_id=stream_id, expected=expected, actual=actual)

    def __post_init__(self) -> None:
        Exception.__init__(self, f"OCC on '{self.stream_id}': expected v{self.expected}, actual v{self.actual}")
