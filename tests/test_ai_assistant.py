"""Tests of the Care Assistant (ai_assistant.py). Everything runs offline: no network, no API key."""

import json
import sys
import types
from datetime import date, time
from pathlib import Path

import pytest

import ai_assistant as ai
from pawpal_system import Category, Owner, Pet, Priority, Recurrence, Task

TODAY = date(2026, 3, 2)
KB_DIR = Path(__file__).resolve().parent.parent / "knowledge"


def mk(title: str, minutes: int, **kwargs) -> Task:
    """A task due on TODAY (a Task defaults to starting on the real today, which would make tests date-dependent)."""
    return Task(title, minutes, start_date=TODAY, **kwargs)


def make_owner(**pet_kwargs) -> tuple[Owner, Pet]:
    owner = Owner("Jordan", day_start=time(7, 0), day_end=time(20, 0))
    pet = Pet(**{"name": "Biscuit", "species": "dog", "age": 4, **pet_kwargs})
    owner.add_pet(pet)
    return owner, pet


def config(tmp_path, **changes) -> ai.AssistantConfig:
    return ai.AssistantConfig(offline=True, api_key=None, model="offline", kb_dir=KB_DIR,
                              trace_path=tmp_path / "trace.jsonl", **changes)


def trace_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def proposal(**changes) -> dict:
    base = {"title": "Morning walk", "duration_minutes": 30, "priority": "high", "category": "walk",
            "timing": "preferred", "time": "07:30", "recurrence": "daily", "required": False,
            "description": "A brisk walk.", "rationale": "Dogs need exercise.", "source_ids": ["dog-exercise"],
            "confidence": 0.9}
    return {**base, **changes}


def payload(*items) -> str:
    return json.dumps({"proposals": list(items)})


class ScriptedClient:
    """A fake model that returns canned replies in order (and records the steps it was asked)."""

    model = "fake"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.steps = []

    def generate_json(self, *, system, prompt, schema):
        self.steps.append(ai._context_of(prompt)["step"])
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


NEEDS = json.dumps({"needs": ["Exercise"], "query_terms": ["dog", "walk"]})


# ------------------------------------------------------------------ config

def test_config_is_offline_when_forced_or_without_a_key():
    assert ai.AssistantConfig.from_env({"PAWPAL_OFFLINE": "1", "GEMINI_API_KEY": "k"}).offline
    no_key = ai.AssistantConfig.from_env({})
    assert no_key.offline and "GEMINI_API_KEY" in no_key.offline_reason


def test_config_reads_model_and_trace_path_from_the_environment(monkeypatch):
    monkeypatch.setattr(ai, "_sdk_available", lambda: True)
    cfg = ai.AssistantConfig.from_env({"GEMINI_API_KEY": " secret ", "PAWPAL_GEMINI_MODEL": "m-1",
                                       "PAWPAL_TRACE_FILE": "/tmp/x.jsonl"})
    assert (cfg.offline, cfg.api_key, cfg.model, cfg.trace_path) == (False, "secret", "m-1", Path("/tmp/x.jsonl"))
    assert ai.AssistantConfig.from_env({"GEMINI_API_KEY": "k"}).model == ai.DEFAULT_MODEL


def test_config_is_offline_when_the_sdk_is_missing(monkeypatch):
    monkeypatch.setattr(ai, "_sdk_available", lambda: False)
    cfg = ai.AssistantConfig.from_env({"GEMINI_API_KEY": "k"})
    assert cfg.offline and "google-genai" in cfg.offline_reason


def test_gemini_client_uses_a_stubbed_sdk_without_network(monkeypatch):
    calls = {}

    class FakeModels:
        def generate_content(self, **kwargs):
            calls.update(kwargs)
            return types.SimpleNamespace(text='{"proposals": []}')

    class FakeClient:
        def __init__(self, **kwargs):
            calls["client_kwargs"] = kwargs
            self.models = FakeModels()

    google = types.ModuleType("google")
    genai = types.ModuleType("google.genai")
    genai.Client = FakeClient
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)

    client = ai.GeminiClient(ai.AssistantConfig(offline=False, api_key="k", model="m-1", timeout_s=5))
    assert client.generate_json(system="s", prompt="p", schema={"type": "object"}) == '{"proposals": []}'
    assert calls["client_kwargs"] == {"api_key": "k", "http_options": {"timeout": 5000}}
    assert calls["model"] == "m-1"
    assert calls["config"]["response_mime_type"] == "application/json"
    assert calls["config"]["temperature"] == 0


