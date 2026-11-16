"""Fixing a fixed-time clash: conflicts.py (which tasks clash, where one could move) and the buttons in app.py."""

from datetime import date, time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import display
from conflicts import conflict_pairs, find_move_slot
from pawpal_system import Owner, Pet, Scheduler, Task

DAY = date(2026, 3, 2)
APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")
ADD_PET = "FormSubmitter:add_pet_form-Add pet"
ADD_TASK = "FormSubmitter:add_task_form-Add task"
SAVE_OWNER = "FormSubmitter:owner_form-Save availability"


def t(hour, minute=0):
    return time(hour, minute)


def scheduler_with(*task_specs):
    """task_specs: (pet name, title, fixed time, minutes). One pet per distinct name."""
    pets = {}
    for pet_name, title, when, minutes in task_specs:
        pets.setdefault(pet_name, Pet(pet_name, "dog")).add_task(Task(title, minutes, fixed_time=when, start_date=DAY))
    owner = Owner("Jordan")
    for pet in pets.values():
        owner.add_pet(pet)
    return Scheduler(owner)


def slot_for(scheduler, title):
    task = next(pt.task for pt in scheduler.owner.get_all_tasks() if pt.task.title == title)
    return find_move_slot(scheduler, task, DAY)


# ---------- conflict_pairs ----------


def test_conflict_pairs_match_the_warnings_one_for_one_earlier_task_first():
    scheduler = scheduler_with(("Rex", "Walk", t(9), 30), ("Mochi", "Brush", t(9, 15), 15),
                               ("Rex", "Nap", t(9, 20), 10), ("Rex", "Late", t(15), 30))
    pairs = conflict_pairs(scheduler, DAY)
    warnings = scheduler.find_conflicts(DAY)
    assert [(a.task.title, b.task.title) for a, b in pairs] == [("Walk", "Brush"), ("Walk", "Nap"), ("Brush", "Nap")]
    assert len(pairs) == len(warnings)
    for (a, b), warning in zip(pairs, warnings):
        assert f"'{a.task.title}'" in warning and f"'{b.task.title}'" in warning


def test_touching_tasks_and_flexible_tasks_are_not_conflict_pairs():
    assert conflict_pairs(scheduler_with(("Rex", "Walk", t(8), 30), ("Rex", "Meds", t(8, 30), 10)), DAY) == []
    assert conflict_pairs(Scheduler(Owner("Jordan")), DAY) == []


# ---------- find_move_slot ----------


def test_the_nearest_free_time_after_the_task_is_preferred():
    scheduler = scheduler_with(("Rex", "Walk", t(9), 30), ("Mochi", "Brush", t(9, 15), 15))
    assert slot_for(scheduler, "Brush") == t(9, 30)  # right after the Walk
    assert slot_for(scheduler, "Walk") == t(9, 30)


def test_the_time_freed_by_the_skipped_half_of_the_clash_is_not_offered():
    # The planner keeps the shorter Brush at 9:15 and skips the Walk, so 9:00-9:15 looks free in the plan.
    # Moving the Brush there would clash with the Walk again.
    scheduler = scheduler_with(("Rex", "Walk", t(9), 30), ("Mochi", "Brush", t(9, 15), 15))
    assert scheduler.generate_plan(DAY).scheduled_items[0].task.title == "Brush"
    assert slot_for(scheduler, "Brush") != t(9)


def test_other_fixed_tasks_are_skipped_and_an_earlier_time_is_the_fallback():
    scheduler = scheduler_with(("Rex", "Walk", t(17, 30), 30), ("Mochi", "Brush", t(17, 45), 15), ("Rex", "Vet", t(18), 30))
    scheduler.owner.set_availability(t(8), t(18, 30))  # nothing free after the clash
    assert slot_for(scheduler, "Brush") == t(8)


