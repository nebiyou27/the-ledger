from __future__ import annotations

from unittest.mock import Mock, patch

import requests

from ledger.agents.ollama_client import OllamaClient


def _make_post_response(content: str) -> Mock:
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"message": {"content": content}}
    return response


def test_ask_returns_parsed_dict_for_valid_json():
    client = OllamaClient(base_url="http://localhost:11434", model="test-model", timeout=9)
    payload = '{"decision":"approve","score":0.87}'

    with patch("requests.post") as mock_post:
        mock_post.return_value = _make_post_response(payload)

        result = client.ask("system prompt", "user message")

    assert result == {"decision": "approve", "score": 0.87}
    mock_post.assert_called_once_with(
        "http://localhost:11434/api/chat",
        json={
            "model": "test-model",
            "stream": False,
            "messages": [
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "user message"},
            ],
        },
        timeout=9,
    )


def test_ask_returns_raw_payload_when_response_text_is_not_json():
    client = OllamaClient(base_url="http://localhost:11434", model="test-model", timeout=9)

    with patch("requests.post") as mock_post:
        mock_post.return_value = _make_post_response("plain text answer")

        result = client.ask("system prompt", "user message")

    assert result == {"raw": "plain text answer", "parse_error": True}


def test_ask_returns_fallback_when_connection_refused():
    client = OllamaClient()

    with patch("requests.post", side_effect=requests.exceptions.ConnectionError):
        result = client.ask("system prompt", "user message")

    assert result == {"error": "ollama_unavailable", "fallback": True}


def test_ask_returns_fallback_when_it_times_out():
    client = OllamaClient()

    with patch("requests.post", side_effect=requests.exceptions.Timeout):
        result = client.ask("system prompt", "user message")

    assert result == {"error": "ollama_timeout", "fallback": True}


def test_is_available_returns_true_when_reachable():
    client = OllamaClient(base_url="http://localhost:11434")
    response = Mock()
    response.raise_for_status.return_value = None

    with patch("requests.get", return_value=response) as mock_get:
        result = client.is_available()

    assert result is True
    mock_get.assert_called_once_with("http://localhost:11434/api/tags", timeout=2)


def test_is_available_returns_false_when_unreachable():
    client = OllamaClient(base_url="http://localhost:11434")

    with patch("requests.get", side_effect=requests.exceptions.ConnectionError):
        result = client.is_available()

    assert result is False
