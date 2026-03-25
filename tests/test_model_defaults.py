from __future__ import annotations

import importlib
import inspect

import pytest


TEST_MODEL = "test-model:latest"


@pytest.mark.parametrize(
    ("module_name", "class_name", "param_name"),
    [
        ("ledger.agents.ollama_client", "OllamaClient", "model"),
        ("ledger.agents.llm_adapter", "OllamaClient", "model"),
        ("ledger.agents.base_agent", "BaseApexAgent", "model"),
        ("ledger.agents.stub_agents", "DocumentProcessingAgent", "model"),
    ],
)
def test_constructor_defaults_follow_ollama_model_env(monkeypatch, module_name, class_name, param_name):
    monkeypatch.setenv("OLLAMA_MODEL", TEST_MODEL)

    module = importlib.import_module(module_name)
    module = importlib.reload(module)

    constructor = getattr(module, class_name).__init__
    default_value = inspect.signature(constructor).parameters[param_name].default

    assert default_value == TEST_MODEL


def test_pipeline_parser_defaults_follow_ollama_model_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", TEST_MODEL)

    from scripts.run_pipeline import build_parser

    parser = build_parser()

    assert parser.get_default("model") == TEST_MODEL
