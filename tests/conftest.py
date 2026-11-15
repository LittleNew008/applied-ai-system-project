"""Shared fixtures: no test may reach the network, read an API key or write the real trace file."""

import pytest


@pytest.fixture(autouse=True)
def offline_assistant(tmp_path, monkeypatch):
    """Force the Care Assistant into its deterministic offline mode, with a throwaway trace file."""
    monkeypatch.setenv("PAWPAL_NO_DOTENV", "1")  # never read the developer's real .env (it may hold a key)
    monkeypatch.setenv("PAWPAL_OFFLINE", "1")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("PAWPAL_GEMINI_MODEL", raising=False)
    monkeypatch.setenv("PAWPAL_TRACE_FILE", str(tmp_path / "ai_trace.jsonl"))
