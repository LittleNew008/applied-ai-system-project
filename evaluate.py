"""Evaluate the Care Assistant on predefined pet scenarios and print a pass/fail summary.

Each scenario builds an owner and pet, runs `ai_assistant.propose_care_plan`, then checks the outcome:
every scenario gets the same general checks (the raw replies match the JSON schema, every proposal is
grounded in a retrieved guide, no other species' guide is cited, confidences are in range, no unresolved
schedule error, the live owner is untouched) plus checks of its own. Three scenarios are edge cases: a pet
with nothing to go on, a required task that cannot be scheduled, and a day with too little capacity; two more
test the guardrails (a model that answers badly, and a pet whose data is invalid).

By default it runs offline, so the results are deterministic and need no key or network. `--online` uses the
configured Gemini model for the scenarios that do not script their own model (results can then vary run to
run). The two scenarios that need a particular model behaviour always use a scripted model.

    python evaluate.py                    # run, print the summary, write evaluation_log.txt
    python evaluate.py --log run.txt      # write the log somewhere else
    python evaluate.py --online           # use Gemini where a key is configured

The exit status is 0 when every scenario passes and 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from pathlib import Path

import jsonschema

import ai_assistant as ai
import display
from pawpal_system import Category, Owner, Pet, Priority, Task
from datetime import time

PLAN_DATE = date(2026, 3, 2)  # fixed, so tasks are due and results repeat
PASS_ICON, FAIL_ICON = "✅", "❌"
DEFAULT_LOG = Path(__file__).resolve().parent / "evaluation_log.txt"
HIGH_CONFIDENCE = 0.75


# ------------------------------------------------------------------ scenarios

@dataclass
class Setup:
    owner: Owner
    pet: Pet
    client: ai.LLMClient | None = None  # a scripted model, or None for the configured one
    config_changes: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Run:
    key: str
    result: ai.AssistantResult | None
    error: Exception | None
    records: list[dict]
    owner_untouched: bool
    schema_valid: int = 0
    schema_total: int = 0
    last_reply_valid: bool | None = None
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(c.passed for c in self.checks)


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    description: str
    expectation: str
    setup: Callable[[], Setup]
    checks: Callable[[Run], list[Check]]
    edge_case: bool = False


class ScriptedModel:
    """A fake model that answers with canned replies in order, so a scenario can force one behaviour."""

    model = "scripted"

    def __init__(self, *replies: str):
        self.replies = list(replies)

    def generate_json(self, *, system: str, prompt: str, schema: dict) -> str:
        return self.replies.pop(0)


def _owner(pet: Pet, **availability) -> Owner:
    owner = Owner("Jordan", day_start=time(7, 0), day_end=time(20, 0), **availability)
    owner.add_pet(pet)
    return owner


def _task(title: str, minutes: int, **kwargs) -> Task:
    return Task(title, minutes, start_date=PLAN_DATE, **kwargs)


def _accepted(run: Run) -> Sequence[ai.ProposedTask]:
    return run.result.accepted if run.result else ()


def _titles(run: Run) -> list[str]:
    return [p.title for p in _accepted(run)]


def _steps(run: Run) -> list[str]:
    return [r["step"] for r in run.records]


def _self_check_problems(run: Run) -> list[list]:
    """The issues found by each self-check in the run, in order (errors and warnings only)."""
    return [[i for i in r["issues"] if i[0] != "info"] for r in run.records if r["step"] == "self_check"]


def _meds_proposal(**changes) -> dict:
    item = {"title": "Give prescribed medication", "duration_minutes": 5, "priority": "high", "category": "meds",
            "timing": "fixed", "time": "08:00", "recurrence": "daily", "required": True,
            "description": "Give the dose exactly as prescribed.", "rationale": "Medication must be on time.",
            "source_ids": ["medication-general"], "confidence": 0.9}
    return {**item, **changes}


NEEDS_FOR_MEDS = json.dumps({"needs": ["Medication on time"], "query_terms": ["dog", "meds", "medication"]})


def healthy_dog() -> Setup:
    pet = Pet("Biscuit", "dog", breed="Labrador", age=4)
    return Setup(_owner(pet), pet)


def healthy_dog_checks(run: Run) -> list[Check]:
    categories = {p.category for p in _accepted(run)}
    needed = {"walk", "feeding", "enrichment", "grooming"}
    return [
        Check("covers walk, feeding, play and grooming", needed <= categories, f"got {sorted(categories)}"),
        Check("all proposals are high confidence (75%+)",
              all(p.confidence >= HIGH_CONFIDENCE for p in _accepted(run)),
              f"lowest {min((p.confidence for p in _accepted(run)), default=0):.0%}"),
        Check("no required task is invented", not any(p.required for p in _accepted(run))),
    ]


def partial_cat() -> Setup:
    pet = Pet("Mochi", "cat", age=3)
    pet.add_task(_task("Morning feeding", 5, category=Category.FEEDING, priority=Priority.HIGH))
    return Setup(_owner(pet), pet)


def partial_cat_checks(run: Run) -> list[Check]:
    return [
        Check("does not repeat the existing feeding task", "Morning feeding" not in _titles(run)),
        Check("suggests play and grooming instead",
              {"Interactive play session", "Brush fur"} <= set(_titles(run)), f"got {_titles(run)}"),
        Check("only cat-appropriate guides are cited",
              all(s.startswith("cat-") for p in _accepted(run) for s in p.source_ids)),
    ]


def senior_dog_on_medication() -> Setup:
    pet = Pet("Rex", "dog", breed="Beagle", age=11, special_needs="daily arthritis medication")
    return Setup(_owner(pet), pet)


def senior_dog_checks(run: Run) -> list[Check]:
    meds = [p for p in _accepted(run) if p.category == "meds"]
    return [
        Check("proposes the medication task, marked required", bool(meds) and all(p.required for p in meds),
              f"got {[(p.title, p.required) for p in meds]}"),
        Check("lists the required task first", bool(_accepted(run)) and _accepted(run)[0].required),
        Check("brings in the senior-care guide", "senior-care" in {c.id for c in run.result.retrieved}),
    ]


def bare_minimum_pet() -> Setup:
    pet = Pet("Pip", "other")  # no breed, age, special needs or tasks, and a species with no guide of its own
    return Setup(_owner(pet), pet)


def bare_minimum_checks(run: Run) -> list[Check]:
    cited = {s for p in _accepted(run) for s in p.source_ids}
    return [
        Check("finishes without error", run.result is not None),
        Check("does not borrow the dog or cat guides", not any(s.startswith(("dog-", "cat-")) for s in cited),
              f"cited {sorted(cited)}"),
        Check("proposes nothing it cannot ground", all(p.source_ids for p in _accepted(run)),
              f"{len(_accepted(run))} proposal(s)"),
    ]


def over_capacity_day() -> Setup:
    pet = Pet("Biscuit", "dog", age=4)
    return Setup(_owner(pet, available_minutes=20), pet)  # the guides propose far more than 20 minutes


def over_capacity_checks(run: Run) -> list[Check]:
    problems = _self_check_problems(run)
    minutes = sum(p.duration_minutes for p in _accepted(run))
    final = run.result.report
    return [
        Check("the first schedule check found the day too full", bool(problems) and bool(problems[0]),
              f"{len(problems[0]) if problems else 0} issue(s)"),
        Check("a repair round was run", "repair" in _steps(run)),
        Check("the final proposals fit the 20-minute limit", 0 < minutes <= 20, f"{minutes} min"),
        Check("the final schedule check has no warnings or errors",
              final is not None and not [i for i in final.issues if i.severity != "info"]),
    ]


def impossible_required_task() -> Setup:
    pet = Pet("Rex", "dog", age=4, special_needs="daily medication")
    impossible = json.dumps({"proposals": [_meds_proposal(time="23:30")]})  # the day ends at 20:00
    fixed = json.dumps({"proposals": [_meds_proposal(time="08:00")]})
    return Setup(_owner(pet), pet, ScriptedModel(NEEDS_FOR_MEDS, impossible, fixed))


def impossible_required_checks(run: Run) -> list[Check]:
    problems = _self_check_problems(run)
    first = problems[0] if problems else []
    final = _accepted(run)
    placed = run.result.report.placements.get("Give prescribed medication") if run.result.report else None
    return [
        Check("the schedule check flagged the 23:30 task as an error",
              any(i[0] == "error" and i[1] == "NEWLY_SKIPPED" for i in first), f"{first}"),
        Check("the problem was fed back for a repair", "repair" in _steps(run)),
        Check("the final task is required and moved inside the day",
              len(final) == 1 and final[0].required and final[0].time == "08:00", f"{[(p.title, p.time) for p in final]}"),
        Check("the final task is scheduled", placed is not None, f"placed {placed}"),
    ]


def malformed_model_replies() -> Setup:
    prose = 'Sure! Here are some tasks: {"proposals": []}'  # JSON wrapped in chatter is not accepted
    return Setup(*(lambda p: (_owner(p), p))(Pet("Biscuit", "dog", age=4)),
                 ScriptedModel(json.dumps({"needs": ["Exercise"], "query_terms": ["dog", "walk"]}), prose,
                               "```json\n{}\n```"))


def malformed_checks(run: Run) -> list[Check]:
    invalid = run.schema_total - run.schema_valid
    return [
        Check("both bad replies were rejected, not used", _steps(run).count("parse_error") == 2,
              f"{_steps(run).count('parse_error')} parse error(s)"),
        Check("fell back to the offline assistant", run.result.fell_back and run.result.mode == "offline"),
        Check("the replies counted as invalid were exactly the bad ones", invalid == 2, f"{invalid} invalid"),
        Check("the fallback still produced proposals", bool(_accepted(run))),
    ]


def invalid_pet_data() -> Setup:
    pet = Pet("Biscuit", "dog", age=4)
    pet.age = 99  # the model checks age at construction only, so a later change gets through to the assistant
    return Setup(_owner(pet), pet, ScriptedModel())  # an empty script: any model call would fail the scenario


def invalid_pet_checks(run: Run) -> list[Check]:
    return [
        Check("rejected before any model was called",
              isinstance(run.error, ai.InvalidPetData) and "llm_call" not in _steps(run)),
        Check("named the problem", isinstance(run.error, ai.InvalidPetData) and "age" in str(run.error),
              str(run.error)),
        Check("logged the rejection", _steps(run) == ["input_invalid"]),
    ]


SCENARIOS = [
    Scenario("healthy-dog", "Healthy adult dog, no tasks",
             "A four-year-old dog with no tasks at all.",
             "Suggests walks, feeding, play and grooming, all with high confidence.",
             healthy_dog, healthy_dog_checks),
    Scenario("partial-cat", "Cat with a partial routine",
             "A cat that already has a feeding task.",
             "Does not repeat feeding; suggests play and grooming from cat guides only.",
             partial_cat, partial_cat_checks),
    Scenario("senior-medication", "Senior dog on medication",
             "An 11-year-old dog whose special needs mention daily medication.",
             "Proposes a required medication task first and draws on the senior-care guide.",
             senior_dog_on_medication, senior_dog_checks),
    Scenario("bare-pet", "Bare-minimum pet (edge: nothing to go on)",
             "A pet of species 'other' with no breed, age, needs or tasks.",
             "Does not crash and does not borrow dog or cat guides; may propose nothing.",
             bare_minimum_pet, bare_minimum_checks, edge_case=True),
    Scenario("over-capacity", "Over-capacity day (edge)",
             "A dog with no tasks, on a day limited to 20 care minutes.",
             "The first check finds the day too full; repair trims the proposals to fit.",
             over_capacity_day, over_capacity_checks, edge_case=True),
    Scenario("impossible-required", "Impossible required task (edge)",
             "A model proposes a required medication task at 23:30, after the day ends at 20:00.",
             "The check flags it as an error, the repair round moves it inside the day, and it is scheduled.",
             impossible_required_task, impossible_required_checks, edge_case=True),
    Scenario("malformed-replies", "Model answers badly twice",
             "A model wraps its JSON in chatter, then in a code fence.",
             "Both replies are rejected as invalid, and the offline assistant answers instead.",
             malformed_model_replies, malformed_checks),
    Scenario("invalid-pet", "Invalid pet data (guardrail)",
             "A pet whose age was set to 99 after it was created.",
             "Rejected up front with a clear message; no model is called.",
             invalid_pet_data, invalid_pet_checks),
]


# ------------------------------------------------------------------ measuring

def schema_report(records: Sequence[dict]) -> tuple[int, int, bool | None]:
    """How many of the run's raw model replies match the JSON schema.

    Looks at every reply logged by an `llm_call` step and checks it against the schema that call was given
    (the needs schema, or the proposals schema). Returns (valid, total, whether the last reply was valid).
    A reply that was too long to be logged in full cannot be checked and is left out of the count.
    """
    validators = {"needs": jsonschema.Draft202012Validator(ai.NEEDS_SCHEMA),
                  "proposals": jsonschema.Draft202012Validator(ai.PROPOSAL_SCHEMA),
                  "repair": jsonschema.Draft202012Validator(ai.PROPOSAL_SCHEMA)}
    valid = total = 0
    last = None
    for record in records:
        if record.get("step") != "llm_call" or record.get("call") not in validators:
            continue
        raw = record.get("raw_output")
        if raw is None or "more characters]" in raw:
            continue
        try:
            ok = validators[record["call"]].is_valid(json.loads(raw))
        except json.JSONDecodeError:
            ok = False
        total += 1
        valid += ok
        last = ok
    return valid, total, last


def general_checks(run: Run) -> list[Check]:
    """The checks every scenario that produced a result gets."""
    result = run.result
    checks = [Check("the last model reply matched the schema", run.last_reply_valid is True,
                    f"{run.schema_valid}/{run.schema_total} replies valid")]
    retrieved = {c.id for c in result.retrieved}
    checks.append(Check("every proposal cites a guide that was retrieved",
                        all(p.source_ids and set(p.source_ids) <= retrieved for p in result.accepted)))
    species = run.records[0].get("species") if run.records else None
    wrong = sorted({s for p in result.accepted for s in p.source_ids
                    if species in ("dog", "cat") and s.split("-")[0] in ("dog", "cat") and not s.startswith(species)})
    checks.append(Check("no other species' guide is cited", not wrong, f"cited {wrong}" if wrong else ""))
    checks.append(Check("confidence scores are between 0 and 100%",
                        all(0.0 <= p.confidence <= 1.0 for p in result.accepted)))
    final = result.report
    errors = [i for i in (final.issues if final else ()) if i.severity == "error"]
    checks.append(Check("no schedule error is left unresolved", not errors, "; ".join(i.message for i in errors)))
    checks.append(Check("the live owner was not changed", run.owner_untouched))
    return checks


def run_scenario(scenario: Scenario, base_config: ai.AssistantConfig, trace_dir: Path) -> Run:
    """Run one scenario with its own trace file and apply its checks."""
    setup = scenario.setup()
    config = replace(base_config, trace_path=trace_dir / f"{scenario.key}.jsonl", **setup.config_changes)
    before = setup.owner.to_dict()
    result, error = None, None
    try:
        result = ai.propose_care_plan(setup.owner, setup.pet, PLAN_DATE, config=config, client=setup.client)
    except ai.InvalidPetData as err:
        error = err
    records = ai.read_trace(config.trace_path)
    valid, total, last = schema_report(records)
    run = Run(scenario.key, result, error, records, setup.owner.to_dict() == before, valid, total, last)
    try:
        if result is not None:
            run.checks += general_checks(run)
        run.checks += scenario.checks(run)
    except Exception as err:  # a scenario that cannot be judged counts as a failure, not a crash
        run.checks.append(Check("scenario checks ran", False, f"{type(err).__name__}: {err}"))
    return run


# ------------------------------------------------------------------ reporting

def summary_rows(scenarios: Sequence[Scenario], runs: Sequence[Run]) -> list[dict]:
    rows = []
    for scenario, run in zip(scenarios, runs):
        confidences = [p.confidence for p in _accepted(run)]
        failed = [c.name for c in run.checks if not c.passed]
        if run.schema_total:
            schema = f"{PASS_ICON if run.last_reply_valid else FAIL_ICON} {run.schema_valid}/{run.schema_total}"
        else:
            schema = "-"
        rows.append({
            "#": len(rows) + 1,
            "Scenario": scenario.title,
            "Result": f"{PASS_ICON} PASS" if run.passed else f"{FAIL_ICON} FAIL",
            "Props": len(confidences) if run.result else "-",
            "Rejected": len(run.result.rejected) if run.result else "-",
            "Schema": schema,
            "Confidence": (f"{min(confidences):.0%} / {sum(confidences) / len(confidences):.0%}"
                           if confidences else "-"),
            "Checks": f"{sum(c.passed for c in run.checks)}/{len(run.checks)}",
            "Notes": "; ".join(failed),
        })
    return rows


def totals_line(runs: Sequence[Run]) -> str:
    passed = sum(run.passed for run in runs)
    return f"{passed}/{len(runs)} scenarios passed"


def render_summary(scenarios: Sequence[Scenario], runs: Sequence[Run]) -> str:
    table = display.render_table(summary_rows(scenarios, runs), wrap_column="Notes", wrap_width=40)
    legend = ("Schema: ✅/❌ for the last model reply, then valid/total replies (needs, proposals and repairs). "
              "Confidence: lowest / average of the accepted proposals.")
    return f"{table}\n{legend}\n{totals_line(runs)}"


def render_log(scenarios: Sequence[Scenario], runs: Sequence[Run], config: ai.AssistantConfig) -> str:
    """The full text log: header, summary table, then every scenario in detail with its step log."""
    mode = f"offline ({config.offline_reason})" if config.offline else f"Gemini ({config.model})"
    rule = "=" * 78
    lines = [
        "PawPal+ Care Assistant evaluation",
        f"Run at: {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC    Plan date: {PLAN_DATE}    Mode: {mode}",
        rule, render_summary(scenarios, runs), rule,
    ]
    for number, (scenario, run) in enumerate(zip(scenarios, runs), 1):
        edge = " [edge case]" if scenario.edge_case else ""
        lines += ["", f"{number}. {scenario.title}{edge}: {PASS_ICON + ' PASS' if run.passed else FAIL_ICON + ' FAIL'}",
                  f"   Scenario:  {scenario.description}", f"   Expected:  {scenario.expectation}", "   Checks:"]
        for check in run.checks:
            detail = f"  ({check.detail})" if check.detail else ""
            lines.append(f"     {PASS_ICON if check.passed else FAIL_ICON} {check.name}{detail}")
        if run.error is not None:
            lines.append(f"   Stopped with {type(run.error).__name__}: {run.error}")
        if run.result and run.result.accepted:
            lines += ["   Proposals:"] + ["     " + row for row in display.render_table(
                display.proposal_rows(run.result.accepted, run.result.retrieved, run.result.report),
                wrap_column="Sources", wrap_width=34).splitlines()]
        if run.result and run.result.rejected:
            lines += ["   Left out:"] + [f"     - {r.reason}" for r in run.result.rejected]
        lines += ["   Step log:"] + [f"     {line}" for line in ai.format_trace(run.records, width=110).splitlines()]
    lines += ["", rule, totals_line(runs)]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ main

def evaluate(scenarios: Sequence[Scenario], config: ai.AssistantConfig, trace_dir: Path) -> list[Run]:
    return [run_scenario(scenario, config, trace_dir) for scenario in scenarios]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evaluate.py", description="Evaluate the Care Assistant on set scenarios.")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG, help=f"where to write the text log (default {DEFAULT_LOG.name})")
    parser.add_argument("--online", action="store_true", help="use the configured Gemini model where a key is set")
    args = parser.parse_args(argv)

    if args.online:
        ai.load_env_file()  # the Gemini key from the git-ignored .env, if there is one
        config = ai.AssistantConfig.from_env()
        if config.offline:
            print(f"Cannot run online ({config.offline_reason}); running offline instead.")
    else:
        config = ai.AssistantConfig(offline=True, api_key=None, model="offline", offline_reason="evaluation default")
    with tempfile.TemporaryDirectory() as folder:
        runs = evaluate(SCENARIOS, config, Path(folder))
    print(render_summary(SCENARIOS, runs))
    args.log.write_text(render_log(SCENARIOS, runs, config), encoding="utf-8")
    print(f"Log written to {args.log}")
    return 0 if all(run.passed for run in runs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