def test_no_slot_when_the_day_is_full():
    scheduler = scheduler_with(("Rex", "Walk", t(9), 30), ("Mochi", "Brush", t(9, 15), 15))
    scheduler.owner.set_availability(t(9), t(9, 30))
    assert slot_for(scheduler, "Brush") is None


# ---------- the buttons in the app ----------


@pytest.fixture
def at(tmp_path, monkeypatch):
    monkeypatch.setenv("PAWPAL_DATA_FILE", str(tmp_path / "data.json"))
    return AppTest.from_file(APP_PATH, default_timeout=15).run()


def add_pet(at, name, species="dog"):
    at.text_input(key="pet_name").set_value(name)
    at.selectbox(key="pet_species").set_value(species)
    at.button(ADD_PET).click().run()


def add_fixed_task(at, pet, title, when, duration):
    at.button(key=f"select_pet_{pet.id}").click().run() if not at.button(key=f"select_pet_{pet.id}").disabled else None
    at.text_input(key="task_title").set_value(title)
    at.number_input(key="task_duration").set_value(duration)
    at.radio(key="task_timing").set_value("Fixed time")
    at.time_input(key="task_time").set_value(when)
    at.button(ADD_TASK).click().run()


def clashing_pair(at):
    """Rex's Walk 9:00-9:30 and Mochi's Brush 9:15-9:30 (a clash across pets); returns (walk, brush)."""
    add_pet(at, "Rex")
    add_pet(at, "Mochi", species="cat")
    rex, mochi = at.session_state["owner"].pets
    add_fixed_task(at, rex, "Walk", t(9), 30)
    add_fixed_task(at, mochi, "Brush", t(9, 15), 15)
    return rex.tasks[0], mochi.tasks[0]


def clash_button(at, kind, task, other):
    """The button of that kind for `task` in its clash with `other`, or None if it is not shown."""
    key = f"clash_{kind}_{task.id}_{other.id}"
    return next((b for b in at.button if b.key == key), None)


def has_conflict_warning(at):
    return any("Conflict" in w.value for w in at.warning)


def test_a_conflict_offers_move_flexible_and_delete_for_both_tasks(at):
    walk, brush = clashing_pair(at)
    assert has_conflict_warning(at)
    for task, other in ((walk, brush), (brush, walk)):
        for kind in ("move", "flex", "delete"):
            assert clash_button(at, kind, task, other) is not None


def test_move_puts_the_task_in_a_free_slot_and_clears_the_conflict(at):
    walk, brush = clashing_pair(at)
    button = clash_button(at, "move", brush, walk)
    assert button.label.startswith(f"{display.SLOT_ICON} Move to ")
    moved_to = time.fromisoformat(button.label.rsplit(" ", 1)[1])
    button.click().run()

    assert brush.fixed_time == moved_to and walk.fixed_time == t(9)
    assert not has_conflict_warning(at)
    assert any("Moved 'Brush'" in s.value for s in at.success)


def test_delete_removes_only_that_task(at):
    walk, brush = clashing_pair(at)
    clash_button(at, "delete", brush, walk).click().run()

    assert [pt.task.title for pt in at.session_state["owner"].get_all_tasks()] == ["Walk"]
    assert not has_conflict_warning(at)


def test_let_planner_choose_keeps_the_time_as_a_preference(at):
    walk, brush = clashing_pair(at)
    clash_button(at, "flex", brush, walk).click().run()

    assert brush.fixed_time is None and brush.preferred_start == t(9, 15)
    assert not has_conflict_warning(at)  # flexible tasks are not compared


def test_no_move_button_when_nothing_is_free_but_delete_is_still_offered(at):
    at.time_input(key="day_start").set_value(t(9))
    at.time_input(key="day_end").set_value(t(9, 30))
    at.button(SAVE_OWNER).click().run()
    walk, brush = clashing_pair(at)
    assert clash_button(at, "move", brush, walk) is None
    assert clash_button(at, "delete", brush, walk) is not None
