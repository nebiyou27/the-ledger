from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


UpcastFn = Callable[
    [dict[str, Any], dict[str, Any], "UpcasterRegistry", dict[str, Any] | None],
    dict[str, Any],
]


@dataclass
class _Registration:
    event_type: str
    from_version: int
    fn: UpcastFn


class UpcasterRegistry:
    """Centralized event upcaster registry.

    Upcasters are read-time transforms only. They must never mutate persisted events.
    """

    def __init__(self):
        self._upcasters: dict[tuple[str, int], UpcastFn] = {}

    def register(self, event_type: str, from_version: int):
        def decorator(fn: UpcastFn):
            self._upcasters[(event_type, from_version)] = fn
            return fn

        return decorator

    # Backward-compatible alias used in older modules.
    def upcaster(self, event_type: str, from_version: int, to_version: int):
        if to_version != from_version + 1:
            raise ValueError("Upcasters must advance exactly one version at a time")
        return self.register(event_type=event_type, from_version=from_version)

    def upcast(self, event: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
        current = dict(event)
        current["payload"] = dict(current.get("payload") or {})
        event_type = str(current.get("event_type", ""))
        version = int(current.get("event_version", 1))

        while (event_type, version) in self._upcasters:
            fn = self._upcasters[(event_type, version)]
            new_payload = fn(dict(current["payload"]), dict(current), self, context)
            current["payload"] = dict(new_payload or {})
            version += 1
            current["event_version"] = version
        return current
