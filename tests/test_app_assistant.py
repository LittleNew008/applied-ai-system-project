"""Headless tests of the Care Assistant panel in app.py (offline mode, no network)."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")
ADD_PET = "FormSubmitter:add_pet_form-Add pet"


@pytest.fixture(autouse=True)
def data_file(tmp_path, monkeypatch):
    path = tmp_path / "data.json"
    monkeypatch.setenv("PAWPAL_DATA_FILE", str(path))
    return path


@pytest.fixture
def at():
    app = AppTest.from_file(APP_PATH, default_timeout=15).run()
    app.text_input(key="pet_name").set_value("Biscuit")
    app.button(ADD_PET).click().run()
    return app


def pet_titles(app) -> list[str]:
    return [t.title for t in app.session_state["owner"].pets[0].tasks]


def test_panel_is_shown_in_offline_mode_and_calls_nothing_until_asked(at):
    assert not at.exception
    assert "ai_result" not in at.session_state
    assert any("Offline mode" in c.value for c in at.caption)
    assert pet_titles(at) == []


def test_suggest_shows_proposals_but_adds_nothing_until_confirmed(at):
    at.button(key="ai_suggest").click().run()
    assert not at.exception
    assert at.session_state["ai_result"][1].accepted
    assert pet_titles(at) == []  # a proposal is not a task yet
    assert at.button(key="ai_add").label == "Add selected"


def test_add_selected_adds_only_the_ticked_tasks_and_autosaves(at, data_file):
    at.button(key="ai_suggest").click().run()
    result = at.session_state["ai_result"][1]
    trace = result.trace_id
    for i, p in enumerate(result.accepted):  # untick everything except the first
        at.checkbox(key=f"ai_pick_{trace}_{i}").set_value(i == 0)
    at.button(key="ai_add").click().run()
    assert not at.exception
    assert pet_titles(at) == [result.accepted[0].title]
    assert "ai_result" not in at.session_state
    assert data_file.is_file() and result.accepted[0].title in data_file.read_text(encoding="utf-8")


def test_dismiss_throws_the_proposals_away(at):
    at.button(key="ai_suggest").click().run()
    at.button(key="ai_dismiss").click().run()
    assert "ai_result" not in at.session_state and pet_titles(at) == []


def test_required_suggestions_start_unticked(at):
    owner = at.session_state["owner"]
    owner.pets[0].special_needs = "daily medication"
    at.button(key="ai_suggest").click().run()
    result = at.session_state["ai_result"][1]
    required = [i for i, p in enumerate(result.accepted) if p.required]
    assert required
    for i in required:
        assert at.checkbox(key=f"ai_pick_{result.trace_id}_{i}").value is False