# ------------------------------------------------------------------ knowledge base

def test_the_shipped_knowledge_base_loads_and_every_template_is_valid():
    chunks = ai.load_knowledge_base(KB_DIR)
    assert len({c.id for c in chunks}) == len(chunks) >= 8
    ids = {c.id for c in chunks}
    for chunk in chunks:
        for template in chunk.templates:
            item = {**template, "rationale": "r", "source_ids": [chunk.id], "confidence": 0.8}
            ai._check_item(item, ids)  # raises if a template breaks the proposal rules


def test_load_knowledge_base_rejects_a_file_without_a_header(tmp_path):
    (tmp_path / "bad.md").write_text("no header here", encoding="utf-8")
    with pytest.raises(ValueError, match="bad.md"):
        ai.load_knowledge_base(tmp_path)
    assert ai.load_knowledge_base(tmp_path / "missing") == []


def test_retrieve_ranks_by_tags_and_never_returns_another_species():
    kb = ai.load_knowledge_base(KB_DIR)
    hits = ai.retrieve(["dog", "walk", "feeding"], kb, k=3, species="dog")
    assert [c.id for c, _ in hits][:2] == ["dog-exercise", "dog-feeding"]
    assert hits == ai.retrieve(["dog", "walk", "feeding"], kb, k=3, species="dog")  # deterministic
    assert not any(c.id.startswith("cat-") for c, _ in ai.retrieve(["cat", "feeding"], kb, species="dog", k=10))
    assert "senior-care" not in [c.id for c, _ in ai.retrieve(["dog", "walk"], kb, k=10)]  # stage not asked for
    assert "senior-care" in [c.id for c, _ in ai.retrieve(["dog", "walk", "senior"], kb, k=10)]
    assert ai.retrieve(["zzz"], kb) == []


# ------------------------------------------------------------------ strict proposal parsing

def test_parse_accepts_a_valid_reply():
    accepted, rejected = ai.parse_proposals(payload(proposal()), {"dog-exercise"})
    assert rejected == [] and accepted[0].title == "Morning walk" and accepted[0].source_ids == ("dog-exercise",)


@pytest.mark.parametrize("raw", [
    "not json",
    "```json\n" + payload(proposal()) + "\n```",
    "Here you go: " + payload(proposal()),
    "[]",
    json.dumps({"proposals": "nope"}),
    json.dumps({"proposals": [], "extra": 1}),
])
def test_parse_rejects_a_reply_that_is_not_exactly_the_proposals_object(raw):
    with pytest.raises(ai.ProposalError):
        ai.parse_proposals(raw, {"dog-exercise"})


@pytest.mark.parametrize("change, fragment", [
    ({"title": "  "}, "title"),
    ({"duration_minutes": 0}, "duration"),
    ({"duration_minutes": 241}, "duration"),
    ({"duration_minutes": True}, "duration"),
    ({"duration_minutes": "30"}, "duration"),
    ({"priority": "urgent"}, "priority"),
    ({"category": "swim"}, "category"),
    ({"timing": "whenever"}, "timing"),
    ({"timing": "fixed", "time": None}, "required"),
    ({"timing": "fixed", "time": "25:00"}, "HH:MM"),
    ({"timing": "flexible", "time": "07:30"}, "null"),
    ({"recurrence": "monthly"}, "recurrence"),
    ({"required": "yes"}, "required"),
    ({"rationale": ""}, "rationale"),
    ({"source_ids": []}, "source_ids"),
    ({"source_ids": ["made-up-id"]}, "not among"),
])
def test_parse_turns_a_bad_item_into_a_rejection_and_keeps_the_good_one(change, fragment):
    accepted, rejected = ai.parse_proposals(payload(proposal(**change), proposal(title="Dinner")), {"dog-exercise"})
    assert [p.title for p in accepted] == ["Dinner"]
    assert len(rejected) == 1 and rejected[0].index == 0 and fragment in rejected[0].reason


def test_parse_rejects_unknown_and_missing_keys():
    extra = {**proposal(), "id": "abc"}
    missing = {k: v for k, v in proposal().items() if k != "rationale"}
    accepted, rejected = ai.parse_proposals(payload(extra, missing), {"dog-exercise"})
    assert accepted == [] and "unexpected keys" in rejected[0].reason and "missing keys" in rejected[1].reason


