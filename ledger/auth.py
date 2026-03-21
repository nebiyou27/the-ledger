from __future__ import annotations

from dataclasses import dataclass
from os import environ
from secrets import compare_digest
from typing import Any


@dataclass(frozen=True)
class ApiPrincipal:
    token: str
    role: str


def _parse_api_keys(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}

    mapping: dict[str, str] = {}
    for chunk in raw.split(","):
        item = chunk.strip()
        if not item:
            continue
        separator = "=" if "=" in item else ":" if ":" in item else None
        if separator is None:
            raise ValueError(
                "LEDGER_API_KEYS entries must use role=token or role:token, for example admin=secret"
            )
        role, token = item.split(separator, 1)
        role = role.strip().lower()
        token = token.strip()
        if not role or not token:
            raise ValueError("LEDGER_API_KEYS entries must include both role and token")
        mapping[token] = role
    return mapping


def get_api_key_roles() -> dict[str, str]:
    return _parse_api_keys(environ.get("LEDGER_API_KEYS"))


def auth_enabled() -> bool:
    return bool(get_api_key_roles())


def get_bearer_token(headers: Any) -> str | None:
    auth_header = headers.get("authorization") if headers is not None else None
    if auth_header:
        prefix = "bearer "
        value = str(auth_header).strip()
        if value.lower().startswith(prefix):
            return value[len(prefix) :].strip()
        return value

    api_key = headers.get("x-api-key") if headers is not None else None
    return str(api_key).strip() if api_key else None


def resolve_principal(token: str | None) -> ApiPrincipal | None:
    if not token:
        return None

    for configured_token, role in get_api_key_roles().items():
        if compare_digest(token, configured_token):
            return ApiPrincipal(token=configured_token, role=role)
    return None
