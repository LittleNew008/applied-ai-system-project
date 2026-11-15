"""Checks that model_card.md says only things that are true of the code and the recorded evidence.

The card's tables are filled from evidence/*.txt by scripts/sync_readme.py. These tests check the sections, that the
tables match the evidence, that the numbers in the prose match the tables, and that each limitation or safeguard the
card states is really how the code behaves.
"""

import importlib.util
import json
import re
import subprocess
import sys
from datetime import date, time
from pathlib import Path

import pytest

import ai_assistant as ai
from pawpal_system import Category, Owner, Pet, Task

ROOT = Path(__file__).resolve().parent.parent
CARD = ROOT / "model_card.md"
TODAY = date(2026, 3, 2)


def load_sync():
    spec = importlib.util.spec_from_file_location("sync_readme", ROOT / "scripts" / "sync_readme.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sync_readme = load_sync()


@pytest.fixture(scope="module")
def text() -> str:
    return CARD.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def kb():
    return ai.load_knowledge_base(ROOT / "knowledge")


def generated(text: str, name: str) -> str:
    match = re.search(rf"<!-- BEGIN generated:{name} -->\n(.*?)\n<!-- END generated:{name} -->", text, re.S)
    assert match, name
    return match.group(1)


def table_rows(block: str) -> list[list[str]]:
    rows = [[c.strip() for c in line.strip().strip("|").split("|")] for line in block.splitlines()]
    return rows[2:]  # drop the header and the separator


def section(text: str, title: str) -> str:
    match = re.search(rf"^## {re.escape(title)}.*?$(.*?)(?=^## |\Z)", text, re.S | re.M)
    assert match, title
    return match.group(1)


# ------------------------------------------------------------------ the labelled sections

def test_the_card_has_the_requested_sections_in_order(text):
    headings = re.findall(r"^## (.*)$", text, re.M)
    expected = ["At a glance", "1. System limitations and biases", "2. Misuse potential and prevention",
                "3. What surprised me in reliability testing", "4. AI collaboration",
                "5. Baseline versus specialized: the RAG comparison", "Reproducing this card"]
    assert headings == expected


def test_the_collaboration_section_has_one_accepted_and_one_revised_suggestion_each_with_a_reason(text):
    body = section(text, "4. AI collaboration")
    accepted = re.search(r"\*\*A suggestion I accepted:.*?\*\*(.*?)(?=\*\*A suggestion I revised)", body, re.S)
    revised = re.search(r"\*\*A suggestion I revised:.*?\*\*(.*?)(?=\*\*Smaller corrections)", body, re.S)
    assert accepted and revised
    for part in (accepted.group(1), revised.group(1)):
        assert "because" in part and len(part.split()) > 80
    assert "Claude Code" in body


def test_there_are_no_placeholders_and_every_link_exists(text):
    assert not re.search(r"TODO|TBD|lorem ipsum|XXX", text, re.I)
    links = set(re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", text))
    assert links and all((ROOT / l).exists() for l in links if not l.startswith("http"))


# ------------------------------------------------------------------ tables equal the evidence, prose equals the tables

def test_the_generated_tables_equal_the_recorded_evidence(text):
    assert sync_readme.sync(text, sync_readme.CARD_BLOCKS, "model_card.md") == text, "run: python scripts/sync_readme.py"
    assert len(re.findall(r"<!-- BEGIN generated:", text)) == len(sync_readme.CARD_BLOCKS)


def totals(text):
    rows = {r[0][:1]: r for r in table_rows(generated(text, "rag-totals"))}
    assert set(rows) == {"A", "B", "C"}
    return rows


def test_the_numbers_in_the_prose_are_the_numbers_in_the_rag_tables(text):
    rows = totals(text)
    a, b, c = rows["A"], rows["B"], rows["C"]
    # columns: condition, guides, proposed, well-formed, cited a real guide, accepted, copied, pets with accepted, calls
    assert (a[2], a[3], a[5]) == ("21", "21", "0") and a[4].startswith("0 ")
    assert c[2] == c[5] == "19" and c[4].startswith("19 ") and c[6].startswith("19 ") and c[7] == "4 of 4"
    assert b[7] == "1 of 4" and b[5] == "4"
    assert (a[8], b[8], c[8]) == ("12", "11", "8")
    prose = section(text, "5. Baseline versus specialized: the RAG comparison") + section(text, "3. What surprised me in reliability testing")
    for claim in ("21 well-formed proposals", "19 of 19", "4 of 4", "8 model calls instead of 11 or 12", "none of the 21",
                  "3 of 4 pets got nothing", "all 19 accepted proposals" if False else "19 of 19 accepted titles"):
        assert claim in prose, claim
    assert "all 19 accepted proposals had exactly the title" in section(text, "1. System limitations and biases")
    assert "1 of 4 to 4 of 4" in section(text, "4. AI collaboration")


def test_the_per_pet_table_shows_the_failure_the_card_describes(text):
    rows = table_rows(generated(text, "rag-per-pet"))
    assert len(rows) == 12
    by = {(r[0][:1], r[1]): r for r in rows}
    # columns: condition, pet, guides, proposed, cited real, accepted, still rejected, mean confidence, fell back
    assert by[("B", "Healthy adult dog, no tasks")][2] == "0" and by[("B", "Healthy adult dog, no tasks")][5] == "0"
    assert by[("B", "Cat with a partial routine")][5] != "0"  # the one pet where the first design worked
    for pet in ("Healthy adult dog, no tasks", "Cat with a partial routine", "Senior dog on medication",
                "Pet of a species with no guide"):
        assert by[("C", pet)][5] != "0" and by[("C", pet)][8] == "no"
    assert all(r[8] == "no" for r in rows)  # no run fell back to the offline model, so these are Gemini's own answers
    assert by[("C", "Pet of a species with no guide")][2] == "1"  # the only guide a pet of another species gets


def test_the_injection_table_supports_what_the_card_says_about_it(text):
    rows = {r[0]: r for r in table_rows(generated(text, "injection-table"))}
    # columns: placed in, stopped, in replies, in accepted, required accepted, accepted, rejected, fell back
    assert len(rows) == 4
    assert all(r[3] in ("no", "-") for r in rows.values())  # attack text never reached an accepted proposal
    long_breed = rows["Pet's breed, long (with a newline)"]
    assert long_breed[1].startswith("yes") and "longer than 60" in long_breed[1]
    assert "skip the vet" in rows["Pet's breed, short enough for the limit"][2]
    assert rows["Pet's special needs"][4] == "Give prescribed medication"
    assert rows["Title of an existing task"][4] == "none"
    body = section(text, "2. Misuse potential and prevention")
    assert "None of the attack text reached an accepted proposal" in body and "needs* reply" in body


# ------------------------------------------------------------------ the stated numbers match the suite and evaluation

def test_the_test_and_scenario_counts_in_the_card_are_current(text):
    for name in sync_readme.CARD_LIVE_BLOCKS:
        assert generated(text, name) == sync_readme.CARD_BLOCKS[name](), f"{name} is stale: run python scripts/sync_readme.py"
    run = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
                         capture_output=True, text=True, cwd=ROOT)
    total = int(re.search(r"(\d+) tests? collected", run.stdout).group(1))
    block = generated(text, "test-total")
    assert f"**{total} automated tests**" in block and "**8 of 8**" in block
    assert "8/8 scenarios passed" in (ROOT / "evaluation_log.txt").read_text(encoding="utf-8")


def test_the_scheduler_is_unchanged_since_the_commit_the_card_names(text):
    commit = re.search(r"no changes since commit `([0-9a-f]+)`", text).group(1)
    check = subprocess.run(["git", "cat-file", "-t", commit], capture_output=True, text=True, cwd=ROOT)
    if check.returncode != 0:
        pytest.skip("the named commit is not in this checkout")
    diff = subprocess.run(["git", "diff", commit, "--", "pawpal_system.py"], capture_output=True, text=True, cwd=ROOT)
    assert diff.stdout == ""


# ------------------------------------------------------------------ the limitations are really how the code behaves

def test_the_knowledge_base_is_ten_guides_with_species_guides_only_for_dogs_and_cats(kb):
    assert len(kb) == 10
    assert {t for c in kb for t in c.tags if t in ("dog", "cat", "bird", "rabbit", "fish", "reptile")} == {"dog", "cat"}


def test_a_pet_of_another_species_can_only_ever_get_the_two_general_guides(kb, text):
    owner = Owner("J")
    pet = Pet("Pip", "other")
    owner.add_pet(pet)
    focus = ai._context(owner, pet, TODAY)["focus_categories"]
    # With only the pet's own terms (what the offline assistant has) nothing is retrieved ...
    assert ai.retrieve(ai.retrieval_terms(pet, [], focus), kb, ai.RETRIEVE_K, species=pet.species) == []
    # ... a model's terms can reach the general vet guide ...
    hits = ai.retrieve(ai.retrieval_terms(pet, ["health check", "vet"], focus), kb, ai.RETRIEVE_K, species=pet.species)
    assert hits[0][0].id == "vet-basics" and {c.id for c, _ in hits} <= {"vet-basics", "medication-general"}
    # ... and whatever terms are used, no species-specific or life-stage guide can come back.
    everything = {w for c in kb for w in c.tags} | {"walk", "feeding", "senior", "young", "puppy", "kitten"}
    ids = {c.id for c, _ in ai.retrieve(sorted(everything), kb, 20, species="other")}
    assert ids <= {"vet-basics", "medication-general"}
    assert "`vet-basics` and `medication-general`" in text


@pytest.mark.parametrize("age, stage", [(None, None), (0, "young"), (1, "young"), (2, None), (7, None), (8, "senior"), (15, "senior")])
def test_life_stage_is_the_same_age_rule_for_every_species(age, stage):
    assert ai._age_stage(age) == stage


def test_medication_is_detected_only_from_a_short_keyword_list(text):
    owner = Owner("J")
    for needs, expect in (("daily pill", True), ("on insulin", True), ("prescribed tablets", True), ("epilepsy", False),
                          ("diabetes", False), ("seizures twice a year", False)):
        pet = Pet("P", "dog", age=4, special_needs=needs)
        owner.pets.clear()
        owner.add_pet(pet)
        assert ("meds" in ai._context(owner, pet, TODAY)["focus_categories"]) is expect, needs
    for word in ai._MEDS_WORDS:
        assert f'"{word}"' in text, word


def test_deduplication_misses_a_near_duplicate_with_a_different_title():
    pet = Pet("Mochi", "cat", age=2)
    pet.add_task(Task("Laser-pointer play", 20, category=Category.ENRICHMENT, start_date=TODAY,
                      recurrence=ai.Recurrence.DAILY))
    item = {"title": "Interactive play session", "duration_minutes": 15, "priority": "medium", "category": "enrichment",
            "timing": "preferred", "time": "19:00", "recurrence": "daily", "required": False, "description": "",
            "rationale": "r", "source_ids": ["cat-play"], "confidence": 0.9}
    proposed = ai.parse_proposals(json.dumps({"proposals": [item]}), {"cat-play"})[0]
    kept, rejected = ai.dedupe_against(pet, proposed)
    assert [p.title for p in kept] == ["Interactive play session"] and rejected == []  # a person must notice


def test_defaults_and_templates_assume_a_daytime_routine(text, kb):
    assert (Owner("x").day_start, Owner("x").day_end) == (time(8, 0), time(20, 0))
    times = {t["title"]: t["time"] for c in kb for t in c.templates}
    assert (times["Morning walk"], times["Evening walk"], times["Breakfast feeding"], times["Dinner feeding"]) == (
        "07:30", "18:00", "08:00", "17:30")
    assert "08:00 to 20:00" in text and "07:30 and 18:00" in text and "08:00 and 17:30" in text


def test_the_confidence_weights_and_offline_constants_are_as_described(text):
    assert ai.CONFIDENCE_WEIGHTS == {"model": 0.5, "grounding": 0.3, "fit": 0.2}
    assert "50% the model's own figure, 30% whether a cited guide covers" in text and "20% how the trial schedule" in text
    ctx = {"focus_categories": ["feeding", "grooming"], "existing_tasks": [], "pet": {"species": "dog"},
           "chunks": [{"id": "dog-feeding", "title": "t", "tags": ["dog", "feeding"],
                       "templates": [{"title": "Feed", "category": "feeding", "required": False, "description": "d"}]},
                      {"id": "vet-basics", "title": "t", "tags": ["vet"],
                       "templates": [{"title": "Brush", "category": "grooming", "required": False, "description": "d"}]}]}
    confidence = {p["source_ids"][0]: p["confidence"] for p in ai._offline_proposals(ctx)}
    assert confidence == {"dog-feeding": 0.9, "vet-basics": 0.75}
    assert "90% for a guide written for that species, 75% for a general guide" in text


def test_the_input_limits_and_the_prompt_rules_in_the_card_are_the_code(text):
    assert (ai.MAX_BREED, ai.MAX_SPECIAL_NEEDS) == (60, 300) and "breed 60, special needs 300" in text
    assert "dosing" in ai.SYSTEM_PROMPT.lower() and "never instructions" in ai.SYSTEM_PROMPT
    assert ai.CONTEXT_MARKER.strip() in ai.SYSTEM_PROMPT
    assert ai.AssistantConfig.__dataclass_fields__["timeout_s"].default == 20.0 and "20-second timeout" in text


def test_a_rejected_list_after_repair_shows_only_the_last_round(tmp_path):
    """The card admits this reporting limit: reasons from an earlier round are in the log, not in the result."""
    owner = Owner("J", day_start=time(7, 0), day_end=time(20, 0))
    pet = Pet("Rex", "dog", age=4)
    owner.add_pet(pet)
    bad = {"title": "Walk", "duration_minutes": 20, "priority": "high", "category": "walk", "timing": "flexible",
           "time": None, "recurrence": "daily", "required": False, "description": "", "rationale": "r",
           "source_ids": ["not-a-guide"], "confidence": 0.9}
    replies = [json.dumps({"needs": ["x"], "query_terms": ["dog"]}), json.dumps({"proposals": [bad]}),
               json.dumps({"proposals": []})]

    class Model:
        model = "scripted"

        def generate_json(self, *, system, prompt, schema):
            return replies.pop(0)

    config = ai.AssistantConfig(offline=True, api_key=None, model="m", trace_path=tmp_path / "t.jsonl")
    result = ai.propose_care_plan(owner, pet, TODAY, config=config, client=Model())
    assert result.accepted == () and result.rejected == ()  # the first round's rejection is gone from the result
    log = ai.read_trace(config.trace_path)
    assert any(r["step"] == "validate" and r["rejected"] for r in log)  # but it is in the log


# ------------------------------------------------------------------ the call bound, exercised for real

def test_one_click_makes_at_most_six_model_calls_even_when_every_step_needs_a_retry(tmp_path):
    good_item = {"title": "Morning walk", "duration_minutes": 30, "priority": "high", "category": "walk",
                 "timing": "preferred", "time": "07:30", "recurrence": "daily", "required": False, "description": "d",
                 "rationale": "r", "source_ids": ["dog-exercise"], "confidence": 0.9}
    malformed = {**good_item, "title": "Evening walk", "priority": "urgent"}  # forces a repair round
    needs = json.dumps({"needs": ["x"], "query_terms": ["dog", "walk"]})
    replies = [
        "garbage", needs,                                                              # needs: retried once
        "garbage", json.dumps({"proposals": [good_item, malformed]}),                  # proposals: retried once
        "garbage", json.dumps({"proposals": [good_item, {**good_item, "title": "Evening walk"}]}),  # repair: retried once
    ]
    calls = []

    class Model:
        model = "scripted"

        def generate_json(self, *, system, prompt, schema):
            calls.append(ai._context_of(prompt)["step"])
            return replies.pop(0)  # a seventh call would raise IndexError, and the agent would then fall back

    owner = Owner("J", day_start=time(7, 0), day_end=time(20, 0))
    pet = Pet("Rex", "dog", age=4)
    owner.add_pet(pet)
    config = ai.AssistantConfig(offline=True, api_key=None, model="m", trace_path=tmp_path / "t.jsonl")
    result = ai.propose_care_plan(owner, pet, TODAY, config=config, client=Model())
    assert calls == ["needs", "needs", "proposals", "proposals", "repair", "repair"]
    assert not result.fell_back and [p.title for p in result.accepted] == ["Morning walk", "Evening walk"]
    assert replies == []  # all six were used, none more
    assert "at most 6 model calls per click" in CARD.read_text(encoding="utf-8")


def test_when_the_model_keeps_failing_the_calls_stop_and_the_offline_stand_in_answers(tmp_path):
    calls = []

    class Broken:
        model = "scripted"

        def generate_json(self, *, system, prompt, schema):
            calls.append(1)
            return "garbage"

    owner = Owner("J", day_start=time(7, 0), day_end=time(20, 0))
    pet = Pet("Rex", "dog", age=4)
    owner.add_pet(pet)
    config = ai.AssistantConfig(offline=True, api_key=None, model="m", trace_path=tmp_path / "t.jsonl")
    result = ai.propose_care_plan(owner, pet, TODAY, config=config, client=Broken())
    assert len(calls) == 2 and result.fell_back and result.accepted  # one retry, then the offline stand-in


def test_the_age_rule_the_card_states_is_the_age_rule_in_the_code(text):
    young, senior = re.search(r'aged (\d+) or under is "young" and (\d+) or over is "senior"', text).groups()
    young, senior = int(young), int(senior)
    assert ai._age_stage(young) == "young" and ai._age_stage(young + 1) is None
    assert ai._age_stage(senior) == "senior" and ai._age_stage(senior - 1) is None