def test_to_task_maps_timing_and_builds_a_valid_task():
    p = ai.parse_proposals(payload(proposal(timing="fixed", time="08:15", required=True)), {"dog-exercise"})[0][0]
    task = ai.to_task(p, TODAY)
    assert (task.fixed_time, task.preferred_start) == (time(8, 15), None)
    assert (task.priority, task.category, task.recurrence) == (Priority.HIGH, Category.WALK, Recurrence.DAILY)
    assert task.required and task.start_date == TODAY
    assert ai.to_task(p, TODAY).id != task.id  # the model never supplies ids


def test_dedupe_drops_repeats_of_open_tasks_but_not_completed_ones():
    _, pet = make_owner()
    pet.add_task(mk("morning walk", 20, category=Category.WALK, recurrence=Recurrence.DAILY))
    done = mk("Dinner", 5, category=Category.WALK, recurrence=Recurrence.DAILY)
    done.completed_on = TODAY
    pet.add_task(done)
    parsed = ai.parse_proposals(payload(proposal(), proposal(title="Dinner"), proposal(title="Dinner")),
                                {"dog-exercise"})[0]
    kept, rejected = ai.dedupe_against(pet, parsed)
    assert [p.title for p in kept] == ["Dinner"] and len(rejected) == 2


# ------------------------------------------------------------------ offline client

def test_offline_client_is_deterministic_and_its_proposals_pass_strict_parsing(tmp_path):
    owner, pet = make_owner()
    first = ai.propose_care_plan(owner, pet, TODAY, config=config(tmp_path))
    second = ai.propose_care_plan(owner, pet, TODAY, config=config(tmp_path))
    assert first.accepted == second.accepted and first.needs == second.needs
    assert first.mode == "offline" and not first.fell_back and first.rejected == ()
    titles = [p.title for p in first.accepted]
    assert "Morning walk" in titles and "Breakfast feeding" in titles
    assert all(p.source_ids for p in first.accepted)
    assert not any("cat" in c.tags and "dog" not in c.tags for c in first.retrieved)


def test_offline_assistant_skips_what_the_pet_already_has_and_follows_the_species(tmp_path):
    owner, pet = make_owner(name="Mochi", species="cat")
    pet.add_task(mk("Morning feeding", 5, category=Category.FEEDING))
    result = ai.propose_care_plan(owner, pet, TODAY, config=config(tmp_path))
    titles = [p.title for p in result.accepted]
    assert "Morning feeding" not in titles and "Interactive play session" in titles
    assert all(c.id.startswith(("cat-", "senior", "young", "medication")) for c in result.retrieved)


def test_special_needs_medication_brings_in_a_required_medication_proposal(tmp_path):
    owner, pet = make_owner(special_needs="needs a daily pill for arthritis")
    result = ai.propose_care_plan(owner, pet, TODAY, config=config(tmp_path))
    meds = [p for p in result.accepted if p.category == "meds"]
    assert meds and meds[0].required


def test_a_pet_that_already_covers_every_category_gets_no_proposals(tmp_path):
    owner, pet = make_owner()
    for category in (Category.WALK, Category.FEEDING, Category.ENRICHMENT, Category.GROOMING):
        pet.add_task(Task(f"Existing {category.value}", 10, category=category))
    result = ai.propose_care_plan(owner, pet, TODAY, config=config(tmp_path))
    assert result.accepted == () and result.report is None


# ------------------------------------------------------------------ self-check

def test_self_check_leaves_the_live_owner_untouched():
    owner, pet = make_owner()
    before = owner.to_dict()
    report = ai.self_check(owner, pet, [mk("Walk", 20, category=Category.WALK)], TODAY)
    assert owner.to_dict() == before and report.ok
    assert report.suggested_slots["Walk"] is not None


def test_self_check_reports_a_clashing_fixed_time_candidate_as_newly_skipped():
    owner, pet = make_owner()
    pet.add_task(mk("Lunch", 30, priority=Priority.HIGH, fixed_time=time(12, 0)))
    clash = mk("Midday play", 30, priority=Priority.LOW, fixed_time=time(12, 15))
    report = ai.self_check(owner, pet, [clash], TODAY)
    codes = [(i.code, i.severity) for i in report.issues]
    assert ("NEWLY_SKIPPED", "warn") in codes and ("CLASH", "warn") in codes and report.ok
    assert report.suggested_slots["Midday play"][0] >= time(12, 15)


