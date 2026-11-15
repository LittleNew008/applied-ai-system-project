"""Tests of ai_assistant.py's error paths: the failures that should be handled, not crash the app.

Every one of these used to be unexercised. All run offline, with no network and no key."""

import json
import sys
import types
from datetime import date, time
from pathlib import Path

import pytest

import ai_assistant as ai
from pawpal_system import Owner, Pet, Scheduler, Task

TODAY = date(2026, 3, 2)
KB_DIR = Path(__file__).resolve().parent.parent / "knowledge"


def make_owner(**pet_kwargs):
    owner = Owner("Jordan", day_start=time(7, 0), day_end=time(20, 0))
    pet = Pet(**{"name": "Biscuit", "species": "dog", "age": 4, **pet_kwargs})
    owner.add_pet(pet)
    return owner, pet


def cfg(tmp_path, **changes):
    base = dict(offline=True, api_key=None, model="offline", kb_dir=KB_DIR, trace_path=tmp_path / "trace.jsonl")
    return ai.AssistantConfig(**{**base, **changes})


def steps(path):
    return [json.loads(line)["step"] for line in Path(path).read_text(encoding="utf-8").splitlines()]


# ------------------------------------------------------------------ SDK and client failures

def test_a_broken_sdk_lookup_counts_as_not_installed(monkeypatch):
    import importlib.util

    for error in (ImportError("no google"), ValueError("bad spec")):
        monkeypatch.setattr(importlib.util, "find_spec", lambda name, e=error: (_ for _ in ()).throw(e))
        assert ai._sdk_available() is False


def stub_sdk(monkeypatch, reply):
    """Install a fake google.genai whose model returns `reply` as the response text."""
    class Models:
        def generate_content(self, **kwargs):
            return types.SimpleNamespace(text=reply)

    class Client:
        def __init__(self, **kwargs):
            self.models = Models()

    google, genai = types.ModuleType("google"), types.ModuleType("google.genai")
    genai.Client = Client
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)


@pytest.mark.parametrize("reply", ["", "   \n", None, 42])
def test_an_empty_or_non_text_gemini_reply_is_an_error_not_a_silent_blank(monkeypatch, reply):
    stub_sdk(monkeypatch, reply)
    client = ai.GeminiClient(ai.AssistantConfig(offline=False, api_key="k", model="m"))
    with pytest.raises(RuntimeError, match="empty reply"):
        client.generate_json(system="s", prompt="p", schema={})


def test_an_empty_gemini_reply_makes_the_agent_fall_back_to_offline(monkeypatch, tmp_path):
    stub_sdk(monkeypatch, "")
    owner, pet = make_owner()
    config = cfg(tmp_path, offline=False, api_key="test-key-0123456789", model="m")
    result = ai.propose_care_plan(owner, pet, TODAY, config=config)
    assert result.fell_back and result.mode == "offline" and "empty reply" in result.fallback_reason
    assert result.accepted  # the offline assistant still answered


