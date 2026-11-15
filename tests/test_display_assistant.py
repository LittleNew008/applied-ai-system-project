"""Tests of the display.py helpers that format Care Assistant proposals (shared by the app and main.py)."""

import json
from datetime import date, time
from pathlib import Path

import pytest
from wcwidth import wcswidth

import ai_assistant as ai
import display
from pawpal_system import Owner, Pet, Priority, Task

TODAY = date(2026, 3, 2)
KB = ai.load_knowledge_base(Path(__file__).resolve().parent.parent / "knowledge")


def proposal(**changes) -> ai.ProposedTask:
    base = {"title": "Morning walk", "duration_minutes": 30, "priority": "high", "category": "walk",
            "timing": "preferred", "time": "07:30", "recurrence": "daily", "required": False,
            "description": "d", "rationale": "Dogs need exercise.", "source_ids": ["dog-exercise"],
            "confidence": 0.9}
    item = {**base, **changes}
    return ai.parse_proposals(json.dumps({"proposals": [item]}), {"dog-exercise", "dog-feeding"})[0][0]


def report_for(p, *extra_tasks):
    owner = Owner("J", day_start=time(7, 0), day_end=time(20, 0))
    pet = Pet("Rex", "dog", age=4)
    owner.add_pet(pet)
    for task in extra_tasks:
        pet.add_task(task)
    return ai.self_check(owner, pet, [ai.to_task(p, TODAY)], TODAY)


@pytest.mark.parametrize("value, badge", [
    (1.0, "🟢 100%"), (0.95, "🟢 95%"), (0.75, "🟢 75%"), (0.74, "🟡 74%"), (0.5, "🟡 50%"), (0.49, "🔴 49%"),
    (0.0, "🔴 0%"),
])
def test_confidence_badge_is_colour_coded(value, badge):
    assert display.confidence_badge(value) == badge


def test_proposal_text_matches_how_tasks_are_worded():
    p = proposal()
    task = ai.to_task(p, TODAY)
    assert display.proposal_label(p) == display.task_label(task)
    assert display.proposal_when(p) == display.when_text(task) == "prefers 07:30"
    assert display.proposal_repeats(p) == display.repeats_text(task) == "🔁 daily"
    assert display.proposal_priority(p) == display.priority_badge(Priority.HIGH)
    fixed = proposal(timing="fixed", time="08:15", recurrence="once", required=True)
    assert display.proposal_when(fixed) == "fixed 08:15" and display.proposal_repeats(fixed) == "once"
    assert display.proposal_priority(fixed).endswith("⭐")
    flexible = proposal(timing="flexible", time=None)
    assert display.proposal_when(flexible) == "any time"


def test_sources_show_the_guide_title_and_id():
    p = proposal(source_ids=["dog-exercise", "dog-feeding"])
    text = display.proposal_sources(p, KB)
    assert text == "Daily exercise for dogs [dog-exercise]; Feeding routine for dogs [dog-feeding]"
    assert display.proposal_sources(p, []) == "[dog-exercise]; [dog-feeding]"  # unknown guides: just the id


def test_fit_says_where_the_trial_schedule_put_the_task():
    p = proposal()
    assert display.proposal_fit(p, report_for(p)) == "✅ at 07:30"
    taken = Task("Breakfast", 60, priority=Priority.HIGH, fixed_time=time(7, 30), start_date=TODAY)
    assert display.proposal_fit(p, report_for(p, taken)) == "🕒 moved to 07:00"  # the fixed breakfast holds 07:30-08:30, so the walk takes the gap before it
    noon = Task("Lunch", 30, priority=Priority.HIGH, fixed_time=time(12, 0), start_date=TODAY)
    clash = proposal(title="Midday play", priority="low", timing="fixed", time="12:10")
    assert display.proposal_fit(clash, report_for(clash, noon)) == "⛔ would not fit"
    flexible = proposal(timing="flexible", time=None)
    assert display.proposal_fit(flexible, report_for(flexible)) == "✅ at 07:00"
    assert display.proposal_fit(p, None) == ""


def test_rows_are_numbered_and_carry_every_column():
    p, q = proposal(), proposal(title="Dinner walk", required=True)
    [row1, row2] = display.proposal_rows([p, q], KB, report_for(p))
    assert list(row1) == ["#", "Task", "Min", "Priority", "When", "Repeats", "Confidence", "Fit", "Sources"]
    assert (row1["#"], row2["#"]) == (1, 2)
    assert row1["Task"] == "🐕 Morning walk" and row1["Min"] == 30 and row1["Confidence"] == display.confidence_badge(p.confidence)
    assert row1["Sources"].startswith("Daily exercise for dogs") and row2["Priority"].endswith("⭐")
    assert row2["Fit"] == ""  # not in the trial plan's placements
    assert display.proposal_rows([], KB) == []


def test_the_rows_render_as_an_aligned_terminal_table():
    rows = display.proposal_rows([proposal(), proposal(title="Dinner walk", priority="low")], KB, report_for(proposal()))
    lines = display.render_table(rows, wrap_column="Sources", wrap_width=34).splitlines()
    assert len({wcswidth(line) for line in lines}) == 1  # emoji do not push columns out of line


def test_markdown_headline_and_detail_carry_what_the_user_needs_to_decide():
    report = report_for(proposal(required=True))
    p = ai.score_proposals([proposal(required=True)], KB, report)[0]
    headline = display.proposal_markdown(p)
    assert "**Morning walk**" in headline and ":red[**REQUIRED**]" in headline and "30 min" in headline
    assert "prefers 07:30" in headline and "🔁 daily" in headline
    assert ":red" not in display.proposal_markdown(proposal())
    detail = display.proposal_detail(p, KB, report)
    for part in ("Confidence", "guide support", "Dogs need exercise.", "Daily exercise for dogs [dog-exercise]",
                 "Schedule check: ✅ at 07:30", "Next free slot:"):
        assert part in detail
    assert "Schedule check" not in display.proposal_detail(p, KB, None)
    assert "()" not in display.proposal_detail(proposal(), KB, None)  # an unscored proposal has no breakdown to show


def test_problems_are_split_into_per_proposal_and_general_ones():
    noon = Task("Lunch", 30, priority=Priority.HIGH, fixed_time=time(12, 0), start_date=TODAY)
    clash = proposal(title="Midday play", priority="low", timing="fixed", time="12:10")
    report = report_for(clash, noon)
    mine = display.proposal_problems(clash, report)
    assert mine and all(line.startswith(display.CLASH_ICON) for line in mine) and "Midday play" in mine[0]
    assert display.proposal_problems(proposal(), report) == []
    assert display.proposal_problems(clash, None) == [] and display.general_problems(None) == []
    assert all(line.startswith(display.CLASH_ICON) for line in display.general_problems(report))


def test_every_new_symbol_is_two_cells_wide_so_tables_line_up():
    for symbol in (display.ASSISTANT_ICON, "🟢", "🟡", "🔴"):
        assert wcswidth(symbol) == 2