def test_self_check_makes_a_skipped_required_candidate_an_error():
    owner, pet = make_owner()
    pet.add_task(mk("Lunch", 30, priority=Priority.HIGH, required=True, fixed_time=time(12, 0)))
    clash = mk("Meds", 10, priority=Priority.LOW, required=True, fixed_time=time(12, 10))
    report = ai.self_check(owner, pet, [clash], TODAY)
    assert not report.ok and any(i.code == "NEWLY_SKIPPED" and i.severity == "error" for i in report.issues)


def test_self_check_makes_a_displaced_required_task_an_error():
    owner, pet = make_owner()
    pet.add_task(mk("Lunch", 30, priority=Priority.HIGH, required=True, fixed_time=time(12, 0)))
    shorter_and_required = mk("Meds", 10, priority=Priority.HIGH, required=True, fixed_time=time(12, 10))
    report = ai.self_check(owner, pet, [shorter_and_required], TODAY)  # the planner prefers the shorter task
    assert not report.ok and any(i.code == "DISPLACED" and i.severity == "error" for i in report.issues)


def test_self_check_flags_a_day_pushed_over_its_limit_and_displaced_tasks():
    owner, pet = make_owner()
    owner.set_availability(time(7, 0), time(20, 0), available_minutes=40)
    pet.add_task(mk("Existing", 30, priority=Priority.LOW))
    big = mk("Long walk", 30, priority=Priority.HIGH)
    report = ai.self_check(owner, pet, [big], TODAY)
    codes = {i.code for i in report.issues}
    assert {"OVER_CAP", "DISPLACED"} <= codes


# ------------------------------------------------------------------ agent loop, fallback, tracing

def test_trace_file_records_each_step_in_order_with_no_secret(tmp_path):
    owner, pet = make_owner()
    cfg = config(tmp_path, max_repairs=0)
    ai.propose_care_plan(owner, pet, TODAY, config=cfg)
    steps = [line["step"] for line in trace_lines(cfg.trace_path)]
    assert steps == ["config", "input_check", "llm_call", "plan_needs", "retrieve", "llm_call", "parse", "validate",
                     "self_check", "score", "result"]
    assert len({line["trace_id"] for line in trace_lines(cfg.trace_path)}) == 1
    llm = [line for line in trace_lines(cfg.trace_path) if line["step"] == "llm_call"]
    assert all({"prompt_sha256", "response_sha256", "duration_ms", "model"} <= set(line) for line in llm)


def test_a_tracer_scrubs_secrets_and_survives_an_unwritable_path(tmp_path):
    tracer = ai.Tracer(tmp_path / "t.jsonl", secrets=("sk-12345",))
    tracer.step("x", note="the key is sk-12345")
    assert "sk-12345" not in (tmp_path / "t.jsonl").read_text()
    blocked = tmp_path / "file"
    blocked.write_text("x")
    ai.Tracer(blocked / "inside.jsonl").step("x")  # parent is a file: must not raise


def test_invalid_json_is_retried_once_with_the_error_then_accepted(tmp_path):
    owner, pet = make_owner()
    client = ScriptedClient(NEEDS, "```not json```", payload(proposal()))
    cfg = config(tmp_path, max_repairs=0)
    result = ai.propose_care_plan(owner, pet, TODAY, config=cfg, client=client)
    assert client.steps == ["needs", "proposals", "proposals"]
    assert result.mode == "gemini" and not result.fell_back
    assert [p.title for p in result.accepted] == ["Morning walk"]
    assert "parse_error" in [line["step"] for line in trace_lines(cfg.trace_path)]


def test_persistent_invalid_json_falls_back_to_offline(tmp_path):
    owner, pet = make_owner()
    client = ScriptedClient(NEEDS, "nope", "still nope")
    cfg = config(tmp_path)
    result = ai.propose_care_plan(owner, pet, TODAY, config=cfg, client=client)
    assert result.fell_back and result.mode == "offline" and result.accepted
    assert "ProposalError" in result.fallback_reason
    lines = trace_lines(cfg.trace_path)
    assert "fallback" in [line["step"] for line in lines]
    assert lines[-1]["mode"] == "offline"


def test_a_network_error_falls_back_to_offline(tmp_path):
    owner, pet = make_owner()
    result = ai.propose_care_plan(owner, pet, TODAY, config=config(tmp_path),
                                  client=ScriptedClient(TimeoutError("boom")))
    assert result.fell_back and "boom" in result.fallback_reason and result.accepted


