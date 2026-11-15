"""Tests of the Care Assistant command-line flow (main.py assist). Offline: no network, no key."""

import re
from datetime import date
from pathlib import Path

import pytest
from wcwidth import wcswidth

import ai_assistant as ai
import main
from pawpal_system import Owner, Pet

TODAY = date(2026, 3, 2)
KB_DIR = Path(__file__).resolve().parent.parent / "knowledge"


def config(tmp_path):
    return ai.AssistantConfig(offline=True, api_key=None, model="offline", offline_reason="test",
                              kb_dir=KB_DIR, trace_path=tmp_path / "trace.jsonl")


def sample():
    owner = main.build_owner()
    return owner, owner.pets[0]  # Biscuit the dog


def no_input(prompt):
    raise AssertionError("should not have asked")


class Typed:
    """A scripted `input()`: returns the given answers in order (an Exception is raised instead)."""

    def __init__(self, *answers):
        self.answers, self.prompts = list(answers), []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


# ------------------------------------------------------------------ choosing

@pytest.mark.parametrize("text, expected", [
    ("", [1, 2]), ("  ", [1, 2]), ("all", [1, 2, 3, 4]), ("ALL", [1, 2, 3, 4]), ("none", []), ("n", []), ("0", []),
    ("3", [3]), ("1,3", [1, 3]), ("3 1", [1, 3]), ("1, 1 ,2", [1, 2]),
])
def test_parse_selection_understands_numbers_all_none_and_blank(text, expected):
    assert main.parse_selection(text, 4, default=[1, 2]) == expected


@pytest.mark.parametrize("text", ["5", "0 1", "-1", "x", "1,x", "1.5", "all 2"])
def test_parse_selection_rejects_anything_else_with_a_message(text):
    with pytest.raises(ValueError, match="not a suggestion number"):
        main.parse_selection(text, 4, default=[1])


# ------------------------------------------------------------------ the flow

def test_the_flow_shows_needs_guides_the_table_and_confidence(capsys, tmp_path):
    owner, pet = sample()
    main.assistant_flow(owner, pet, TODAY, accept="none", config=config(tmp_path))
    out = capsys.readouterr().out
    assert "Care Assistant for 🐶 Biscuit" in out and "Mode: offline (test)" in out
    assert "Needs:" in out and "Care guides used:" in out and "[dog-grooming]" in out
    assert "Confidence" in out and "Sources" in out and "🟢 95%" in out
    assert "Nothing added." in out
    assert len(pet.tasks) == len(main.build_owner().pets[0].tasks)


def test_choosing_numbers_adds_exactly_those_tasks_through_pet_add_task(capsys, tmp_path, monkeypatch):
    owner, pet = sample()
    before = len(pet.tasks)
    added = []
    original = Pet.add_task
    monkeypatch.setattr(Pet, "add_task", lambda self, task: (added.append((self, task)), original(self, task))[1])
    created = main.assistant_flow(owner, pet, TODAY, accept="2", config=config(tmp_path))
    assert [t.title for t in created] == ["Tooth brushing"]
    assert [(p is pet, t.title) for p, t in added if p is pet] == [(True, "Tooth brushing")]
    assert len(pet.tasks) == before + 1
    out = capsys.readouterr().out
    assert "Added 1 task(s) to Biscuit: Tooth brushing" in out and "Today's Schedule" in out
    assert "Tooth brushing" in out.split("Today's Schedule")[1]  # the new schedule includes it


def test_all_adds_every_suggestion_and_default_leaves_out_required_ones(tmp_path, capsys):
    owner, pet = sample()
    pet.special_needs = "daily medication"
    for task in list(pet.tasks):
        if task.category.value == "meds":
            pet.remove_task(task.id)  # so a medication suggestion appears
    result = ai.propose_care_plan(owner, pet, TODAY, config=config(tmp_path))
    required = [i for i, p in enumerate(result.accepted, 1) if p.required]
    assert required and not set(required) & set(main.default_picks(result))
    created = main.assistant_flow(owner, pet, TODAY, accept="", config=config(tmp_path))
    assert all(not t.required for t in created)
    created_all = main.assistant_flow(owner, pet, TODAY, accept="all", config=config(tmp_path))
    assert any(t.required for t in created_all)
    capsys.readouterr()


def test_without_accept_the_user_is_asked_and_the_answer_is_used(capsys, tmp_path):
    owner, pet = sample()
    typed = Typed("1")
    created = main.assistant_flow(owner, pet, TODAY, input_fn=typed, config=config(tmp_path))
    assert [t.title for t in created] == ["Brush coat"]
    assert "Add which?" in typed.prompts[0] and "Enter = 1, 2" in typed.prompts[0]
    capsys.readouterr()


def test_a_mistake_is_explained_and_asked_again_then_gives_up_after_three(capsys, tmp_path):
    owner, pet = sample()
    typed = Typed("9", "banana", "2")
    created = main.assistant_flow(owner, pet, TODAY, input_fn=typed, config=config(tmp_path))
    assert [t.title for t in created] == ["Tooth brushing"]
    assert capsys.readouterr().out.count("is not a suggestion number") == 2

    owner, pet = sample()
    created = main.assistant_flow(owner, pet, TODAY, input_fn=Typed("x", "y", "z"), config=config(tmp_path))
    assert created == [] and "No valid choice, so nothing was added." in capsys.readouterr().out


