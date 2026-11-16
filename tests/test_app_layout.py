"""Tests of the app's layout and navigation: four tabs, a visible way into the AI Care Assistant, compact pet tiles.

They drive the real app headlessly (offline, no network). What a browser would show (how wide the column looks, how the
tiles are drawn) cannot be seen here; these tests check the structure that produces it, and the behaviour.
"""

import re
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"
ADD_PET = "FormSubmitter:add_pet_form-Add pet"
ADD_TASK = "FormSubmitter:add_task_form-Add task"
TAB_PETS, TAB_AI, TAB_PLAN, TAB_SETTINGS = "🐾 Pets & tasks", "✨ AI Care Assistant", "📅 Daily schedule", "⚙️ Settings"


@pytest.fixture(autouse=True)
def data_file(tmp_path, monkeypatch):
    path = tmp_path / "data.json"
    monkeypatch.setenv("PAWPAL_DATA_FILE", str(path))
    return path


@pytest.fixture
def at():
    return AppTest.from_file(str(APP_PATH), default_timeout=20).run()


@pytest.fixture
def sample(at):
    at.button(key="load_sample").click().run()
    return at


def add_pet(app, name, species="dog"):
    app.text_input(key="pet_name").set_value(name)
    app.selectbox(key="pet_species").set_value(species)
    app.button(ADD_PET).click().run()
    return app


def pets(app):
    return app.session_state["owner"].pets


def expanders(app) -> dict[str, bool]:
    return {e.label: e.proto.expanded for e in app.expander}


# ------------------------------------------------------------------ the page is a narrow column, not the full screen

def test_the_page_is_a_centred_column_capped_well_under_a_wide_screen():
    source = APP_PATH.read_text(encoding="utf-8")
    assert 'layout="centered"' in source and 'layout="wide"' not in source
    widths = [int(w) for w in re.findall(r"max-width:\s*(\d+)px", source)]
    assert widths and max(widths) <= 1000  # the cap that keeps the page from stretching across a large monitor
    assert ".stMainBlockContainer" in source


# ------------------------------------------------------------------ four tabs, with the assistant easy to find

def test_there_are_four_tabs_and_the_ai_assistant_is_the_second_with_an_icon_and_ai_in_its_name(at):
    assert not at.exception
    assert [t.label for t in at.tabs] == [TAB_PETS, TAB_AI, TAB_PLAN, TAB_SETTINGS]
    assert "✨" in at.tabs[1].label and "AI" in at.tabs[1].label
    assert at.session_state["main_tab"] == TAB_PETS  # the first tab is shown first


def test_the_pets_tab_holds_pets_and_tasks_the_schedule_tab_the_plan_and_settings_the_day(at):
    pets_tab, ai_tab, plan_tab, settings_tab = at.tabs
    keys = lambda tab: {el.key for el in _walk(tab) if getattr(el, "key", None)}
    assert ADD_PET in keys(pets_tab) and "ai_suggest" not in keys(pets_tab)
    assert "generate" in keys(plan_tab) and "plan_date" in keys(plan_tab) and "ai_suggest" not in keys(plan_tab)
    assert "owner_name" in keys(settings_tab) and "clear_all" in keys(settings_tab)
    assert "load_sample_ai" in keys(ai_tab)  # (no pets yet: the assistant tab's own sample-data button)


def _walk(node):
    """Every element under a container, whatever its depth."""
    for child in getattr(node, "children", {}).values():
        yield child
        yield from _walk(child)


# ------------------------------------------------------------------ getting to the assistant

def test_with_no_pets_there_is_no_callout_but_the_assistant_tab_explains_what_to_do(at):
    assert "open_assistant" not in [b.key for b in at.button]
    assert any("No pets yet" in i.value for i in at.info)
    assert "load_sample_ai" in [b.key for b in at.button]  # the assistant tab offers the sample data too
    at.button(key="load_sample_ai").click().run()
    assert len(pets(at)) == 2 and not at.exception


def test_once_there_is_a_pet_the_first_tab_shows_a_card_that_opens_the_assistant(sample):
    callout = [m.value for m in sample.markdown if "Not sure what to add for" in m.value]
    assert callout and "Biscuit" in callout[0] and "AI Care Assistant" in callout[0] and " min," not in callout[0]
    assert sample.session_state["main_tab"] == TAB_PETS
    sample.button(key="open_assistant").click().run()
    assert sample.session_state["main_tab"] == TAB_AI and not sample.exception