def test_a_model_citing_an_unknown_source_is_rejected_per_item(tmp_path):
    owner, pet = make_owner()
    client = ScriptedClient(NEEDS, payload(proposal(), proposal(title="Dinner", source_ids=["invented"])))
    result = ai.propose_care_plan(owner, pet, TODAY, config=config(tmp_path, max_repairs=0), client=client)
    assert [p.title for p in result.accepted] == ["Morning walk"]
    assert "not among" in result.rejected[0].reason


def test_proposals_beyond_the_limit_are_rejected(tmp_path):
    owner, pet = make_owner()
    items = [proposal(title=f"Walk {i}") for i in range(5)]
    result = ai.propose_care_plan(owner, pet, TODAY, config=config(tmp_path, max_proposals=2, max_repairs=0),
                                  client=ScriptedClient(NEEDS, payload(*items)))
    assert len(result.accepted) == 2 and sum("over the limit" in r.reason for r in result.rejected) == 3


def test_the_self_check_problems_are_fed_back_for_one_repair(tmp_path):
    owner, pet = make_owner()
    pet.add_task(mk("Lunch", 30, priority=Priority.HIGH, required=True, fixed_time=time(12, 0)))
    clash = proposal(title="Meds", category="meds", timing="fixed", time="12:10", required=True, priority="high")
    fixed = proposal(title="Meds", category="meds", timing="fixed", time="13:00", required=True, priority="high")
    client = ScriptedClient(NEEDS, payload(clash), payload(fixed))
    result = ai.propose_care_plan(owner, pet, TODAY, config=config(tmp_path), client=client)
    assert client.steps == ["needs", "proposals", "repair"]
    assert result.report.ok and result.accepted[0].time == "13:00"


def test_offline_repair_drops_proposals_that_the_daily_limit_would_skip(tmp_path):
    owner, pet = make_owner()
    owner.set_availability(time(7, 0), time(20, 0), available_minutes=40)
    cfg = config(tmp_path)
    result = ai.propose_care_plan(owner, pet, TODAY, config=cfg)
    assert result.report.ok and not [i for i in result.report.issues if i.severity != "info"]
    assert sum(p.duration_minutes for p in result.accepted) <= 40
    steps = [line["step"] for line in trace_lines(cfg.trace_path)]
    assert "repair" in steps and steps.index("self_check") < steps.index("repair")


def test_the_live_owner_is_unchanged_by_proposing(tmp_path):
    owner, pet = make_owner()
    before = owner.to_dict()
    ai.propose_care_plan(owner, pet, TODAY, config=config(tmp_path))
    assert owner.to_dict() == before


def test_propose_uses_the_environment_config_in_offline_mode():
    owner, pet = make_owner()
    result = ai.propose_care_plan(owner, pet, TODAY)  # conftest forces PAWPAL_OFFLINE=1 and a temp trace file
    assert result.mode == "offline" and result.accepted


# ------------------------------------------------------------------ commit

def test_commit_adds_only_the_accepted_titles_through_pet_add_task(tmp_path):
    owner, pet = make_owner()
    result = ai.propose_care_plan(owner, pet, TODAY, config=config(tmp_path))
    created = ai.commit(owner, pet, result, ["morning walk", "No such task"], TODAY)
    assert [t.title for t in created] == ["Morning walk"]
    assert [t.title for t in pet.tasks] == ["Morning walk"]
    assert len({t.id for t in pet.tasks}) == len(pet.tasks)
    assert Owner.from_dict(owner.to_dict()) == owner  # still serialises cleanly
    assert ai.commit(owner, pet, result, ["Morning walk"], TODAY) == []  # now a duplicate: skipped


def test_commit_needs_required_proposals_to_be_listed_explicitly(tmp_path):
    owner, pet = make_owner(special_needs="daily medication")
    result = ai.propose_care_plan(owner, pet, TODAY, config=config(tmp_path))
    ai.commit(owner, pet, result, ["Morning walk"], TODAY)
    assert not any(t.required for t in pet.tasks)
    ai.commit(owner, pet, result, ["Give prescribed medication"], TODAY)
    assert any(t.required for t in pet.tasks)


def test_commit_rejects_a_pet_from_another_owner(tmp_path):
    owner, pet = make_owner()
    stranger = Pet("Rex", "dog")
    result = ai.propose_care_plan(owner, pet, TODAY, config=config(tmp_path))
    with pytest.raises(ValueError):
        ai.commit(owner, stranger, result, ["Morning walk"], TODAY)