def test_end_of_input_means_nothing_is_added(capsys, tmp_path):
    owner, pet = sample()
    before = len(pet.tasks)
    assert main.assistant_flow(owner, pet, TODAY, input_fn=Typed(EOFError()), config=config(tmp_path)) == []
    assert len(pet.tasks) == before
    capsys.readouterr()


def test_an_invalid_accept_value_raises_so_the_command_can_report_it(tmp_path):
    owner, pet = sample()
    with pytest.raises(ValueError):
        main.assistant_flow(owner, pet, TODAY, accept="42", config=config(tmp_path))


def test_a_pet_with_bad_details_is_explained_and_nothing_is_asked_or_added(capsys, tmp_path):
    owner, pet = sample()
    pet.age = 99
    before = len(pet.tasks)
    assert main.assistant_flow(owner, pet, TODAY, input_fn=no_input, config=config(tmp_path)) == []
    out = capsys.readouterr().out
    assert "was not asked" in out and "age" in out and len(pet.tasks) == before


def test_a_pet_that_needs_nothing_says_so(capsys, tmp_path):
    owner, pet = sample()
    owner.pets[1].tasks.clear()
    mochi = owner.pets[1]
    from pawpal_system import Category, Task
    for category in (Category.FEEDING, Category.ENRICHMENT, Category.GROOMING):
        mochi.add_task(Task(f"Existing {category.value}", 10, category=category))
    assert main.assistant_flow(owner, mochi, TODAY, input_fn=no_input, config=config(tmp_path)) == []
    assert "Nothing new to suggest" in capsys.readouterr().out


def test_the_suggestion_table_is_a_closed_box_of_equal_width_lines(capsys, tmp_path):
    owner, pet = sample()
    main.assistant_flow(owner, pet, TODAY, accept="none", config=config(tmp_path))
    lines = [l for l in capsys.readouterr().out.splitlines() if l and l[0] in "╭│├╰"]
    assert lines and len({wcswidth(l) for l in lines}) == 1
    assert lines[0].startswith("╭") and lines[-1].startswith("╰")


def test_the_flow_writes_a_step_log_and_names_it(capsys, tmp_path):
    owner, pet = sample()
    cfg = config(tmp_path)
    main.assistant_flow(owner, pet, TODAY, accept="1", config=cfg)
    out = capsys.readouterr().out
    run_id = re.search(r"run id (\w+)", out).group(1)
    assert {r["trace_id"] for r in ai.read_trace(cfg.trace_path, run_id)} == {run_id}


# ------------------------------------------------------------------ the command

def test_the_assist_command_runs_on_the_demo_owner_and_saves_nothing(capsys, monkeypatch):
    monkeypatch.setattr(Owner, "save_to_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("saved")))
    assert main.cli(["assist", "--accept", "1", "--date", "2026-03-02"]) == 0
    assert "Added 1 task(s)" in capsys.readouterr().out


def test_the_assist_command_picks_a_pet_by_name_and_rejects_an_unknown_one(capsys):
    assert main.cli(["assist", "--pet", "mochi", "--accept", "none"]) == 0
    assert "🐱 Mochi" in capsys.readouterr().out
    assert main.cli(["assist", "--pet", "Rex", "--accept", "none"]) == 1
    assert "No pet named 'Rex'. Pets: Biscuit, Mochi" in capsys.readouterr().out


def test_the_assist_command_reports_a_bad_accept_value(capsys):
    assert main.cli(["assist", "--accept", "99"]) == 2
    assert "Invalid --accept" in capsys.readouterr().out


def test_with_data_the_chosen_tasks_are_loaded_from_and_saved_to_that_file(capsys, tmp_path):
    path = tmp_path / "data.json"
    owner = main.build_owner()
    owner.save_to_json(path)
    before = len(owner.pets[0].tasks)
    assert main.cli(["assist", "--data", str(path), "--accept", "1"]) == 0
    assert "Saved to" in capsys.readouterr().out
    reloaded = Owner.load_from_json(path)
    assert len(reloaded.pets[0].tasks) == before + 1 and reloaded.pets[0].tasks[-1].title == "Brush coat"


def test_with_data_nothing_is_saved_when_nothing_is_added(capsys, tmp_path):
    path = tmp_path / "data.json"
    main.build_owner().save_to_json(path)
    stamp = path.read_text(encoding="utf-8")
    assert main.cli(["assist", "--data", str(path), "--accept", "none"]) == 0
    assert path.read_text(encoding="utf-8") == stamp and "Saved to" not in capsys.readouterr().out


def test_a_missing_or_damaged_data_file_is_reported_not_raised(capsys, tmp_path):
    assert main.cli(["assist", "--data", str(tmp_path / "nope.json")]) == 1
    assert "No such data file" in capsys.readouterr().out
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert main.cli(["assist", "--data", str(bad)]) == 1
    assert "Could not read" in capsys.readouterr().out


def test_a_data_file_with_no_pets_is_reported(capsys, tmp_path):
    path = tmp_path / "empty.json"
    Owner("Jordan").save_to_json(path)
    assert main.cli(["assist", "--data", str(path)]) == 1
    assert "no pets" in capsys.readouterr().out


def test_no_command_still_runs_the_demo(capsys):
    assert main.cli([]) == 0
    assert "Today's Schedule" in capsys.readouterr().out