def test_a_gemini_client_that_cannot_start_means_offline_with_the_reason(monkeypatch, tmp_path):
    def broken(config):
        raise RuntimeError("sdk exploded")

    monkeypatch.setattr(ai, "GeminiClient", broken)
    owner, pet = make_owner()
    config = cfg(tmp_path, offline=False, api_key="test-key-0123456789", model="m")
    result = ai.propose_care_plan(owner, pet, TODAY, config=config)
    assert result.mode == "offline" and not result.fell_back and result.accepted
    first = json.loads((tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert first["step"] == "config" and "could not start Gemini: sdk exploded" in first["reason"]
    assert first["offline"] is True


def test_an_unexpected_failure_in_offline_mode_is_raised_not_hidden(monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise RuntimeError("a real bug")

    monkeypatch.setattr(ai, "retrieve", boom)
    owner, pet = make_owner()
    with pytest.raises(RuntimeError, match="a real bug"):  # there is nothing to fall back to, so do not mask it
        ai.propose_care_plan(owner, pet, TODAY, config=cfg(tmp_path))


# ------------------------------------------------------------------ the offline client's inputs

def test_the_offline_client_rejects_a_step_it_does_not_know_and_a_prompt_without_context():
    client = ai.OfflineClient()
    with pytest.raises(ValueError, match="does not know step 'dance'"):
        client.generate_json(system="", prompt=ai._build_prompt("x", {"step": "dance"}), schema={})
    with pytest.raises(ValueError, match="no CONTEXT_JSON"):
        client.generate_json(system="", prompt="just words", schema={})


def test_the_prompt_round_trips_its_context_and_takes_the_last_context_block():
    context = {"step": "needs", "pet": {"special_needs": "mentions CONTEXT_JSON: itself"}}
    prompt = ai._build_prompt("instruction", context)
    assert ai._context_of(prompt) == context
    assert prompt.rstrip().endswith("}") and prompt.index("CONTEXT_JSON:") < len(prompt)


# ------------------------------------------------------------------ malformed knowledge-base files

@pytest.mark.parametrize("header, fragment", [
    ("id: x\ntask: only | three | fields", "task line needs 9"),
    ("id: x\nthis line has no colon", "bad header line"),
    ("id: x\ntask: T | notanumber | low | walk | flexible | - | daily | false | d", "invalid literal"),
])
def test_a_malformed_knowledge_base_file_is_named_in_the_error(tmp_path, header, fragment):
    (tmp_path / "broken.md").write_text(f"---\n{header}\n---\nbody", encoding="utf-8")
    with pytest.raises(ValueError, match=fragment) as caught:
        ai.load_knowledge_base(tmp_path)
    if "invalid literal" not in fragment:
        assert "broken.md" in str(caught.value)


def test_a_broken_knowledge_base_is_logged_and_the_run_still_finishes_with_no_proposals(tmp_path):
    (tmp_path / "kb").mkdir()
    (tmp_path / "kb" / "broken.md").write_text("no header at all", encoding="utf-8")
    owner, pet = make_owner()
    config = cfg(tmp_path, kb_dir=tmp_path / "kb")
    result = ai.propose_care_plan(owner, pet, TODAY, config=config)
    assert result.retrieved == () and result.accepted == () and result.report is None
    assert "kb_error" in steps(config.trace_path)  # the problem is in the log, not swallowed silently


# ------------------------------------------------------------------ proposal shapes and the Task constructor

@pytest.mark.parametrize("item", ["a string", 42, None, ["a", "list"], True])
def test_a_proposal_that_is_not_an_object_is_rejected_with_a_reason(item):
    accepted, rejected = ai.parse_proposals(json.dumps({"proposals": [item]}), {"x"})
    assert accepted == [] and rejected[0].reason == "proposal is not an object" and rejected[0].raw == item


def test_a_proposal_the_task_constructor_refuses_is_rejected_but_the_others_are_kept(monkeypatch, tmp_path):
    owner, pet = make_owner()
    real = ai.to_task

    def picky(p, on):
        if p.title == "Evening walk":
            raise ValueError("task rejected by Task()")
        return real(p, on)

    monkeypatch.setattr(ai, "to_task", picky)
    result = ai.propose_care_plan(owner, pet, TODAY, config=cfg(tmp_path))
    titles = [p.title for p in result.accepted]
    assert "Evening walk" not in titles and "Morning walk" in titles
    assert any(r.reason == "task rejected by Task()" and r.raw.title == "Evening walk" for r in result.rejected)


# ------------------------------------------------------------------ self-check and scoring corners

def test_a_planner_overlap_is_reported_as_an_error_and_makes_the_check_fail(monkeypatch):
    owner, pet = make_owner()
    walk = Task("Walk", 20, start_date=TODAY)
    pet.add_task(Task("Other", 20, start_date=TODAY))
    real_plan = Scheduler.generate_plan

    def overlapping_pair(self, items):  # pretend the planner broke its own no-overlap rule
        plan = real_plan(self, TODAY)
        return [(plan.scheduled_items[0], plan.scheduled_items[-1])]

    monkeypatch.setattr(Scheduler, "detect_conflicts", overlapping_pair)
    report = ai.self_check(owner, pet, [walk], TODAY)
    invariant = [i for i in report.issues if i.code == "INVARIANT"]
    assert invariant and invariant[0].severity == "error" and "planner bug" in invariant[0].message
    assert report.ok is False


def test_with_no_schedule_check_a_proposals_fit_counts_as_full():
    p = ai.parse_proposals(json.dumps({"proposals": [{
        "title": "Walk", "duration_minutes": 10, "priority": "low", "category": "walk", "timing": "flexible",
        "time": None, "recurrence": "once", "required": False, "description": "", "rationale": "r",
        "source_ids": ["a"], "confidence": 0.8}]}), {"a"})[0][0]
    assert ai._schedule_fit(p, None) == 1.0
    [scored] = ai.score_proposals([p], [], None)
    assert "schedule fit 100%" in scored.confidence_detail and "guide support 40%" in scored.confidence_detail


@pytest.mark.parametrize("value, expected", [(None, ""), (42, ""), (["x"], ""), ("a\x00b\x1b[0m\n c", "a b [0m c"),
                                             ("  tidy   text  ", "tidy text")])
def test_clean_text_handles_anything_a_pet_field_might_hold(value, expected):
    assert ai.clean_text(value) == expected