def test_each_pet_tile_has_a_suggest_button_that_selects_that_pet_and_opens_the_assistant(sample):
    biscuit, mochi = pets(sample)
    assert sample.session_state["selected_pet_id"] == biscuit.id
    sample.button(key=f"suggest_pet_{mochi.id}").click().run()
    assert sample.session_state["selected_pet_id"] == mochi.id
    assert sample.session_state["main_tab"] == TAB_AI
    assert sample.button(key="ai_suggest").label == "✨ Suggest tasks for Mochi"


def test_the_callout_follows_the_selected_pet(sample):
    biscuit, mochi = pets(sample)
    sample.button(key=f"select_pet_{mochi.id}").click().run()
    assert any("Not sure what to add for Mochi" in m.value for m in sample.markdown)


# ------------------------------------------------------------------ inside the assistant tab

def test_with_several_pets_the_assistant_tab_lets_you_choose_the_pet_and_stays_in_step_with_the_tiles(sample):
    biscuit, mochi = pets(sample)
    chooser = sample.radio(key="ai_pet_choice")
    assert chooser.value == biscuit.id and list(chooser.options) == ["🐶 Biscuit", "🐱 Mochi"]  # shown with their icons
    chooser.set_value(mochi.id).run()
    assert sample.session_state["selected_pet_id"] == mochi.id  # the choice is the app's selected pet
    assert sample.button(key="ai_suggest").label == "✨ Suggest tasks for Mochi"
    sample.button(key=f"select_pet_{biscuit.id}").click().run()  # choosing a tile moves the chooser too
    assert sample.radio(key="ai_pet_choice").value == biscuit.id
    assert sample.button(key="ai_suggest").label == "✨ Suggest tasks for Biscuit"


def test_with_one_pet_there_is_no_chooser_just_its_name(at):
    add_pet(at, "Rex")
    assert "ai_pet_choice" not in [r.key for r in at.radio]
    assert any("Suggesting for" in m.value and "Rex" in m.value for m in at.markdown)


def test_the_suggest_button_is_the_main_action_and_the_explainer_shows_until_there_are_results(sample):
    button = sample.button(key="ai_suggest")
    assert button.proto.type == "primary"
    assert any("Nothing is added until you tick it" in m.value for m in sample.markdown)
    assert "How it works" in expanders(sample)
    sample.button(key="ai_suggest").click().run()
    assert "How it works" not in expanders(sample)  # replaced by the results
    assert any(m.value.startswith("**Suggestions for Biscuit**") for m in sample.markdown)
    assert any(re.fullmatch(r"\d+ of \d+ selected", c.value) for c in sample.caption)


def test_dismissing_the_suggestions_brings_the_explainer_back(sample):
    sample.button(key="ai_suggest").click().run()
    sample.button(key="ai_dismiss").click().run()
    assert "ai_result" not in sample.session_state and "How it works" in expanders(sample)


# ------------------------------------------------------------------ the page stays where you are

def test_adding_the_suggested_tasks_keeps_you_on_the_assistant_tab(sample):
    sample.button(key="open_assistant").click().run()
    sample.button(key="ai_suggest").click().run()
    before = len(pets(sample)[0].tasks)
    sample.button(key="ai_add").click().run()
    assert len(pets(sample)[0].tasks) > before
    assert sample.session_state["main_tab"] == TAB_AI  # st.rerun() did not send you back to the first tab
    assert any("suggested task" in s.value for s in sample.success)


def test_adding_a_task_by_hand_keeps_you_on_the_pets_tab(at):
    add_pet(at, "Rex")
    at.text_input(key="task_title").set_value("Walk")
    at.button(ADD_TASK).click().run()
    assert at.session_state["main_tab"] == TAB_PETS and [t.title for t in pets(at)[0].tasks] == ["Walk"]


# ------------------------------------------------------------------ forms get out of the way once they are not needed

def test_the_add_pet_form_is_open_only_while_there_are_no_pets(at):
    assert expanders(at)["➕ Add a pet"] is True
    add_pet(at, "Rex")
    assert expanders(at)["➕ Add a pet"] is False


def test_the_add_task_form_is_named_for_the_pet_and_open_only_while_that_pet_has_no_tasks(at):
    add_pet(at, "Rex")
    assert expanders(at)["➕ Add a task for Rex"] is True
    assert any("Adding a task for **Rex**" in m.value for m in at.markdown)
    at.text_input(key="task_title").set_value("Walk")
    at.button(ADD_TASK).click().run()
    assert expanders(at)["➕ Add a task for Rex"] is False


def test_the_pet_tiles_are_compact_two_rows_of_buttons(sample):
    for pet in pets(sample):
        keys = [b.key for b in sample.button]
        assert {f"select_pet_{pet.id}", f"suggest_pet_{pet.id}", f"remove_pet_{pet.id}"} <= set(keys)
    assert sum(1 for m in sample.markdown if m.value.startswith("### ")) == 2  # a heading per tile, nothing else
